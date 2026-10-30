"""AppImages on Jace OS: double-click one to open it, or right-click → Install.

  jace-appimage open <file>       run it (makes it executable first)
  jace-appimage install <file>    copy it to ~/Applications and add it to the start menu
  jace-appimage remove <file>     take an installed AppImage out again

Installing reads the app's name and icon from inside the AppImage without running
it, and adds it to Jace Store's My apps too, where it can be opened or uninstalled.
"""
import configparser
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

APPS = Path.home() / "Applications"
MENU = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "applications"
ICONS = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "icons" / "jace-os-appimages"


def notify(title: str, body: str = "", icon: str = "application-x-executable", actions=()) -> str:
    cmd = ["notify-send", "-a", "AppImage", "-i", icon, *[f"-A{k}={v}" for k, v in actions], title, body]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120 if actions else 10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def squashfs_offset(path: Path) -> int:
    """The AppImage's files start right after its ELF runtime."""
    with open(path, "rb") as f:
        d = f.read(4096)
    if d[:4] != b"\x7fELF":
        raise ValueError("not an AppImage")
    shoff = struct.unpack_from("<Q", d, 0x28)[0]
    size, num = struct.unpack_from("<HH", d, 0x3A)
    return shoff + size * num


def read_metadata(path: Path) -> dict:
    """Name, comment, categories and icon from inside the AppImage, without running it."""
    info = {"name": re.sub(r"[-_.]?(x86_64|amd64|aarch64)?\.appimage$", "", path.name, flags=re.I) or path.stem,
            "comment": "", "categories": "Utility;", "icon": None}
    if not shutil.which("unsquashfs"):
        return info
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "root"
        try:
            off = str(squashfs_offset(path))
            subprocess.run(["unsquashfs", "-q", "-n", "-o", off, "-d", str(root), str(path),
                            "*.desktop", ".DirIcon", "*.png", "*.svg"], capture_output=True, timeout=60)
        except (OSError, ValueError, subprocess.SubprocessError):
            return info
        desktop = next(iter(sorted(root.glob("*.desktop"))), None)
        if desktop:
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            cp.optionxform = str
            try:
                cp.read(desktop, encoding="utf-8")
                e = cp["Desktop Entry"]
                info.update(name=e.get("Name", info["name"]), comment=e.get("Comment", ""),
                            categories=e.get("Categories", "Utility;"), icon_name=e.get("Icon", ""))
            except (configparser.Error, KeyError):
                pass
        # .DirIcon is usually a link to the real icon in the AppImage.
        icon = root / ".DirIcon"
        if icon.is_symlink():
            target = os.readlink(icon)
            icon = root / target.lstrip("/")
            if not icon.exists():
                subprocess.run(["unsquashfs", "-q", "-n", "-o", off, "-d", str(root), "-f", str(path),
                                target.lstrip("/")], capture_output=True, timeout=60)
        if not icon.exists() and info.get("icon_name"):
            icon = next((p for p in root.glob(f"{info['icon_name']}.*") if p.suffix in (".png", ".svg")), icon)
        if icon.exists() and icon.is_file():
            ICONS.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^a-z0-9]+", "-", info["name"].lower()).strip("-") or "app"
            suffix = ".svg" if icon.read_bytes()[:5] in (b"<?xml", b"<svg ") else ".png"
            dest = ICONS / f"{slug}{suffix}"
            shutil.copyfile(icon, dest)
            info["icon"] = str(dest)
    return info


def open_appimage(path: Path, args=()):
    if not os.access(path, os.X_OK):
        try:
            path.chmod(path.stat().st_mode | 0o755)
        except OSError:
            notify("Can't open this AppImage", f"{path.name} can't be made runnable here. Copy it to your "
                   "home folder first.", "dialog-error")
            return
    env = dict(os.environ, APPIMAGELAUNCHER_DISABLE="1")
    try:
        subprocess.Popen([str(path), *args], env=env, cwd=str(path.parent), start_new_session=True)
    except OSError as e:
        notify("Couldn't open the AppImage", str(e), "dialog-error")


def _store_dir() -> Path:
    d = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "jace-store"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _my_apps(update):
    """Edits Jace Store's My apps list (library.json)."""
    f = _store_dir() / "library.json"
    try:
        apps = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        apps = []
    apps = update(apps)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(apps, indent=2), encoding="utf-8")
    tmp.replace(f)


def install(path: Path) -> Path:
    path = path.resolve()
    info = read_metadata(path)
    APPS.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w .-]", "", info["name"]).strip() or path.stem
    dest = APPS / f"{safe}.AppImage"
    if path != dest:
        shutil.copy2(path, dest)
    dest.chmod(0o755)

    MENU.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", safe.lower()).strip("-") or "app"
    entry = MENU / f"appimage-{slug}.desktop"
    ex = str(dest).replace('"', '\\"')
    entry.write_text("\n".join([
        "[Desktop Entry]", "Type=Application", f"Name={info['name']}", f"Comment={info['comment']}",
        f'Exec=env APPIMAGELAUNCHER_DISABLE=1 "{ex}" %U', f"Path={dest.parent}",
        f"Icon={info['icon'] or 'application-x-executable'}", "Terminal=false",
        f"Categories={info['categories']}", f"X-JaceOS-AppImage={dest}", ""]), encoding="utf-8")
    subprocess.run(["update-desktop-database", str(MENU)], capture_output=True)

    def add(apps):
        apps = [a for a in apps if a.get("system_id") != f"appimage:{dest}"]
        apps.append({"id": uuid.uuid4().hex[:12], "name": info["name"], "kind": "exec", "target": str(dest),
                     "args": "", "icon": info["icon"], "website": "", "notes": info["comment"], "origin": "jace-os",
                     "system_id": f"appimage:{dest}", "added_at": time.time()})
        return apps

    _my_apps(add)
    return dest


def remove(path: Path):
    path = path.resolve()
    for entry in MENU.glob("appimage-*.desktop"):
        if f"X-JaceOS-AppImage={path}" in entry.read_text(encoding="utf-8", errors="ignore"):
            entry.unlink()
    _my_apps(lambda apps: [a for a in apps if a.get("system_id") != f"appimage:{path}"])
    if path.parent == APPS:
        path.unlink(missing_ok=True)


def main():
    args = sys.argv[1:]
    if len(args) < 2 or args[0] not in ("open", "install", "remove"):
        print(__doc__)
        sys.exit(2)
    action, path = args[0], Path(args[1])
    if not path.is_file():
        notify("AppImage not found", str(path), "dialog-error")
        sys.exit(1)
    if action == "open":
        open_appimage(path, args[2:])
    elif action == "remove":
        remove(path)
        notify("Removed", f"{path.name} is no longer installed.")
    else:
        try:
            dest = install(path)
        except (OSError, ValueError) as e:
            notify("Couldn't install the AppImage", str(e), "dialog-error")
            sys.exit(1)
        if notify(f"Installed {dest.stem}", "It's in the start menu and in Jace Store → My apps.",
                  "emblem-ok-symbolic", actions=[("open", "Open")]) == "open":
            open_appimage(dest)
