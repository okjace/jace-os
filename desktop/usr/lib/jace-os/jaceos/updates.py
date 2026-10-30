"""Finding updates for Jace OS: Arch packages (Linux and security fixes), the Jace
apps, Zen Browser and Flatpaks. Installing them is /usr/bin/jace-update (root).

  jace-updates            list what can be updated
  jace-updates --json     the same, for scripts
  jace-updates --waybar   the top bar's updates button (JSON)
"""
import json
import subprocess
import sys
from pathlib import Path

from jaceos import JACE_APPS, ok, run, which

STATE = Path("/var/lib/jace-os")
REBOOT_FLAG = Path("/run/jace-os/reboot-required")
EXTRA_APPS = {"zen-browser-bin": "Zen Browser"}


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
    for pkg, cmd, name, about in JACE_APPS + [(p, "", n, "Web browser") for p, n in EXTRA_APPS.items()]:
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
    if REBOOT_FLAG.exists():
        return json.dumps({"text": "󰜉", "tooltip": "Restart to finish installing updates", "class": "available"})
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
