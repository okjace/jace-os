"""Editing the start menu's apps (Settings → Start menu → Edit the menu).

Apps are .desktop files. Editing an app saves your own copy in
~/.local/share/applications (the same file name), which every app menu uses instead of
the system one; "Remove my changes" deletes that copy. Apps and websites you add are new
files there named jace-os-custom-*.desktop.
"""
import configparser
import os
import re
import shlex
import uuid
from pathlib import Path

from gi.repository import Gio

CUSTOM_PREFIX = "jace-os-custom-"


def user_dir() -> Path:
    d = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "applications"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _parser() -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None, strict=False, delimiters=("=",))
    cp.optionxform = str
    return cp


def read(path: Path) -> dict:
    cp = _parser()
    try:
        cp.read(path, encoding="utf-8")
        return dict(cp["Desktop Entry"])
    except (configparser.Error, KeyError, OSError):
        return {}


def system_path(app_id: str) -> Path | None:
    """The app's .desktop file that isn't yours (what "Remove my changes" goes back to)."""
    dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    for d in dirs + ["/var/lib/flatpak/exports/share"]:
        p = Path(d) / "applications" / app_id
        if p.exists():
            return p
    return None


def load(app_id: str) -> dict:
    mine = user_dir() / app_id
    if mine.exists():
        return read(mine)
    app = Gio.DesktopAppInfo.new(app_id)
    if app and app.get_filename():
        return read(Path(app.get_filename()))
    return {}


def has_override(app_id: str) -> bool:
    return (user_dir() / app_id).exists()


def is_custom_id(app_id: str) -> bool:
    return app_id.startswith(CUSTOM_PREFIX)


def is_custom(path: Path) -> bool:
    return is_custom_id(path.name)


def user_entries() -> list[Path]:
    """Your added apps, plus apps you edited (copies of system apps)."""
    out = []
    for f in user_dir().glob("*.desktop"):
        if is_custom(f) or system_path(f.name):
            if read(f).get("X-JaceOS-Edited") == "true" or is_custom(f):
                out.append(f)
    return out


def shown_exec(exec_line: str) -> str:
    """What to show in the "Command or website" box: a website for our web launchers, else the command."""
    m = re.fullmatch(r"xdg-open (\S+)", exec_line.strip())
    if m:
        return m.group(1)
    return re.sub(r"\s+%[fFuUick]", "", exec_line).strip()


def _looks_like_website(what: str) -> bool:
    from shutil import which
    if re.match(r"^(https?://|www\.)", what):
        return True
    # youtube.com, jaceo.vercel.app/projects … but not a command that happens to have a dot in it
    return bool(re.fullmatch(r"[\w-]+(\.[\w-]+)+(/\S*)?", what)) and not which(what.split("/")[0])


def _exec_for(what: str) -> str:
    what = what.strip()
    if _looks_like_website(what):
        url = what if what.startswith("http") else "https://" + what
        return "xdg-open " + url
    return what


def save(app_id: str | None, name: str, what: str, icon: str, hidden: bool) -> str:
    """Saves an edited or new app. Returns its id (file name)."""
    if app_id:
        base = load(app_id)
        target = user_dir() / app_id
    else:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "app"
        app_id = f"{CUSTOM_PREFIX}{slug}-{uuid.uuid4().hex[:6]}.desktop"
        base = {"Type": "Application", "Terminal": "false"}
        target = user_dir() / app_id
    is_web = _exec_for(what).startswith("xdg-open ")
    old_exec = base.get("Exec", "")
    new_exec = _exec_for(what)
    # Keep %U-style arguments when the command didn't really change.
    if old_exec and shown_exec(old_exec) == what.strip():
        new_exec = old_exec
    entry = dict(base)
    entry.update({"Name": name, "Exec": new_exec, "NoDisplay": "true" if hidden else "false",
                  "X-JaceOS-Edited": "true"})
    entry.pop("TryExec", None)          # your command decides, not the original program's path
    entry.pop("DBusActivatable", None)  # or launchers would start the original app, not your command
    entry.pop("Actions", None)          # only the main section is copied
    if icon:
        entry["Icon"] = icon
    elif is_web and "Icon" not in entry:
        entry["Icon"] = "web-browser"
    if is_web:
        entry.setdefault("Categories", "Network;")
    # Translations of the old name would win over yours in other languages.
    for k in [k for k in entry if re.fullmatch(r"(Name|GenericName|Comment)\[.+\]", k)]:
        if k.startswith("Name["):
            entry.pop(k)
    lines = ["[Desktop Entry]"] + [f"{k}={v}" for k, v in entry.items()]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return app_id


def reset(app_id: str):
    (user_dir() / app_id).unlink(missing_ok=True)


def command_works(what: str) -> bool:
    """Whether a command's program exists (websites always do)."""
    exec_line = _exec_for(what)
    if exec_line.startswith("xdg-open "):
        return True
    try:
        prog = shlex.split(exec_line)[0]
    except (ValueError, IndexError):
        return False
    from shutil import which
    return bool(which(prog) or Path(prog).exists())
