"""Cloud sync: keeps the things you choose the same on all your Jace OS computers, through
your Jace account.

  Look        dark or light, accent colour, icons, pointer, text size, clock
  Wallpaper   your wallpaper (the picture itself if it's your own)
  Desktop     top bar, start menu, keyboard shortcuts, windows, keyboard and mouse,
              power and away times, night light, clipboard, storage clean-up
  Apps        which Flatpak and AUR apps you have (others offer to install them)

Each of them is a collection in Jace's sync storage (/api/oauth/sync/os-*), encrypted on this
computer first (AES-256-GCM, with a key made from your sync passphrase by scrypt), so Jace
only ever stores scrambled data. The "os-keys" collection holds the salt and a check value, so
your other computers can tell whether the passphrase they're given is right.

Every sync compares what's here with what was last synced and with the newest version in
Jace: changes here go up, changes from your other computers come down, and if both changed
the newest one wins. A computer that's never synced takes what's in Jace (like setting up a
new phone from a backup).

  jace-sync            sync now
  jace-sync --daemon   sync at sign-in, every 15 minutes, and soon after you change something
"""
import base64
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from jaceos import account, config_dir, data_dir, run, which

KEYS = "os-keys"
CHECK = b"jace-os-sync-v1"
CATEGORIES = {
    # id: (collection, title, what it is)
    "look": ("os-look", "Look", "Dark or light, accent colour, icons, pointer, text size and clock"),
    "wallpaper": ("os-wallpaper", "Wallpaper", "Your wallpaper, including your own pictures"),
    "desktop": ("os-desktop", "Desktop", "Top bar, start menu, keyboard shortcuts, windows, keyboard and mouse, "
                                         "power, night light and clipboard"),
    "apps": ("os-apps", "Apps", "Which Flatpak and AUR apps you have, so your other computers can add them"),
}
INTERFACE_KEYS = ["color-scheme", "gtk-theme", "accent-color", "icon-theme", "cursor-theme", "cursor-size",
                  "text-scaling-factor", "clock-format"]
# Jace OS settings files that belong in "Desktop" (not "displays": screens differ per computer).
DESKTOP_FILES = ["bar.json", "menu.json", "shortcuts.json", "idle", "nightlight", "clipboard-off",
                 "clipboard-no-autopaste", "storage.json", "terminal", "no-jace-profile-sync"]
LABWC_FILES = ["rc.xml", "environment"]
MAX_WALLPAPER = 5 * 1024 * 1024        # Jace allows 8 MB per collection, after encryption and base64
INTERVAL = 15 * 60


class SyncError(Exception):
    pass


# --- where things live ------------------------------------------------------------------------------

def _state_file() -> Path:
    return config_dir() / "sync.json"


def _key_file() -> Path:
    return data_dir() / "sync-key"


def labwc_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "labwc"


def load_state() -> dict:
    state = {"enabled": False, "categories": {c: True for c in CATEGORIES}, "versions": {}, "hashes": {},
             "last_sync": 0, "last_error": "", "remote_apps": []}
    try:
        state.update(json.loads(_state_file().read_text()))
    except (OSError, ValueError):
        pass
    return state


def save_state(state: dict):
    tmp = _state_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(_state_file())


def has_key() -> bool:
    return _key_file().is_file()


def _key() -> bytes:
    try:
        return _key_file().read_bytes()
    except OSError as e:
        raise SyncError("Type your sync passphrase in Settings → Jace account first.") from e


def _save_key(key: bytes):
    fd = os.open(_key_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(key)


# --- encryption -------------------------------------------------------------------------------------

def _derive(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def encrypt(key: bytes, data: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, data, None)).decode()


def decrypt(key: bytes, payload: str) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = base64.b64decode(payload)
    try:
        return AESGCM(key).decrypt(raw[:12], raw[12:], None)
    except InvalidTag as e:
        raise SyncError("This doesn't match your sync passphrase.") from e


# --- talking to Jace --------------------------------------------------------------------------------

def _token() -> str:
    acct = account.load()
    if not acct:
        raise SyncError("Sign in with Jace to use sync.")
    return acct["access_token"]


def _call(method: str, path: str, body: dict | None = None, if_match: int | None = None):
    """(status, json) for /api/oauth/sync<path>."""
    url = account.settings()["url"] + "/api/oauth/sync" + path
    headers = {"Authorization": f"Bearer {_token()}", "Accept": "application/json",
               "User-Agent": account.USER_AGENT}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if if_match is not None:
        headers["If-Match"] = f'"{if_match}"'
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            info = json.loads(e.read() or b"{}")
        except ValueError:
            info = {}
        if e.code == 403 and info.get("error") == "insufficient_scope":
            raise SyncError("Sign in with Jace again to turn on sync (Settings → Jace account).") from e
        if e.code == 401:
            raise SyncError("Your Jace sign-in ran out. Sign in again in Settings → Jace account.") from e
        return e.code, info
    except (urllib.error.URLError, OSError) as e:
        raise SyncError("Couldn't reach Jace. Check your internet connection.") from e


def remote_versions() -> dict:
    status, info = _call("GET", "")
    if status != 200:
        raise SyncError(info.get("error_description") or "Jace's sync isn't available right now.")
    known = set(info.get("collections", []))
    if not all(c in known for c, *_ in CATEGORIES.values()):
        raise SyncError("Jace doesn't store Jace OS sync data yet.")
    return {r["collection"]: r for r in info.get("records", [])}


def _get(collection: str) -> dict | None:
    status, info = _call("GET", "/" + collection)
    return info if status == 200 else None


def _put(collection: str, payload: str, version: int) -> int | None:
    """The new version, or None if someone else wrote first (412)."""
    status, info = _call("PUT", "/" + collection, {"payload": payload, "device": socket.gethostname()}, version)
    if status == 412:
        return None
    if status not in (200, 201):
        raise SyncError(info.get("error_description") or f"Jace didn't save it ({status}).")
    return int(info.get("version", version + 1))


# --- the passphrase ---------------------------------------------------------------------------------

def passphrase_exists() -> bool:
    """Has one of your computers set a sync passphrase already?"""
    return _get(KEYS) is not None


def set_passphrase(passphrase: str):
    """Use this passphrase: checks it against the one your other computers use, or (the first
    time) makes it the one. Keeps the key on this computer, readable only by you."""
    if len(passphrase) < 8:
        raise SyncError("Use at least 8 characters.")
    existing = _get(KEYS)
    if existing:
        info = json.loads(base64.b64decode(existing["payload"]))
        key = _derive(passphrase, base64.b64decode(info["salt"]))
        if decrypt(key, info["check"]) != CHECK:
            raise SyncError("This doesn't match your sync passphrase.")
    else:
        salt = os.urandom(16)
        key = _derive(passphrase, salt)
        keys = {"salt": base64.b64encode(salt).decode(), "check": encrypt(key, CHECK), "kdf": "scrypt-32768-8-1"}
        if _put(KEYS, base64.b64encode(json.dumps(keys).encode()).decode(), 0) is None:
            return set_passphrase(passphrase)        # another computer just set one: check against it
    _save_key(key)


def forget(everywhere: bool = False):
    """Turns sync off here. everywhere=True also deletes Jace OS's sync data in Jace (not Jace
    Browser's), for starting over with a new passphrase."""
    state = load_state()
    state.update(enabled=False, versions={}, hashes={})
    save_state(state)
    _key_file().unlink(missing_ok=True)
    if everywhere:
        for c in [KEYS] + [col for col, *_ in CATEGORIES.values()]:
            _call("DELETE", "/" + c)


# --- what each category is, here --------------------------------------------------------------------

def _gsettings(key: str) -> str:
    return run(["gsettings", "get", "org.gnome.desktop.interface", key]).strip()


def collect(category: str) -> dict:
    if category == "look":
        return {"interface": {k: v for k in INTERFACE_KEYS if (v := _gsettings(k))}}
    if category == "wallpaper":
        path = Path(run(["jace-wallpaper", "path"]).strip() or "/usr/share/backgrounds/jace-os/default.png")
        if str(path).startswith("/usr/share/") or not path.is_file():
            return {"builtin": str(path)}
        data = path.read_bytes()
        if len(data) > MAX_WALLPAPER:
            return {"builtin": "/usr/share/backgrounds/jace-os/default.png", "too_big": path.name}
        return {"name": path.name, "image": base64.b64encode(data).decode()}
    if category == "desktop":
        files = {}
        for name in DESKTOP_FILES:
            f = config_dir() / name
            if f.is_file():
                files["jace-os/" + name] = f.read_text(errors="replace")
        for name in LABWC_FILES:
            f = labwc_dir() / name
            if f.is_file():
                files["labwc/" + name] = f.read_text(errors="replace")
        return {"files": files}
    if category == "apps":
        flatpaks = sorted(set(run(["flatpak", "list", "--app", "--columns=application"]).split())) \
            if which("flatpak") else []
        # Apps from the AUR (installed but not from a repository); the Jace OS repo's apps aren't.
        aur = sorted(set(run(["pacman", "-Qqm"]).split()) - {"jace-os"})
        return {"flatpak": flatpaks, "aur": [a for a in aur if not a.endswith("-debug")]}
    raise ValueError(category)


def shared(category: str, state: dict) -> dict:
    """What this computer adds to the category. For apps, that's every app on any of your
    computers (so each one's apps reach the others); everything else is just this computer's."""
    data = collect(category)
    if category == "apps":
        remote = state.get("remote_apps") or {}
        data = {k: sorted(set(data[k]) | set(remote.get(k, []))) for k in ("flatpak", "aur")}
    return data


def _hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def apply(category: str, data: dict, state: dict):
    """Makes this computer match what came from your other computers."""
    if category == "look":
        for k, v in data.get("interface", {}).items():
            if k in INTERFACE_KEYS:
                subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", k, v], capture_output=True)
    elif category == "wallpaper":
        if data.get("image"):
            folder = data_dir() / "wallpapers"
            folder.mkdir(exist_ok=True)
            target = folder / ("synced-" + Path(data.get("name", "wallpaper.png")).name)
            target.write_bytes(base64.b64decode(data["image"]))
            subprocess.run(["jace-wallpaper", "set", str(target)], capture_output=True)
        elif data.get("builtin") and Path(data["builtin"]).is_file():
            subprocess.run(["jace-wallpaper", "set", data["builtin"]], capture_output=True)
    elif category == "desktop":
        for rel, text in data.get("files", {}).items():
            folder, _, name = rel.partition("/")
            if folder == "jace-os" and name in DESKTOP_FILES:
                target = config_dir() / name
            elif folder == "labwc" and name in LABWC_FILES:
                labwc_dir().mkdir(parents=True, exist_ok=True)
                target = labwc_dir() / name
            else:
                continue
            target.write_text(text)
        # Show the changes: the window manager, top bar, start menu and away times re-read them.
        subprocess.run(["labwc", "--reconfigure"], capture_output=True)
        subprocess.run(["jace-bar", "--reload"], capture_output=True)
        subprocess.run(["pkill", "-x", "swayidle"], capture_output=True)
        subprocess.Popen(["jace-idle"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif category == "apps":
        # Never installs by itself: Settings offers the ones that are missing here.
        state["remote_apps"] = data


def missing_apps(state: dict | None = None) -> dict:
    """Apps your other computers have that this one doesn't."""
    state = state or load_state()
    remote = state.get("remote_apps") or {}
    here = collect("apps")
    return {"flatpak": [a for a in remote.get("flatpak", []) if a not in here["flatpak"]],
            "aur": [a for a in remote.get("aur", []) if a not in here["aur"]]}


# --- syncing ----------------------------------------------------------------------------------------

def sync(force: bool = False) -> list[str]:
    """Syncs every category that's turned on. Returns what happened, one line each."""
    state = load_state()
    if not state["enabled"] and not force:
        return []
    key = _key()
    first_time = not state["versions"]
    remote = remote_versions()
    log = []
    for cat, (collection, title, _about) in CATEGORIES.items():
        if not state["categories"].get(cat, True):
            continue
        try:
            log.append(f"{title}: " + _sync_one(cat, collection, key, remote.get(collection), state, first_time))
        except SyncError as e:
            log.append(f"{title}: {e}")
    state["last_sync"] = time.time()
    state["last_error"] = ""
    save_state(state)
    return log


def _sync_one(cat, collection, key, remote_meta, state, first_time) -> str:
    local = shared(cat, state)
    local_hash = _hash(local)
    known = state["versions"].get(collection, 0)
    remote_version = remote_meta["version"] if remote_meta else 0
    changed_here = local_hash != state["hashes"].get(collection)

    if remote_version > known:
        record = _get(collection)
        if record:
            incoming = json.loads(decrypt(key, record["payload"]))
            # Both changed: the newest change wins. A computer syncing for the first time takes
            # what's in Jace.
            if changed_here and not first_time and state.get("changed_at", {}).get(collection, 0) > \
                    incoming.get("updated_at", 0):
                return _upload(collection, key, local, local_hash, record["version"], state, cat)
            apply(cat, incoming["data"], state)
            state["versions"][collection] = record["version"]
            state["hashes"][collection] = _hash(shared(cat, state))
            return f"updated from {record.get('updated_by') or 'another computer'}"
    if changed_here:
        return _upload(collection, key, local, local_hash, known, state, cat)
    return "up to date"


def _upload(collection, key, local, local_hash, version, state, cat) -> str:
    body = {"updated_at": time.time(), "device": socket.gethostname(), "data": local}
    payload = encrypt(key, json.dumps(body).encode())
    new_version = _put(collection, payload, version)
    if new_version is None:            # another computer wrote first: get theirs and try again
        state["versions"][collection] = 0
        return _sync_one(cat, collection, key, {"version": version + 1}, state, False)
    state["versions"][collection] = new_version
    state["hashes"][collection] = local_hash
    return "saved to your Jace account"


def note_changes(state: dict) -> bool:
    """Remembers when each category last changed here (to pick the newest side in a clash).
    True if something changed since the last sync."""
    changed = False
    stamps = state.setdefault("changed_at", {})
    for cat, (collection, *_rest) in CATEGORIES.items():
        if not state["categories"].get(cat, True):
            continue
        h = _hash(shared(cat, state))
        if h != state["hashes"].get(collection):
            if state.get("pending", {}).get(collection) != h:
                stamps[collection] = time.time()
                state.setdefault("pending", {})[collection] = h
            changed = True
    return changed


def daemon():
    """At sign-in, every 15 minutes, and a minute after you change something."""
    last = 0.0
    while True:
        state = load_state()
        if state["enabled"] and has_key():
            changed = note_changes(state)
            save_state(state)
            if changed or time.time() - last > INTERVAL:
                try:
                    sync()
                except SyncError as e:
                    state = load_state()
                    state["last_error"] = str(e)
                    save_state(state)
                last = time.time()
        time.sleep(60)


def main():
    if "--daemon" in sys.argv:
        daemon()
        return
    try:
        for line in sync(force="--force" in sys.argv):
            print(line)
    except SyncError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
