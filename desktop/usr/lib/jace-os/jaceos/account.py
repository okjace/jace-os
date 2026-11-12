"""Your Jace account on Jace OS ("Sign in with Jace").

Signing in opens Jace in the browser. Jace sends you back to a tiny server on
this computer (http://127.0.0.1:<port>/callback) with a code, which is swapped
for an access token. It's OAuth 2.0 with PKCE, so Jace OS has no secret to
leak. The token and your profile are kept in ~/.local/share/jace-os/account.json,
readable only by you.

  jace-account            open Settings on the Jace account page
  jace-account --waybar   print the top bar's account button (JSON)
  jace-account --signin   sign in from a terminal
  jace-account --signout
  jace-account --avatar   print the round picture the top bar shows
  jace-account --json     who's signed in, for Jace apps (no token): {"signed_in", "user"}
  jace-account --token-for <client_id> [--scope "profile email"]
                          a Jace token for another Jace app, so it can sign you in without
                          asking (Jace exchanges this computer's token; official apps only)
"""
import base64
import configparser
import hashlib
import html
import json
import os
import secrets
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


class AccountError(Exception):
    pass


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
        "scope": s.get("scope", "profile email"),
    }


def _file() -> Path:
    return data_dir() / "account.json"


def load() -> dict | None:
    try:
        data = json.loads(_file().read_text())
    except (OSError, ValueError):
        return None
    if data.get("expires_at", 0) and data["expires_at"] < time.time():
        return None
    return data


def _save(data: dict):
    f = _file()
    tmp = f.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(data, out, indent=2)
    tmp.replace(f)


def sign_out():
    _file().unlink(missing_ok=True)
    (data_dir() / "avatar").unlink(missing_ok=True)
    _refresh_bar()


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
        try:
            err = json.loads(e.read().decode())
            msg = err.get("error_description") or err.get("error") or str(e)
        except ValueError:
            msg = str(e)
        raise AccountError(msg) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise AccountError("Couldn't reach Jace. Check your internet connection.") from e


def sign_in(open_url=webbrowser.open, timeout: float = 300, save: bool = True, cancelled=None) -> dict:
    """Runs the whole browser sign-in and returns the saved account. Blocks until done.

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
            else:
                result["code"] = q.get("code", [""])[0]
            ok = "code" in result
            page = (f"<!doctype html><meta charset=utf-8><title>Jace OS</title>"
                    f"<body style='font-family:sans-serif;background:#111317;color:#e6e8eb;display:grid;"
                    f"place-items:center;height:100vh;margin:0'><div style='text-align:center'>"
                    f"<h1 style='color:#3ddc84'>{'Signed in' if ok else 'Sign-in failed'}</h1>"
                    f"<p>{'You can close this tab and go back to Jace OS.' if ok else html.escape(result.get('error', ''))}"
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

    url = f"{cfg['url']}/oauth/authorize?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": redirect,
        "scope": cfg["scope"],
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    open_url(url)

    deadline = time.time() + timeout
    with server:
        while not result and time.time() < deadline:
            if cancelled and cancelled():
                raise AccountError("Sign-in cancelled.")
            server.handle_request()
    if "error" in result:
        raise AccountError(result["error"])
    if "code" not in result:
        raise AccountError("Sign-in timed out.")

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
    account = {
        "access_token": access,
        "expires_at": time.time() + float(token.get("expires_in", 0)) if token.get("expires_in") else 0,
        "user": user,
        "signed_in_at": time.time(),
    }
    if not save:
        account["avatar"] = _download(user.get("picture"))
        return account
    _save(account)
    _fetch_avatar(user.get("picture"))
    _sync_profile(user)
    _refresh_bar()
    return account


def refresh_profile() -> dict | None:
    """Re-reads your profile from Jace (name, picture). Signs you out if the token was revoked."""
    acct = load()
    if not acct:
        return None
    try:
        user = _request(f"{settings()['url']}/api/oauth/userinfo", token=acct["access_token"])
    except AccountError as e:
        if "invalid_token" in str(e) or "expired" in str(e):
            sign_out()
            return None
        return acct
    acct["user"] = user
    _save(acct)
    _fetch_avatar(user.get("picture"))
    _sync_profile(user)
    _refresh_bar()
    return acct


def avatar_path() -> Path | None:
    p = data_dir() / "avatar"
    return p if p.exists() else None


def _download(url: str | None) -> bytes:
    """Your Jace picture (at most 5 MB), or b"" if there's none or it can't be fetched."""
    if not url:
        return b""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read(5 * 1024 * 1024)
    except (urllib.error.URLError, OSError, ValueError):
        return b""


def _fetch_avatar(url: str | None):
    if not url:
        (data_dir() / "avatar").unlink(missing_ok=True)
        return
    data = _download(url)
    if data:
        (data_dir() / "avatar").write_bytes(data)


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


def exchange_token(client_id: str, scope: str = "profile email") -> dict:
    """Asks Jace for a token for another Jace app, in exchange for this computer's token
    (OAuth 2.0 Token Exchange, RFC 8693). Lets Jace apps sign you in without asking."""
    acct = load()
    if not acct:
        raise AccountError("Not signed in to Jace on this computer.")
    cfg = settings()
    return _request(f"{cfg['url']}/api/oauth/token", {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": acct["access_token"],
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "client_id": cfg["client_id"],
        "audience": client_id,
        "scope": scope,
    })


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
    tip = f"{display_name(acct)} (@{u.get('username', '')})\nJace account: click to manage"
    return json.dumps({"text": display_name(acct), "tooltip": tip, "class": "signed-in"})


def main():
    args = sys.argv[1:]
    if "--waybar" in args:
        print(waybar())
    elif "--avatar" in args:
        print(corner_picture())
    elif "--json" in args:
        acct = load()
        print(json.dumps({"signed_in": bool(acct), "user": (acct or {}).get("user")}))
    elif "--token-for" in args:
        i = args.index("--token-for")
        scope = args[args.index("--scope") + 1] if "--scope" in args else "profile email"
        try:
            print(json.dumps(exchange_token(args[i + 1], scope)))
        except (AccountError, IndexError) as e:
            print(json.dumps({"error": str(e) or "usage: --token-for <client_id>"}))
            sys.exit(1)
    elif "--signout" in args:
        sign_out()
        print("Signed out.")
    elif "--signin" in args:
        try:
            acct = sign_in()
            print(f"Signed in as {display_name(acct)}.")
        except AccountError as e:
            print(f"Couldn't sign in: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        os.execvp("jace-settings", ["jace-settings", "--page", "account"])
