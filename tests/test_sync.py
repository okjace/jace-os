#!/usr/bin/env python3
"""Tests for cloud sync and file sync (desktop/usr/lib/jace-os/jaceos/cloudsync.py and
filesync.py), against stand-ins for Jace's sync and file APIs. Pretend computers live in a
temporary folder; nothing on this computer or in Jace is touched.

    python3 tests/test_sync.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "desktop/usr/lib/jace-os"))
from jaceos import cloudsync as cs  # noqa: E402
from jaceos import filesync as fs  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="jace-sync-test-"))
FAILED = []


def check(what, ok):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILED.append(what)


# --- stand-ins for Jace: sync collections and private file storage ----------------------------------

store, blobs, pending = {}, {}, {}
transfers = []          # pieces actually sent to "Backblaze" (not ones it already had)
COLLECTIONS = ["keys", "bookmarks", "os-keys", "os-look", "os-wallpaper", "os-desktop", "os-apps", "os-files"]


def fake_sync(method, path, body=None, if_match=None):
    c = path.strip("/")
    if method == "GET" and not c:
        return 200, {"collections": COLLECTIONS,
                     "records": [{"collection": k, "version": v["version"]} for k, v in store.items()]}
    if method == "GET":
        return (200, dict(store[c])) if c in store else (404, {})
    if method == "PUT":
        current = store.get(c, {}).get("version", 0)
        if if_match != current:
            return 412, {"version": current}
        store[c] = {"payload": body["payload"], "version": current + 1, "updated_by": body["device"]}
        return 200, {"version": current + 1}
    if method == "DELETE":
        store.pop(c, None)
        return 200, {}


def fake_files(method, path="", body=None):
    if method == "GET" and not path:
        return 200, {"used": sum(map(len, blobs.values())), "quota": 1 << 30,
                     "files": [{"id": k, "size": len(v), "created_at": 0} for k, v in blobs.items()]}
    if method == "POST" and path == "/upload":
        return 200, ({"exists": True} if body["id"] in blobs else {"url": "mem://" + body["id"]})
    if method == "POST" and path.endswith("/done"):
        pid = path.split("/")[1]
        blobs[pid] = pending.pop(pid)
        return 200, {}
    if method == "GET":
        return 200, {"url": "mem://" + path.strip("/")}
    if method == "DELETE":
        blobs.pop(path.strip("/"), None)
        return 200, {}


def fake_transfer(method, url, data=None, headers=None):
    pid = url[len("mem://"):]
    if method == "PUT":
        pending[pid] = data
        transfers.append(pid)
        return b""
    return blobs[pid]


cs._call, fs._files_call, fs._transfer = fake_sync, fake_files, fake_transfer
# Nothing real changes: no desktop reloads or gsettings, and the Trash is a test folder.
cs.subprocess = type("NoOp", (), {"run": staticmethod(lambda *a, **k: None),
                                   "Popen": staticmethod(lambda *a, **k: None), "DEVNULL": subprocess.DEVNULL})
TRASH = TMP / "trash"
TRASH.mkdir()
fs._trash = lambda p: shutil.move(str(p), TRASH / p.name)
fs.CHUNK = 64 * 1024            # small pieces, so files get split
fs.SMALL = 16 * 1024            # files under 16 KB get packed together


def computer(name: str) -> Path:
    home = TMP / name
    (home / "Documents").mkdir(parents=True, exist_ok=True)
    (home / ".config/jace-os").mkdir(parents=True, exist_ok=True)
    os.environ.update(XDG_CONFIG_HOME=str(home / ".config"), XDG_DATA_HOME=str(home / ".local/share"),
                      XDG_CACHE_HOME=str(home / ".cache"))
    fs.HOME = home
    return home


def turn_on(categories=(), files=(), projects=()):
    st = cs.load_state()
    st.update(enabled=True, categories={c: c in categories for c in cs.CATEGORIES},
              files={g: g in files for g in fs.GROUPS}, projects=list(projects))
    cs.save_state(st)


def manifest():
    return fs._load_manifest(cs._key())[0]


# --- settings sync -----------------------------------------------------------------------------------

print("Settings sync")
a = computer("a")
cs.set_passphrase("correct horse battery")
turn_on(categories=("desktop",))
(cs.config_dir() / "bar.json").write_text('{"position": "bottom"}')
cs.sync()
check("only scrambled data is stored", "bottom" not in json.dumps(store))
b = computer("b")
try:
    cs.set_passphrase("wrong passphrase!")
    check("a wrong passphrase is refused", False)
except cs.SyncError:
    check("a wrong passphrase is refused", True)
cs.set_passphrase("correct horse battery")
turn_on(categories=("desktop",))
cs.sync()
check("a new computer takes what's synced", (cs.config_dir() / "bar.json").read_text() == '{"position": "bottom"}')

# --- file sync ---------------------------------------------------------------------------------------

print("File sync")
store.clear()
blobs.clear()
a = computer("a2")
cs.set_passphrase("correct horse battery")
turn_on(files=("documents",))
docs = a / "Documents"
(docs / "notes.txt").write_text("hello from A")
(docs / "run.sh").write_text("#!/bin/sh\necho hi\n")
(docs / "run.sh").chmod(0o755)
(docs / "big.bin").write_bytes(os.urandom(200_000))
(docs / "link").symlink_to("notes.txt")
(docs / "node_modules").mkdir()
(docs / "node_modules/x.js").write_text("skip me")
fs.sync()
everything = b"".join(blobs.values()) + json.dumps(store).encode()
check("nothing readable is stored", b"hello from A" not in everything and b"echo hi" not in everything)

b = computer("b2")
cs.set_passphrase("correct horse battery")
turn_on(files=("documents",))
fs.sync()
bdocs = b / "Documents"
check("files arrive", (bdocs / "notes.txt").read_text() == "hello from A")
check("executable stays executable", os.access(bdocs / "run.sh", os.X_OK))
check("symlinks stay symlinks", os.readlink(bdocs / "link") == "notes.txt")
check("big files arrive whole", (bdocs / "big.bin").read_bytes() == (docs / "big.bin").read_bytes())
check("build folders are skipped", not (bdocs / "node_modules").exists())

(bdocs / "notes.txt").write_text("B edited this")
fs.sync()
computer("a2")
fs.sync()
check("changes travel both ways", (docs / "notes.txt").read_text() == "B edited this")

(docs / "notes.txt").write_text("A's version")
time.sleep(1.1)
computer("b2")
(bdocs / "notes.txt").write_text("B's newer version")
fs.sync()
computer("a2")
fs.sync()
check("in a clash the newer wins", (docs / "notes.txt").read_text() == "B's newer version")
check("and the older is kept as a conflict copy", any(docs.glob("notes (conflict*")))

(docs / "run.sh").unlink()
fs.sync()
computer("b2")
fs.sync()
check("deleted elsewhere goes to the Trash", (TRASH / "run.sh").exists() and not (bdocs / "run.sh").exists())
check("nothing changed: nothing to do", fs.sync() == ["Files: 0 sent, 0 received"])

# --- progress and cancelling -------------------------------------------------------------------------

print("Progress and cancelling")
store.clear()
blobs.clear()
c = computer("c")
cs.set_passphrase("correct horse battery")
turn_on(files=("projects",), projects=("proj1", "proj2"))
for proj in ("proj1", "proj2"):
    (c / proj).mkdir()
    for i in range(6):
        (c / proj / f"f{i}.bin").write_bytes(os.urandom(100_000))
real_upload, sent = fs._upload_piece, []


def upload(pid, blob):
    real_upload(pid, blob)
    sent.append(pid)
    if len(sent) == 14:          # partway through proj2: someone presses Cancel
        fs.cancel()


fs._upload_piece = upload
result = fs.sync()
check("cancelling stops the sync", "cancelled" in result[0])
info = json.loads(fs.progress_file().read_text())
check("progress was counted", 0 < info["done"] < info["total"] and not info["running"])
check("the finished folder is already saved in Jace", len(manifest()["folders"].get("proj1", {})) == 6)
before = len(transfers)
fs.sync()
check("the next sync carries on", len(manifest()["folders"]["proj2"]) == 6)
# 12 files of 100 KB are 24 pieces of 64 KB; 14 went before Cancel, so at most 10 are left.
check("without sending finished pieces again", 0 < len(transfers) - before <= 24 - 14)
fs._upload_piece = real_upload

# --- small files are packed together -----------------------------------------------------------------

print("Small files")
store.clear()
blobs.clear()
d = computer("d")
cs.set_passphrase("correct horse battery")
turn_on(files=("projects",), projects=("code",))
(d / "code/src").mkdir(parents=True)
for i in range(200):
    (d / f"code/src/file{i}.py").write_text(f"print({i})\n" * 20)
before = len(transfers)
fs.sync()
uploads = len(transfers) - before
check(f"200 small files go up in a few packs ({uploads} uploads)", uploads <= 6)
e = computer("e")
cs.set_passphrase("correct horse battery")
turn_on(files=("projects",), projects=("code",))
fs.sync()
check("and all arrive intact", all((e / f"code/src/file{i}.py").read_text() == f"print({i})\n" * 20
                                   for i in range(200)))
(e / "code/src/file7.py").write_text("changed on E\n")
fs.sync()
computer("d")
fs.sync()
check("a changed small file comes across", (d / "code/src/file7.py").read_text() == "changed on E\n")
check("the same files on two computers aren't a clash", not list((d / "code/src").glob("*conflict*")))

shutil.rmtree(TMP, ignore_errors=True)
print("\nAll passed." if not FAILED else f"\n{len(FAILED)} failed.")
sys.exit(1 if FAILED else 0)
