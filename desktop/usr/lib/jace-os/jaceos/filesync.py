"""File sync: keeps folders the same on all your Jace OS computers, through your Jace account
(part of cloud sync, Settings → Jace account).

  Documents & Desktop   ~/Documents and ~/Desktop
  Claude sessions       Claude Code's chats, edit history and memory, and the Claude app's
                        list of sessions, so you can carry on a session on another computer
  Project folders       folders you add (like ~/Jace OS). Every computer gets the projects any of
                        them adds. Build folders (node_modules, build, out, …) are skipped.

Files are cut into pieces of up to 8 MB, and each piece is encrypted on this computer
(AES-256-GCM, with keys made from your sync key) before it's uploaded to Jace's private file
storage (Backblaze B2, through /api/oauth/files), so neither Jace nor Backblaze can read it. A
piece's name is a keyed hash of what's in it, so the same piece is only stored once.

What's in each folder (names, sizes, permissions, symlinks and which pieces) is the encrypted
"os-files" sync collection. Each computer remembers what it last synced, so it can tell what
changed here, what changed elsewhere, and what changed in both:
  - changed in one place: that change wins
  - changed in both: the newer one wins, and the other is kept next to it as
    "name (conflict from <computer> <date>)" (Claude sessions just keep the newer one)
  - deleted elsewhere: it goes to this computer's Trash, never straight away
Permissions (like a script being executable) and symlinks are kept.
"""
import base64
import hashlib
import hmac
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

from jaceos import account, cloudsync, data_dir

CHUNK = 8 * 1024 * 1024
MANIFEST = "os-files"
HOME = Path.home()
# Build output and caches: made again when you build, and often huge.
EXCLUDES = {"node_modules", ".next", ".cache", "__pycache__", ".venv", "venv", ".gradle", "loom-cache", "build",
            "dist", "target", "out", "work", ".turbo", ".expo", ".tmp"}
GROUPS = {
    # id: (title, what it is, folders in your home, conflict rule)
    "documents": ("Documents & Desktop", "Your Documents and Desktop folders", ["Documents", "Desktop"], "copy"),
    "claude": ("Claude sessions", "Claude Code's chats, edit history and memory, and the Claude app's session "
               "list, so you can carry on where you left off", [".claude/projects", ".claude/file-history",
                                                               ".config/Claude/claude-code-sessions"], "newest"),
    "projects": ("Project folders", "Folders you add, on every computer. Build folders are skipped.", [], "copy"),
}
# Files that are never synced, even inside synced folders (they belong to one computer).
SKIP_NAMES = {".DS_Store", "Thumbs.db", ".~lock", "index.lock"}


# --- talking to Jace's file storage -----------------------------------------------------------------

def _files_call(method: str, path: str = "", body: dict | None = None):
    url = account.settings()["url"] + "/api/oauth/files" + path
    headers = {"Authorization": f"Bearer {cloudsync._token()}", "Accept": "application/json",
               "User-Agent": account.USER_AGENT}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            info = json.loads(e.read() or b"{}")
        except ValueError:
            info = {}
        if e.code == 404 and not path:
            raise cloudsync.SyncError("Jace doesn't store synced files yet.") from e
        if e.code in (401, 403) and info.get("error") in ("invalid_token", "insufficient_scope"):
            raise cloudsync.SyncError("Sign in with Jace again (Settings → Jace account).") from e
        if e.code == 413 or info.get("error") == "quota_exceeded":
            raise cloudsync.SyncError(info.get("error_description") or "Your file sync space is full.") from e
        return e.code, info
    except (urllib.error.URLError, OSError) as e:
        raise cloudsync.SyncError("Couldn't reach Jace. Check your internet connection.") from e


def _transfer(method: str, url: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
    """Straight to Backblaze, with the short-lived link Jace gave out."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.read()
    except (urllib.error.URLError, OSError) as e:
        raise cloudsync.SyncError("A file didn't transfer. It'll be tried again.") from e


def usage() -> dict:
    """{"used": bytes, "quota": bytes, "files": [{"id", "size", "created_at"}]}"""
    status, info = _files_call("GET")
    if status != 200:
        raise cloudsync.SyncError(info.get("error_description") or "Jace's file sync isn't available right now.")
    return info


def _upload_piece(piece_id: str, blob: bytes):
    status, info = _files_call("POST", "/upload", {"id": piece_id, "size": len(blob)})
    if status not in (200, 201):
        raise cloudsync.SyncError(info.get("error_description") or f"Jace refused a file ({status}).")
    if info.get("exists"):
        return
    _transfer("PUT", info["url"], blob, info.get("headers") or {"Content-Type": "application/octet-stream"})
    status, info = _files_call("POST", f"/{piece_id}/done")
    if status not in (200, 201):
        raise cloudsync.SyncError(info.get("error_description") or "Jace didn't take a file.")


def _download_piece(piece_id: str) -> bytes:
    status, info = _files_call("GET", f"/{piece_id}")
    if status != 200:
        raise cloudsync.SyncError("A synced file is missing in Jace.")
    return _transfer("GET", info["url"])


# --- pieces and their encryption --------------------------------------------------------------------

class Keys:
    def __init__(self, key: bytes):
        self.ids = hmac.new(key, b"jace-os-files-ids", hashlib.sha256).digest()
        self.enc = hmac.new(key, b"jace-os-files-encrypt", hashlib.sha256).digest()

    def piece_id(self, data: bytes) -> str:
        return hmac.new(self.ids, data, hashlib.sha256).hexdigest()

    def seal(self, data: bytes) -> bytes:
        """The same piece always seals the same way (so it's stored once), and only someone
        with your sync key can tell what's in it."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = hmac.new(self.ids, b"nonce" + data, hashlib.sha256).digest()[:12]
        return nonce + AESGCM(self.enc).encrypt(nonce, data, None)

    def open(self, blob: bytes) -> bytes:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        try:
            return AESGCM(self.enc).decrypt(blob[:12], blob[12:], None)
        except InvalidTag as e:
            raise cloudsync.SyncError("A synced file doesn't match your sync passphrase.") from e


def _pieces(path: Path, keys: Keys) -> list[str]:
    ids = []
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            ids.append(keys.piece_id(chunk))
    return ids or [keys.piece_id(b"")]


def _send_file(path: Path, keys: Keys, known: set) -> list[str]:
    ids = []
    with open(path, "rb") as f:
        first = True
        while True:
            chunk = f.read(CHUNK)
            if not chunk and not first:
                break
            first = False
            pid = keys.piece_id(chunk)
            if pid not in known:
                _upload_piece(pid, keys.seal(chunk))
                known.add(pid)
            ids.append(pid)
            if len(chunk) < CHUNK:
                break
    return ids


def _fetch_file(entry: dict, target: Path, keys: Keys):
    target.parent.mkdir(parents=True, exist_ok=True)
    if entry.get("link") is not None:
        if target.is_symlink() or target.exists():
            target.unlink()
        target.symlink_to(entry["link"])
        return
    tmp = target.with_name(f".{target.name}.jace-sync")
    with open(tmp, "wb") as out:
        for pid in entry["pieces"]:
            out.write(keys.open(_download_piece(pid)))
    os.chmod(tmp, entry.get("mode", 0o644))
    os.replace(tmp, target)
    mtime = entry.get("mtime")
    if mtime:
        os.utime(target, ns=(mtime, mtime))


# --- what's here -------------------------------------------------------------------------------------

def scan(root: Path) -> dict:
    """{relative path: {"size", "mtime" (ns), "mode"} or {"link": target}} for everything to sync."""
    found = {}
    if not root.is_dir():
        return found
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in EXCLUDES]
        here = Path(dirpath)
        for name in files + [d for d in os.listdir(here) if (here / d).is_symlink() and d in dirs]:
            if name in SKIP_NAMES or name.endswith(".jace-sync"):
                continue
            p = here / name
            rel = p.relative_to(root).as_posix()
            try:
                st = p.lstat()
            except OSError:
                continue
            if p.is_symlink():
                found[rel] = {"link": os.readlink(p)}
            elif p.is_file():
                found[rel] = {"size": st.st_size, "mtime": st.st_mtime_ns, "mode": st.st_mode & 0o777}
    return found


def _same_here(local: dict | None, base: dict | None) -> bool:
    """Is this file the way it was at the last sync (going by size, time and permissions)?"""
    if local is None or base is None:
        return local is None and (base is None or base.get("deleted"))
    if "link" in local or "link" in base:
        return local.get("link") == base.get("link")
    return all(local.get(k) == base.get(k) for k in ("size", "mtime", "mode"))


def _same_content(a: dict | None, b: dict | None) -> bool:
    if not a or not b:
        return (not a or a.get("deleted")) and (not b or b.get("deleted"))
    if a.get("deleted") or b.get("deleted"):
        return bool(a.get("deleted")) == bool(b.get("deleted"))
    if "link" in a or "link" in b:
        return a.get("link") == b.get("link")
    return a.get("pieces") == b.get("pieces") and a.get("mode") == b.get("mode")


def _conflict_name(path: Path, device: str) -> Path:
    stamp = time.strftime("%Y-%m-%d %H.%M")
    return path.with_name(f"{path.stem} (conflict from {device} {stamp}){path.suffix}")


def _trash(path: Path):
    """Deleted on another computer: to the Trash here, so nothing's ever lost by surprise."""
    if subprocess.run(["gio", "trash", str(path)], capture_output=True).returncode != 0 and path.exists():
        path.unlink()


# --- which folders --------------------------------------------------------------------------------

def _index_file() -> Path:
    return data_dir() / "files-index.json"


def folders(state: dict, manifest: dict) -> dict:
    """{name: (path here, conflict rule)} for every folder that syncs. Names are relative to your
    home folder, so they line up on computers with different usernames."""
    on = state.get("files", {})
    out = {}
    for gid, (_title, _about, paths, rule) in GROUPS.items():
        if not on.get(gid):
            continue
        names = paths if gid != "projects" else sorted(set(manifest.get("projects", [])) |
                                                       set(state.get("projects", [])))
        for name in names:
            out[name] = (HOME / name, rule)
    return out


def add_project(path: str) -> str:
    """Adds a project folder (in your home folder) to sync; returns its name."""
    p = Path(path).expanduser().resolve()
    try:
        name = p.relative_to(HOME).as_posix()
    except ValueError as e:
        raise cloudsync.SyncError("Project folders have to be in your home folder.") from e
    state = cloudsync.load_state()
    state.setdefault("projects", [])
    if name not in state["projects"]:
        state["projects"].append(name)
    state.setdefault("files", {})["projects"] = True
    cloudsync.save_state(state)
    return name


def remove_project(name: str):
    """Stops syncing a project folder on all computers (its files stay where they are)."""
    state = cloudsync.load_state()
    state["projects"] = [p for p in state.get("projects", []) if p != name]
    state.setdefault("removed_projects", []).append(name)
    cloudsync.save_state(state)


# --- syncing ------------------------------------------------------------------------------------------

def _load_manifest(keys_key: bytes):
    record = cloudsync._get(MANIFEST)
    if not record:
        return {"folders": {}, "projects": []}, 0
    raw = cloudsync.decrypt(keys_key, record["payload"])
    return json.loads(zlib.decompress(raw)), record["version"]


def _save_manifest(keys_key: bytes, manifest: dict, version: int) -> int | None:
    raw = zlib.compress(json.dumps(manifest, separators=(",", ":")).encode(), 9)
    return cloudsync._put(MANIFEST, cloudsync.encrypt(keys_key, raw), version)


def enabled(state: dict | None = None) -> bool:
    state = state or cloudsync.load_state()
    return state.get("enabled") and any(state.get("files", {}).values())


def sync() -> list[str]:
    """Syncs every folder that's turned on. Returns what happened."""
    state = cloudsync.load_state()
    if not enabled(state):
        return []
    key = cloudsync._key()
    keys = Keys(key)
    for _attempt in range(3):
        manifest, version = _load_manifest(key)
        try:
            index = json.loads(_index_file().read_text())
        except (OSError, ValueError):
            index = {}
        # Projects: this computer's list joins everyone's; removed ones leave it.
        removed = set(state.get("removed_projects", []))
        manifest["projects"] = sorted((set(manifest.get("projects", [])) | set(state.get("projects", []))) - removed)
        for name in removed:
            manifest.get("folders", {}).pop(name, None)
            index.pop(name, None)
        known = set()
        for f in manifest.get("folders", {}).values():
            for e in f.values():
                known.update(e.get("pieces", []))
        counts = {"up": 0, "down": 0, "conflicts": 0, "deleted": 0}
        for name, (root, rule) in folders(state, manifest).items():
            remote = manifest.setdefault("folders", {}).setdefault(name, {})
            index[name] = _sync_folder(root, rule, remote, index.get(name, {}), keys, known, counts)
        new_version = _save_manifest(key, manifest, version)
        if new_version is None:
            continue                      # another computer synced at the same time: again, with theirs
        _index_file().write_text(json.dumps(index))
        state = cloudsync.load_state()
        state["removed_projects"] = []
        state["files_last_sync"] = time.time()
        state["files_version"] = new_version
        state["files_error"] = ""
        cloudsync.save_state(state)
        _clean_up(manifest)
        return [f"Files: {counts['up']} sent, {counts['down']} received" +
                (f", {counts['deleted']} moved to the Trash" if counts["deleted"] else "") +
                (f", {counts['conflicts']} kept as conflict copies" if counts["conflicts"] else "")]
    raise cloudsync.SyncError("Your other computers keep syncing at the same time; it'll be tried again.")


def _sync_folder(root: Path, rule: str, remote: dict, base: dict, keys: Keys, known: set, counts: dict) -> dict:
    local = scan(root)
    device = socket.gethostname()
    new_base = {}
    for rel in sorted(set(local) | set(base) | set(remote)):
        L, B, R = local.get(rel), base.get(rel), remote.get(rel)
        path = root / rel
        changed_here = not _same_here(L, B)
        changed_there = not _same_content(R, B)
        if not changed_here and not changed_there:
            if B and not B.get("deleted"):
                new_base[rel] = B
            continue
        if changed_here and changed_there:
            # Both sides changed. Same content in the end? Then there's nothing to do.
            if L is not None and R and not R.get("deleted"):
                entry_here = _entry(path, L, keys)
                if _same_content(entry_here, R):
                    new_base[rel] = dict(R, **{k: L[k] for k in ("size", "mtime") if k in L})
                    continue
            newer_here = L is not None and (L.get("mtime", 0) / 1e9) > (R or {}).get("updated_at", 0)
            if L is None and R and R.get("deleted"):
                continue                                         # deleted on both
            if rule == "copy" and L is not None and R and not R.get("deleted"):
                # Keep both: the older one moves to a conflict copy next to the newer.
                if newer_here:
                    other = _conflict_name(path, R.get("device", "another computer"))
                    _fetch_file(R, other, keys)
                    counts["conflicts"] += 1
                else:
                    os.replace(path, _conflict_name(path, device))
                    counts["conflicts"] += 1
                    _fetch_file(R, path, keys)
                    counts["down"] += 1
                    new_base[rel] = _after_write(path, R)
                    continue
            if not newer_here:
                changed_here = False                              # theirs wins
            else:
                changed_there = False                             # ours wins
        if changed_here:
            if L is None:
                remote[rel] = {"deleted": time.time(), "updated_at": time.time(), "device": device}
                continue
            entry = _entry(path, L, keys, upload=True, known=known)
            entry.update(updated_at=time.time(), device=device)
            remote[rel] = entry
            new_base[rel] = dict(entry, **{k: L[k] for k in ("size", "mtime") if k in L})
            counts["up"] += 1
        elif changed_there:
            if R.get("deleted"):
                if path.exists() or path.is_symlink():
                    _trash(path)
                    counts["deleted"] += 1
                continue
            _fetch_file(R, path, keys)
            new_base[rel] = _after_write(path, R)
            counts["down"] += 1
    return new_base


def _entry(path: Path, local: dict, keys: Keys, upload=False, known=None) -> dict:
    if "link" in local:
        return {"link": local["link"]}
    pieces = _send_file(path, keys, known) if upload else _pieces(path, keys)
    return {"pieces": pieces, "size": local["size"], "mode": local["mode"], "mtime": local["mtime"]}


def _after_write(path: Path, entry: dict) -> dict:
    """What the file looks like here now (so the next sync sees it as unchanged)."""
    out = dict(entry)
    if entry.get("link") is None:
        st = path.lstat()
        out.update(size=st.st_size, mtime=st.st_mtime_ns, mode=st.st_mode & 0o777)
    return out


def _clean_up(manifest: dict):
    """Deletes pieces nothing uses any more (older than a day, so a computer that's still
    sending a file doesn't lose its pieces)."""
    used = set()
    for f in manifest.get("folders", {}).values():
        for e in f.values():
            used.update(e.get("pieces", []))
    try:
        stored = usage().get("files", [])
    except cloudsync.SyncError:
        return
    cutoff = time.time() - 86400
    for f in stored:
        created = f.get("created_at", 0)
        if isinstance(created, str):
            try:
                created = time.mktime(time.strptime(created[:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                created = time.time()
        if f["id"] not in used and created < cutoff:
            _files_call("DELETE", f"/{f['id']}")


def changed_here(state: dict | None = None) -> bool:
    """Quick check (no hashing): has anything in the synced folders changed since the last sync?"""
    state = state or cloudsync.load_state()
    try:
        index = json.loads(_index_file().read_text())
    except (OSError, ValueError):
        return True
    for name, (root, _rule) in folders(state, {"projects": state.get("projects", [])}).items():
        base = index.get(name)
        if base is None:
            return True
        local = scan(root)
        if set(local) != {k for k, v in base.items() if not v.get("deleted")}:
            return True
        if any(not _same_here(v, base.get(k)) for k, v in local.items()):
            return True
    return False
