"""Helpers for the lock screen (jace-lock): a blurred, slightly darkened copy of your
wallpaper, and the time format matching your clock setting.

  python3 -m jaceos.lockscreen background   print the blurred wallpaper's path (made once)
  python3 -m jaceos.lockscreen time-format  print the gtklock time format
"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402


def wallpaper() -> str:
    try:
        return subprocess.run(["jace-wallpaper", "path"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "/usr/share/backgrounds/jace-os/default.png"


def background() -> str:
    src = wallpaper()
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "jace-os"
    cache.mkdir(parents=True, exist_ok=True)
    try:
        stamp = Path(src).stat().st_mtime_ns
    except OSError:
        return src
    out = cache / f"lock-{hashlib.sha1(f'{src}|{stamp}'.encode()).hexdigest()[:12]}.jpg"
    if out.exists():
        return str(out)
    for old in cache.glob("lock-*.jpg"):
        old.unlink(missing_ok=True)
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file(src)
    except GLib.Error:
        return src
    w, h = pb.get_width(), pb.get_height()
    scale = 1920 / max(w, 1)
    w, h = int(w * scale), int(h * scale)
    # Shrinking a lot and growing back (twice) is a cheap, smooth blur.
    for factor in (24, 12):
        small = pb.scale_simple(max(1, w // factor), max(1, h // factor), GdkPixbuf.InterpType.BILINEAR)
        pb = small.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
    # Darken a little so the clock and text stay readable on bright wallpapers.
    dark = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
    dark.fill(0x000000ff)
    dark.composite(pb, 0, 0, w, h, 0, 0, 1, 1, GdkPixbuf.InterpType.NEAREST, 70)
    pb.savev(str(out), "jpeg", ["quality"], ["90"])
    return str(out)


def time_format() -> str:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from jaceos import bar
        return "%H:%M" if bar.load()["clock"].get("24h", True) else "%-I:%M"
    except Exception:  # noqa: BLE001 - the lock screen must always start
        return "%H:%M"


if __name__ == "__main__":
    print(background() if sys.argv[1:] == ["background"] else time_format())
