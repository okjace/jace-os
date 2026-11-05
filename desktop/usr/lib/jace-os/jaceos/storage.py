"""Storage for Settings → Storage: how full each drive is, what's using the space in your
home folder, and cleaning up (trash, caches, old package downloads, unused packages and
Flatpak runtimes).

Cleanups that need an administrator go through /usr/bin/jace-clean (pkexec).

  jace-storage --auto   run at sign-in: empties trash older than 30 days if you turned that
                        on, and warns you when a drive is nearly full
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from jaceos import config_dir, run, which

HOME = Path.home()
CACHE = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache"))
DATA = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local/share"))
TRASH = DATA / "Trash"
PACMAN_CACHE = Path("/var/cache/pacman/pkg")

# Drives worth showing: real filesystems, not memory or system mounts.
_SKIP_FS = {"tmpfs", "devtmpfs", "efivarfs", "overlay", "squashfs", "proc", "sysfs", "ramfs", "fuse.portal",
            "fuse.gvfsd-fuse", "nsfs", "autofs"}

DEFAULTS = {"auto_trash": False, "trash_days": 30, "low_space_warning": True}


def human(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("bytes", "KB") else f"{n:.1f} {unit}"
        n /= 1000
    return ""


def drives() -> list[dict]:
    """Each mounted drive once (btrfs subvolumes of one drive count once), / first."""
    out = run(["df", "-B1", "--output=source,fstype,size,used,avail,target"], timeout=10)
    seen, result = set(), []
    for line in out.splitlines()[1:]:
        parts = line.split(None, 5)
        if len(parts) < 6 or parts[1] in _SKIP_FS or not parts[0].startswith("/dev/"):
            continue
        source, fstype, size, used, avail, target = parts
        if target.startswith(("/boot", "/efi")) or source in seen:
            continue
        seen.add(source)
        size, used, avail = int(size), int(used), int(avail)
        if size <= 0:
            continue
        name = "This computer" if target == "/" else Path(target).name or target
        result.append({"name": name, "mount": target, "device": source, "fstype": fstype,
                       "size": size, "used": used, "free": avail})
    result.sort(key=lambda d: d["mount"] != "/")
    return result


def du(path: Path) -> int:
    """Bytes used under path (0 if it's missing). Doesn't cross into other drives."""
    if not path.exists():
        return 0
    out = run(["du", "-sbx", str(path)], timeout=300)
    try:
        return int(out.split()[0])
    except (IndexError, ValueError):
        return 0


def _xdg_dir(name: str, fallback: str) -> Path:
    try:
        out = subprocess.run(["xdg-user-dir", name], capture_output=True, text=True, timeout=5).stdout.strip()
        if out and Path(out) != HOME:
            return Path(out)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return HOME / fallback


def categories() -> list[dict]:
    """What's using space in your home folder, biggest first. Slow (it adds up every file)."""
    folders = [
        ("Documents", "folder-documents-symbolic", _xdg_dir("DOCUMENTS", "Documents")),
        ("Downloads", "folder-download-symbolic", _xdg_dir("DOWNLOAD", "Downloads")),
        ("Pictures", "folder-pictures-symbolic", _xdg_dir("PICTURES", "Pictures")),
        ("Videos", "folder-videos-symbolic", _xdg_dir("VIDEOS", "Videos")),
        ("Music", "folder-music-symbolic", _xdg_dir("MUSIC", "Music")),
        ("Desktop", "user-desktop-symbolic", _xdg_dir("DESKTOP", "Desktop")),
        ("AppImages", "application-x-executable-symbolic", HOME / "Applications"),
        ("Flatpak apps", "application-x-addon-symbolic", DATA / "flatpak"),
        ("Trash", "user-trash-symbolic", TRASH),
        ("Temporary files and caches", "edit-clear-all-symbolic", CACHE),
    ]
    result, counted = [], 0
    for title, icon, path in folders:
        size = du(path)
        counted += size
        if title == "Flatpak apps":
            size += du(Path("/var/lib/flatpak"))     # everyone's Flatpaks (outside your home folder)
        result.append({"title": title, "icon": icon, "path": str(path), "size": size})
    other = max(du(HOME) - counted, 0)
    result.append({"title": "Other files in your home folder", "icon": "folder-symbolic", "path": str(HOME),
                   "size": other})
    return sorted((c for c in result if c["size"] > 0), key=lambda c: -c["size"])


def _bytes(number: str, unit: str) -> int:
    return int(float(number) * {"KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "TiB": 1024 ** 4}.get(unit, 1))


def cleanup() -> dict:
    """What can be cleaned up, and how much each frees (bytes)."""
    pkg_cache = 0
    if which("paccache"):
        for args in (["-dk1"], ["-duk0"]):
            m = re.search(r"disk space saved: ([\d.]+) (\w+)", run(["paccache", *args], timeout=60))
            if m:
                pkg_cache += _bytes(m.group(1), m.group(2))
    orphans = [p for p in run(["pacman", "-Qdtq"], timeout=30).split() if p]
    orphan_size = 0
    if orphans:
        info = run(["env", "LC_ALL=C", "pacman", "-Qi", *orphans], timeout=30)
        for m in re.finditer(r"^Installed Size\s*:\s*([\d.]+) (\w+)", info, re.M):
            orphan_size += _bytes(m.group(1), m.group(2))
    return {
        "trash": du(TRASH),
        "thumbnails": du(CACHE / "thumbnails"),
        "build_cache": du(CACHE / "yay"),
        "packages": pkg_cache,
        "orphans": orphans,
        "orphans_size": orphan_size,
    }


# --- Cleaning up (as you) ---------------------------------------------------------------------------

def empty_trash() -> bool:
    if which("gio") and subprocess.run(["gio", "trash", "--empty"], capture_output=True).returncode == 0:
        return True
    for sub in ("files", "info", "expunged"):
        shutil.rmtree(TRASH / sub, ignore_errors=True)
    return True


def clear_thumbnails() -> bool:
    shutil.rmtree(CACHE / "thumbnails", ignore_errors=True)
    return True


def clear_build_cache() -> bool:
    """yay keeps every AUR package it built, and its downloads, in ~/.cache/yay."""
    shutil.rmtree(CACHE / "yay", ignore_errors=True)
    return True


def remove_unused_flatpaks() -> bool:
    if not which("flatpak"):
        return True
    ok = subprocess.run(["flatpak", "uninstall", "--user", "--unused", "-y", "--noninteractive"],
                        capture_output=True).returncode == 0
    return ok and subprocess.run(["pkexec", "/usr/bin/jace-clean", "flatpak"], capture_output=True).returncode == 0


def admin_clean(what: str) -> bool:
    """what: packages | orphans | flatpak (asks for your password)."""
    return subprocess.run(["pkexec", "/usr/bin/jace-clean", what], capture_output=True).returncode == 0


# --- Settings and automatic clean-up ----------------------------------------------------------------

def _file() -> Path:
    return config_dir() / "storage.json"


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(_file().read_text()))
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg: dict):
    _file().write_text(json.dumps(cfg, indent=2))


def empty_old_trash(days: int) -> int:
    """Permanently deletes what's been in the trash longer than days. Returns how many."""
    cutoff = time.time() - days * 86400
    removed = 0
    info_dir = TRASH / "info"
    if not info_dir.is_dir():
        return 0
    for info in info_dir.glob("*.trashinfo"):
        try:
            m = re.search(r"^DeletionDate=(.+)$", info.read_text(errors="replace"), re.M)
            when = time.mktime(time.strptime(m.group(1).strip()[:19], "%Y-%m-%dT%H:%M:%S")) if m \
                else info.stat().st_mtime
        except (OSError, ValueError):
            continue
        if when > cutoff:
            continue
        item = TRASH / "files" / info.name[:-len(".trashinfo")]
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item, ignore_errors=True)
        else:
            item.unlink(missing_ok=True)
        info.unlink(missing_ok=True)
        removed += 1
    return removed


def auto():
    cfg = load()
    if cfg.get("auto_trash"):
        empty_old_trash(int(cfg.get("trash_days", 30)))
    if cfg.get("low_space_warning", True):
        for d in drives():
            if d["free"] < max(d["size"] * 0.05, 2e9):
                proc = subprocess.Popen(
                    ["notify-send", "-a", "Settings", "-i", "drive-harddisk-symbolic", "-u", "critical",
                     "-A", "open=Free up space", f"{d['name']} is almost full",
                     f"Only {human(d['free'])} is left. Settings → Storage can free up space."],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                out, _ = proc.communicate()
                if out.strip() == "open":
                    subprocess.Popen(["jace-settings", "--page", "storage"])
                break


def main():
    if "--auto" in sys.argv:
        auto()
        return
    for d in drives():
        print(f"{d['name']} ({d['mount']}): {human(d['free'])} free of {human(d['size'])}")
