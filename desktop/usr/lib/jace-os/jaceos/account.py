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
    os.system("pkill -RTMIN+9 waybar >/dev/null 2>&1")


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


def sign_in(open_url=webbrowser.open, timeout: float = 300) -> dict:
    """Runs the whole browser sign-in and returns the saved account. Blocks until done."""
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
    _save(account)
    _fetch_avatar(user.get("picture"))
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
    _refresh_bar()
    return acct


def avatar_path() -> Path | None:
    p = data_dir() / "avatar"
    return p if p.exists() else None


def _fetch_avatar(url: str | None):
    if not url:
        (data_dir() / "avatar").unlink(missing_ok=True)
        return
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read(5 * 1024 * 1024)
        (data_dir() / "avatar").write_bytes(data)
    except (urllib.error.URLError, OSError, ValueError):
        pass


def display_name(acct: dict | None) -> str:
    if not acct:
        return ""
    u = acct.get("user", {})
    return u.get("name") or u.get("username") or "Jace"


def waybar() -> str:
    acct = load()
    if not acct:
        return json.dumps({"text": "󰀄  Sign in", "tooltip": "Sign in with your Jace account", "class": "signed-out"})
    u = acct.get("user", {})
    tip = f"{display_name(acct)} (@{u.get('username', '')})\nJace account: click to manage"
    return json.dumps({"text": f"󰀄  {display_name(acct)}", "tooltip": tip, "class": "signed-in"})


def main():
    args = sys.argv[1:]
    if "--waybar" in args:
        print(waybar())
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
