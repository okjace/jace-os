"""Your Jace accounts on Jace OS ("Sign in with Jace").

Signing in shows Jace's sign-in page in Jace OS's own window (jaceos/signin.py; a browser if
that can't open). Jace sends it back to a tiny server on this computer
(http://127.0.0.1:<port>/callback) with a code, which is swapped for an access token. It's
OAuth 2.0 with PKCE, so Jace OS has no secret to leak.

Jace OS is one of Jace's own apps, so it signs you in by itself: if you're signed in to Jace in
that window already, a sign-in with prompt=none comes straight back with a code. If Jace says
login_required or consent_required, that's remembered (no loops) and you get the "Sign in with
Jace" button instead; and it never happens right after you signed out on purpose.

Every Jace account that signs in on this computer is remembered (account switcher): its id,
username, name and picture in ~/.local/share/jace-os/accounts.json, and its token in your
keyring (encrypted, GNOME Keyring), or, if the keyring is locked or missing, in a file only you
can read. One account is the current one: the top bar, your name and picture, and cloud sync
follow it.

  jace-account            open Settings on the Jace account page
  jace-account --waybar   print the top bar's account button (JSON)
  jace-account --signin   sign in (Jace OS's sign-in window, jace-signin)
  jace-account --silent   sign in by itself if possible (at login; see above)
  jace-account --signout  sign out of the current account
  jace-account --avatar   print the round picture the top bar shows
  jace-account --json     who's signed in, for Jace apps (no token): {"signed_in", "user", "accounts"}
"""
import base64
import configparser
import hashlib
import html
import json
import os
import secrets
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from jaceos import JACE_URL, ROOT, data_dir

CONF = ROOT / "etc/jace-os/account.conf"
USER_AGENT = "JaceOS-Account/1.0"
SILENT_EVERY = 12 * 3600          # a silent sign-in is tried at most this often (seconds)


class AccountError(Exception):
    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code          # Jace's error code: login_required, consent_required, account_banned, …


def settings() -> dict:
    cp = configparser.ConfigParser()
    cp.read([CONF, Path.home() / ".config/jace-os/account.conf"])
    s = cp["jace"] if cp.has_section("jace") else {}
    return {
        "url": (s.get("url") or JACE_URL).rstrip("/"),
        "client_id": s.get("client_id", "").strip(),
        # Only for testing against your own Jace while it doesn't support PKCE yet. Never ship one.
        "client_secret": s.get("client_secret", "").strip(),
        "port": int(s.get("redirect_port", "47101")),
        # "sync" (cloud sync) is always asked for, even with an older account.conf.
        "scope": " ".join(dict.fromkeys(s.get("scope", "profile email").split() + ["sync"])),
    }


# --- where things are kept ----------------------------------------------------------------------------

def _list_file() -> Path:
    return data_dir() / "accounts.json"


def _avatars() -> Path:
    d = data_dir() / "avatars"
    d.mkdir(exist_ok=True)
    return d


def _write_private(path: Path, data: dict):
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(data, out, indent=2)
    tmp.replace(path)


def _load_list() -> dict:
    _migrate()
    try:
        data = json.loads(_list_file().read_text())
    except (OSError, ValueError):
        data = {}
    data.setdefault("current", None)
    data.setdefault("accounts", {})          # sub: {"user", "expires_at", "signed_in_at"}
    data.setdefault("order", [])             # most recently used first
    return data


def _save_list(data: dict):
    _write_private(_list_file(), data)


def sub_of(user: dict) -> str:
    return str(user.get("sub") or user.get("id") or user.get("username") or "")


# Tokens: in the keyring (Secret Service) when it's unlocked, so nothing ever asks for a keyring
# password; otherwise in a file only you can read.

_SECRET = []


def _secret():
    if not _SECRET:
        _SECRET.append(_open_secret())
    return _SECRET[0]


def _keyring_running() -> bool:
    """Is a keyring (Secret Service) running? Asked first so nothing waits on starting one."""
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                              "NameHasOwner", GLib.Variant("(s)", ("org.freedesktop.secrets",)),
                              None, Gio.DBusCallFlags.NONE, 2000, None)
        return bool(reply.unpack()[0])
    except Exception:  # noqa: BLE001
        return False


def _open_secret():
    if not _keyring_running():
        return None
    try:
        import gi
        gi.require_version("Secret", "1")
        from gi.repository import Secret
        service = Secret.Service.get_sync(Secret.ServiceFlags.LOAD_COLLECTIONS, None)
        default = Secret.Collection.for_alias_sync(service, "default", Secret.CollectionFlags.NONE, None)
        if default is None or default.get_locked():
            return None
        schema = Secret.Schema.new("org.jaceos.Account", Secret.SchemaFlags.NONE,
                                   {"sub": Secret.SchemaAttributeType.STRING})
        return Secret, schema
    except Exception:  # noqa: BLE001 - no keyring: the private file instead
        return None


def _token_file() -> Path:
    return data_dir() / "account-tokens.json"


def _store_token(sub: str, token: str):
    s = _secret()
    if s:
        Secret, schema = s
        if Secret.password_store_sync(schema, {"sub": sub}, Secret.COLLECTION_DEFAULT,
                                      "Jace account (Jace OS)", token, None):
            _forget_file_token(sub)
            return
    try:
        tokens = json.loads(_token_file().read_text())
    except (OSError, ValueError):
        tokens = {}
    tokens[sub] = token
    _write_private(_token_file(), tokens)


def _token(sub: str) -> str | None:
    s = _secret()
    if s:
        Secret, schema = s
        try:
            token = Secret.password_lookup_sync(schema, {"sub": sub}, None)
        except Exception:  # noqa: BLE001
            token = None
        if token:
            return token
    try:
        return json.loads(_token_file().read_text()).get(sub)
    except (OSError, ValueError):
        return None


def _forget_file_token(sub: str):
    try:
        tokens = json.loads(_token_file().read_text())
    except (OSError, ValueError):
        return
    if tokens.pop(sub, None) is not None:
        if tokens:
            _write_private(_token_file(), tokens)
        else:
            _token_file().unlink(missing_ok=True)


def _delete_token(sub: str):
    s = _secret()
    if s:
        Secret, schema = s
        try:
            Secret.password_clear_sync(schema, {"sub": sub}, None)
        except Exception:  # noqa: BLE001
            pass
    _forget_file_token(sub)


def _migrate():
    """Older Jace OS (and Setup) kept one account, token included, in account.json."""
    old = data_dir() / "account.json"
    if not old.exists() or _list_file().exists():
        return
    try:
        acct = json.loads(old.read_text())
        sub = sub_of(acct["user"])
    except (OSError, ValueError, KeyError):
        old.unlink(missing_ok=True)
        return
    _store_token(sub, acct["access_token"])
    pic = data_dir() / "avatar"
    if pic.exists():
        shutil.copyfile(pic, _avatars() / sub)
    _save_list({"current": sub, "order": [sub], "accounts": {sub: {
        "user": acct["user"], "expires_at": acct.get("expires_at", 0),
        "signed_in_at": acct.get("signed_in_at", time.time())}}})
    old.unlink()                                             # the token isn't kept in plain text any more


# --- the accounts --------------------------------------------------------------------------------------

def load() -> dict | None:
    """The current account, with its token: {"sub", "user", "access_token", "expires_at", …}."""
    data = _load_list()
    sub = data["current"]
    info = data["accounts"].get(sub) if sub else None
    if not info:
        return None
    if info.get("expires_at") and info["expires_at"] < time.time():
        return None
    token = _token(sub)
    if not token:
        return None
    return dict(info, sub=sub, access_token=token)


def accounts() -> list[dict]:
    """Every Jace account on this computer (no tokens), the current one first, then most recently
    used: [{"sub", "user", "current", "expired", "picture"}]."""
    data = _load_list()
    order = [s for s in data["order"] if s in data["accounts"]]
    order += [s for s in data["accounts"] if s not in order]
    if data["current"] in order:
        order.remove(data["current"])
        order.insert(0, data["current"])
    out = []
    for sub in order:
        info = data["accounts"][sub]
        pic = _avatars() / sub
        out.append({"sub": sub, "user": info["user"], "current": sub == data["current"],
                    "expired": bool(info.get("expires_at")) and info["expires_at"] < time.time(),
                    "picture": str(pic) if pic.exists() else None})
    return out


def _add(acct: dict, avatar: bytes):
    """Remembers an account that just signed in, and makes it the current one."""
    sub = sub_of(acct["user"])
    _store_token(sub, acct["access_token"])
    if avatar:
        (_avatars() / sub).write_bytes(avatar)
    else:
        (_avatars() / sub).unlink(missing_ok=True)
    data = _load_list()
    data["accounts"][sub] = {"user": acct["user"], "expires_at": acct.get("expires_at", 0),
                             "signed_in_at": acct.get("signed_in_at", time.time())}
    data["current"] = sub
    data["order"] = [sub] + [s for s in data["order"] if s != sub]
    data.pop("signed_out", None)
    _save_list(data)
    _became_current()


def _became_current():
    """Your name and picture, the top bar and the corner picture follow the current account."""
    acct = load()
    pic = data_dir() / "avatar"
    if acct and (_avatars() / acct["sub"]).exists():
        shutil.copyfile(_avatars() / acct["sub"], pic)
    else:
        pic.unlink(missing_ok=True)
    if acct:
        _sync_profile(acct["user"])
    _refresh_bar()


def switch(sub: str) -> dict:
    """Makes another remembered account the current one, using its saved sign-in. Raises
    AccountError(code="reauth") if that account needs to sign in again (expired or revoked), and
    removes it (code="account_banned") if Jace has banned it."""
    data = _load_list()
    if sub not in data["accounts"]:
        raise AccountError("That account isn't on this computer any more.")
    token = _token(sub)
    if not token:
        raise AccountError("Sign in to this account again.", "reauth")
    try:
        user = _request(f"{settings()['url']}/api/oauth/userinfo", token=token)
    except AccountError as e:
        if e.code == "account_banned":
            remove(sub)
            raise
        if e.code in ("invalid_token", "unauthorized") or "invalid" in str(e).lower():
            raise AccountError("Sign in to this account again.", "reauth") from e
        raise
    if user.get("banned"):
        remove(sub)
        raise AccountError(f"{user.get('name') or user.get('username')}'s Jace account is banned, so it "
                           "was removed from this computer.", "account_banned")
    data = _load_list()
    data["accounts"][sub]["user"] = user
    data["current"] = sub
    data["order"] = [sub] + [s for s in data["order"] if s != sub]
    data.pop("signed_out", None)
    _save_list(data)
    pic = _download(user.get("picture"))
    if pic:
        (_avatars() / sub).write_bytes(pic)
    _became_current()
    return load()


def remove(sub: str):
    """Forgets an account on this computer (its token too). If it was the current one, the next
    account takes over, or you're signed out."""
    _delete_token(sub)
    (_avatars() / sub).unlink(missing_ok=True)
    data = _load_list()
    data["accounts"].pop(sub, None)
    data["order"] = [s for s in data["order"] if s != sub]
    if data["current"] == sub:
        data["current"] = data["order"][0] if data["order"] else None
        if data["current"] is None:
            data["signed_out"] = time.time()                  # on purpose: no signing in by itself
    _save_list(data)
    _became_current()


def sign_out():
    """Signs out of the current account (and forgets it on this computer)."""
    acct = _load_list()["current"]
    if acct:
        remove(acct)
    data = _load_list()
    data["signed_out"] = time.time()
    _save_list(data)


def sign_out_all():
    data = _load_list()
    for sub in list(data["accounts"]):
        _delete_token(sub)
        (_avatars() / sub).unlink(missing_ok=True)
    _save_list({"current": None, "accounts": {}, "order": [], "signed_out": time.time()})
    _became_current()


def _refresh_bar():
    """Your name and picture on the top bar update right away (both modules listen on signal 9)."""
    import subprocess
    subprocess.run(["pkill", "-RTMIN+9", "-x", "waybar"], capture_output=True)


def _request(url: str, data: dict | None = None, token: str | None = None) -> dict:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        code = ""
        try:
            err = json.loads(e.read().decode())
            code = err.get("error", "")
            msg = err.get("error_description") or code or str(e)
        except ValueError:
            msg = str(e)
        if e.code == 401 and not code:
            code = "invalid_token"
        raise AccountError(msg, code) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise AccountError("Couldn't reach Jace. Check your internet connection.") from e


# --- signing in ------------------------------------------------------------------------------------------

def sign_in(open_url=webbrowser.open, timeout: float = 300, save: bool = True, cancelled=None,
            prompt: str | None = None) -> dict:
    """Runs the whole sign-in and returns the account. Blocks until done.

    open_url: shows Jace's page (Jace OS's sign-in window, or a browser).
    prompt: "none" (sign in by itself or fail with code login_required / consent_required),
    "select_account" (Jace's account picker, to add another account), or None.
    save=False (Setup, before your account exists) keeps nothing on this computer and returns
    the account with its picture as "avatar" (bytes), for Setup to give to the new account.
    cancelled: a function returning True to stop waiting (someone pressed Cancel)."""
    cfg = settings()
    if not cfg["client_id"]:
        raise AccountError("Sign in with Jace isn't set up in this version of Jace OS yet.")
    redirect = f"http://127.0.0.1:{cfg['port']}/callback"
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

    result: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if urllib.parse.urlparse(self.path).path != "/callback":
                self.send_error(404)
                return
            if q.get("state", [""])[0] != state:
                result["error"] = "The sign-in didn't match. Please try again."
            elif "error" in q:
                result["error"] = q.get("error_description", q["error"])[0]
                result["code_name"] = q["error"][0]
            else:
                result["code"] = q.get("code", [""])[0]
            ok = "code" in result
            page = (f"<!doctype html><meta charset=utf-8><title>Jace OS</title>"
                    f"<body style='font-family:sans-serif;background:#111317;color:#e6e8eb;display:grid;"
                    f"place-items:center;height:100vh;margin:0'><div style='text-align:center'>"
                    f"<h1 style='color:#3ddc84'>{'Signed in' if ok else 'Not signed in'}</h1>"
                    f"<p>{'You can go back to Jace OS.' if ok else html.escape(result.get('error', ''))}"
                    f"</p></div>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page.encode())

        def log_message(self, *args):
            pass

    try:
        server = HTTPServer(("127.0.0.1", cfg["port"]), Handler)
    except OSError as e:
        raise AccountError("Another sign-in is already open. Finish or close it first.") from e
    server.timeout = 1

    params = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": redirect,
        "scope": cfg["scope"],
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if prompt:
        params["prompt"] = prompt
    open_url(f"{cfg['url']}/oauth/authorize?" + urllib.parse.urlencode(params))

    deadline = time.time() + timeout
    with server:
        while not result and time.time() < deadline:
            if cancelled and cancelled():
                raise AccountError("Sign-in cancelled.", "cancelled")
            server.handle_request()
    if "error" in result:
        raise AccountError(result["error"], result.get("code_name", ""))
    if "code" not in result:
        raise AccountError("Sign-in timed out.", "timeout")

    form = {
        "grant_type": "authorization_code",
        "code": result["code"],
        "redirect_uri": redirect,
        "client_id": cfg["client_id"],
        "code_verifier": verifier,
    }
    if cfg["client_secret"]:
        form["client_secret"] = cfg["client_secret"]
    token = _request(f"{cfg['url']}/api/oauth/token", form)
    access = token.get("access_token")
    if not access:
        raise AccountError("Jace didn't send back a sign-in token.")

    user = _request(f"{cfg['url']}/api/oauth/userinfo", token=access)
    if user.get("banned"):
        raise AccountError("This Jace account is banned.", "account_banned")
    account = {
        "access_token": access,
        "expires_at": time.time() + float(token.get("expires_in", 0)) if token.get("expires_in") else 0,
        "user": user,
        "signed_in_at": time.time(),
    }
    avatar = _download(user.get("picture"))
    if not save:
        account["avatar"] = avatar
        return account
    _add(account, avatar)
    return load()


def should_sign_in_silently() -> bool:
    """Not signed in, didn't sign out on purpose, and no silent try lately."""
    data = _load_list()
    if data["current"] and load():
        return False
    if data.get("signed_out"):
        return False
    return time.time() - data.get("silent_tried_at", 0) > SILENT_EVERY


def sign_in_silently(open_url) -> dict | None:
    """Signs in by itself if Jace OS's sign-in window is signed in to Jace already (prompt=none).
    None if Jace said login_required or consent_required (remembered, so it isn't tried again
    for a while: no loops)."""
    data = _load_list()
    data["silent_tried_at"] = time.time()
    _save_list(data)
    try:
        return sign_in(open_url=open_url, prompt="none", timeout=30)
    except AccountError as e:
        if e.code in ("login_required", "consent_required", "interaction_required", "timeout"):
            return None
        raise


def refresh_profile() -> dict | None:
    """Re-reads your profile from Jace (name, picture). Forgets the account if its sign-in was
    revoked or it was banned."""
    acct = load()
    if not acct:
        return None
    try:
        user = _request(f"{settings()['url']}/api/oauth/userinfo", token=acct["access_token"])
    except AccountError as e:
        if e.code in ("invalid_token", "account_banned") or "expired" in str(e):
            remove(acct["sub"])
            return None
        return acct
    if user.get("banned"):
        remove(acct["sub"])
        return None
    data = _load_list()
    data["accounts"][acct["sub"]]["user"] = user
    _save_list(data)
    pic = _download(user.get("picture"))
    if pic:
        (_avatars() / acct["sub"]).write_bytes(pic)
    _became_current()
    return load()


def avatar_path() -> Path | None:
    p = data_dir() / "avatar"
    return p if p.exists() else None


def _download(url: str | None) -> bytes:
    """A Jace picture (at most 5 MB), or b"" if there's none or it can't be fetched."""
    if not url:
        return b""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read(5 * 1024 * 1024)
    except (urllib.error.URLError, OSError, ValueError):
        return b""


def _sync_profile(user: dict):
    """Your Jace display name and photo become this computer's account name and picture."""
    try:
        from jaceos import profile
        profile.sync_from_jace(user, avatar_path())
    except Exception:  # noqa: BLE001 - never let a sync problem break signing in
        pass


def corner_picture() -> str:
    """The round picture for the top bar: your Jace photo, else your account picture, else initials."""
    from jaceos import profile
    acct = load()
    if acct:
        pic = avatar_path()
        return profile.round_picture(str(pic) if pic else None, display_name(acct))
    me = profile.local_user()
    return profile.round_picture(me["picture"], me["name"])


def display_name(acct: dict | None) -> str:
    if not acct:
        return ""
    u = acct.get("user", {})
    return u.get("name") or u.get("username") or "Jace"


def waybar() -> str:
    acct = load()
    if not acct:
        # Not signed in to Jace: show your account on this computer, not "Sign in".
        from jaceos import profile
        me = profile.local_user()
        return json.dumps({"text": me["name"], "class": "local",
                           "tooltip": f"{me['name']} ({me['username']}) on this computer\n"
                                      "Click to sign in with Jace for your Jace apps"})
    u = acct.get("user", {})
    others = len(accounts()) - 1
    tip = f"{display_name(acct)} (@{u.get('username', '')})\nJace account: click to manage" + \
        (f" or switch ({others} more on this computer)" if others > 0 else "")
    return json.dumps({"text": display_name(acct), "tooltip": tip, "class": "signed-in"})


def main():
    args = sys.argv[1:]
    if "--waybar" in args:
        print(waybar())
    elif "--avatar" in args:
        print(corner_picture())
    elif "--json" in args:
        acct = load()
        print(json.dumps({"signed_in": bool(acct), "user": (acct or {}).get("user"),
                          "accounts": [a["user"] for a in accounts()]}))
    elif "--token-for" in args:
        # Jace turned token exchange off: Jace's own apps sign themselves in now (prompt=none).
        print(json.dumps({"error": "interaction_required",
                          "error_description": "Jace apps sign in by themselves now. Use Sign in with Jace."}))
        sys.exit(1)
    elif "--signout" in args:
        sign_out()
        print("Signed out.")
    elif "--signin" in args:
        os.execvp("jace-signin", ["jace-signin"])
    elif "--silent" in args:
        os.execvp("jace-signin", ["jace-signin", "--silent"])
    else:
        os.execvp("jace-settings", ["jace-settings", "--page", "account"])
