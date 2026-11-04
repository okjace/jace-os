"""Finding updates for Jace OS: Arch packages (Linux and security fixes), the Jace
apps, Jace Browser and Flatpaks. Installing them is /usr/bin/jace-update (root).

  jace-updates            list what can be updated
  jace-updates --json     the same, for scripts
  jace-updates --waybar   the top bar's updates button (JSON)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from jaceos import JACE_APPS, ok, run, which

STATE = Path("/var/lib/jace-os")
REBOOT_FLAG = Path("/run/jace-os/reboot-required")
RELOGIN_FLAG = Path("/run/jace-os/relogin-required")


def finish_needed() -> str:
    """ "restart", "signout" or "" after updates (jace-update writes the flags in /run)."""
    if REBOOT_FLAG.exists():
        return "restart"
    if RELOGIN_FLAG.exists():
        started = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())) / "jace-session-started"
        # Signed in again since the update? Then it's done.
        if not started.exists() or started.stat().st_mtime < RELOGIN_FLAG.stat().st_mtime:
            return "signout"
    return ""


def _notify_finish(kind: str):
    """One notification per update that needs finishing, with a button to do it."""
    flag = REBOOT_FLAG if kind == "restart" else RELOGIN_FLAG
    stamp = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "jace-os" / "finish-notified"
    key = f"{kind}:{int(flag.stat().st_mtime)}"
    try:
        if stamp.read_text() == key:
            return
    except OSError:
        pass
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(key)
    if kind == "restart":
        title, body, button, action = ("Restart to finish updating",
                                       "Linux or system parts were updated. They're used after a restart.",
                                       "Restart now", "systemctl reboot")
    else:
        title, body, button, action = ("Sign out to finish updating",
                                       "Parts of the desktop were updated. Sign out and back in to use them.",
                                       "Sign out now", "labwc --exit")
    # Detached, so the top bar isn't kept waiting for the button.
    script = (f'[ "$(notify-send -a "Jace OS Updates" -u critical -i system-software-update '
              f'-A now="{button}" -A later="Later" "{title}" "{body}")" = now ] && {action}')
    subprocess.Popen(["sh", "-c", script], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
EXTRA_APPS = {"jace-browser-bin": "Jace Browser", "zen-browser-bin": "Zen Browser", "yay-bin": "yay"}


def pacman_updates() -> list[dict]:
    """[{name, old, new}] from checkupdates (a private copy of the package lists, so no root needed)."""
    if not which("checkupdates"):
        return []
    out = run(["checkupdates", "--nocolor"], timeout=180)
    updates = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "->":
            updates.append({"name": parts[0], "old": parts[1], "new": parts[3], "source": "pacman"})
    return updates


def flatpak_updates() -> list[dict]:
    if not which("flatpak"):
        return []
    out = run(["flatpak", "remote-ls", "--updates", "--columns=application,name,version"], timeout=120)
    updates = []
    for line in out.splitlines():
        cols = line.split("\t")
        if cols and cols[0]:
            updates.append({"name": cols[0], "title": cols[1] if len(cols) > 1 else cols[0],
                            "old": "", "new": cols[2] if len(cols) > 2 else "", "source": "flatpak"})
    return updates


def installed_version(pkg: str) -> str:
    out = run(["pacman", "-Q", pkg]).split()
    return out[1] if len(out) == 2 else ""


def check() -> dict:
    """Everything the Updates page shows."""
    system = pacman_updates()
    by_name = {u["name"]: u for u in system}
    apps = []
    # Extra apps are listed only when installed (Zen Browser stays gone once removed).
    extra = [(p, "jace-browser" if p == "jace-browser-bin" else "", n, "AUR helper" if p == "yay-bin" else "Web browser")
             for p, n in EXTRA_APPS.items() if installed_version(p)]
    for pkg, cmd, name, about in JACE_APPS + extra:
        cur = installed_version(pkg)
        upd = by_name.pop(pkg, None)
        apps.append({"package": pkg, "command": cmd, "name": name, "about": about, "installed": cur,
                     "new": upd["new"] if upd else "", "update": bool(upd)})
    security = [u for u in system if u["name"] in ("linux", "linux-lts", "linux-firmware", "openssl",
                                                   "glibc", "systemd", "sudo", "openssh", "archlinux-keyring")]
    return {
        "apps": apps,
        "system": list(by_name.values()),
        "security": [u["name"] for u in security],
        "flatpak": flatpak_updates(),
        "reboot_required": REBOOT_FLAG.exists(),
        "finish": finish_needed(),
        "auto": auto_enabled(),
        "last_update": _read(STATE / "last-update"),
        "last_error": _read(STATE / "last-error"),
    }


def count(result: dict) -> int:
    return sum(1 for a in result["apps"] if a["update"]) + len(result["system"]) + len(result["flatpak"])


def auto_enabled() -> bool:
    return ok(["systemctl", "is-enabled", "--quiet", "jace-update.timer"])


def _read(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def install(packages: list[str] | None = None) -> subprocess.Popen:
    """Starts jace-update (asks for a password unless you're an administrator). Returns the process."""
    return subprocess.Popen(["pkexec", "/usr/bin/jace-update", *(packages or [])],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def set_auto(on: bool) -> bool:
    return ok(["pkexec", "/usr/bin/jace-update", "--enable-auto" if on else "--disable-auto"], timeout=120)


def waybar() -> str:
    finish = finish_needed()
    if finish:
        _notify_finish(finish)
        if finish == "restart":
            return json.dumps({"text": "󰜉 Restart", "class": "available",
                               "tooltip": "Restart to finish installing updates (click for details)"})
        return json.dumps({"text": "󰍃 Sign out", "class": "available",
                           "tooltip": "Sign out and back in to finish installing updates (click for details)"})
    n = count({"apps": [{"update": False}], "system": pacman_updates(), "flatpak": flatpak_updates()})
    if n == 0:
        return json.dumps({"text": "", "tooltip": "Everything is up to date", "class": "none"})
    return json.dumps({"text": f"󰚰 {n}", "tooltip": f"{n} update{'s' if n != 1 else ''} available: click to see them",
                       "class": "available"})


def main():
    args = sys.argv[1:]
    if "--waybar" in args:
        print(waybar())
        return
    result = check()
    if "--json" in args:
        print(json.dumps(result, indent=2))
        return
    for a in result["apps"]:
        if a["update"]:
            print(f"{a['name']}: {a['installed']} -> {a['new']}")
    for u in result["system"]:
        print(f"{u['name']}: {u['old']} -> {u['new']}")
    for u in result["flatpak"]:
        print(f"{u['title']} (Flatpak): {u['new']}")
    if not count(result):
        print("Everything is up to date.")
