"""Finding updates for Jace OS: Arch packages (Linux and security fixes), the Jace
apps, Jace Browser, Flatpaks and apps from the AUR (yay). Installing them is
/usr/bin/jace-update (root), and yay as you for the AUR.

Checks share a lock (the top bar and Settings checking at once used to make one of them
fail and show nothing), and the last good result is kept: a check that fails (offline,
or the package lists are busy) shows that instead of "up to date".

  jace-updates            list what can be updated
  jace-updates --json     the same, for scripts
  jace-updates --waybar   the top bar's updates button (JSON)
"""
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
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


def _cache_dir() -> Path:
    d = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "jace-os"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _parse_arrows(out: str, source: str) -> list[dict]:
    """Lines like "name 1.0-1 -> 1.1-1" (checkupdates, yay -Qua)."""
    updates = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "->":
            updates.append({"name": parts[0], "old": parts[1], "new": parts[3], "source": source})
    return updates


def _checked(kind: str, cmd: list[str], ok_codes: tuple, timeout: float) -> list[dict]:
    """Runs one update check (one at a time across the top bar and Settings), retrying once.
    If it still fails, the last good result is used, minus what's been installed since."""
    cache = _cache_dir() / f"updates-{kind}.json"
    with open(_cache_dir() / "updates.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for attempt in range(2):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                   env={**os.environ, "LC_ALL": "C"})
            except (OSError, subprocess.SubprocessError):
                r = None
            failed = r is None or r.returncode not in ok_codes or re.search(r"request failed|error", r.stderr, re.I)
            if not failed:
                found = _parse_arrows(r.stdout, kind)
                cache.write_text(json.dumps(found))
                return found
            if attempt == 0:
                time.sleep(3)
    try:
        old = json.loads(cache.read_text())
    except (OSError, ValueError):
        return []
    return [u for u in old if (cur := installed_version(u["name"])) and cur != u["new"]
            and newer(u["new"].rsplit("-", 1)[0], cur)]


def pacman_updates() -> list[dict]:
    """[{name, old, new}] from checkupdates (a private copy of the package lists, so no root
    needed). checkupdates exits 2 when there's nothing to update."""
    if not which("checkupdates"):
        return []
    return _checked("pacman", ["checkupdates", "--nocolor"], (0, 2), 180)


def aur_updates() -> list[dict]:
    """Apps you installed from the AUR (with yay or Jace Store) that have a newer version."""
    if not which("yay"):
        return []
    return _checked("aur", ["yay", "-Qua", "--color", "never"], (0, 1), 180)


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


# Where each app is released, so a new release shows up right away, even before the Jace OS
# package repository has it (installing it then builds it from the release: refresh-apps).
RELEASES = {
    "jace-launcher-bin": ("okjace/jace-launcher", "v"),
    "jace-social-bin": ("okjace/jace-social", "app-v"),
    "jace-store-bin": ("okjace/jace-store", "desktop-v"),
    "jace-browser-bin": ("okjace/jace-browser", "v"),
    "yay-bin": ("Jguer/yay", "v"),
}
RELEASE_CACHE_SECONDS = 15 * 60


def _release_cache() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "jace-os" / "releases.json"


def latest_releases() -> dict:
    """{package: newest released version} from GitHub, cached for 15 minutes (GitHub allows
    60 requests an hour without signing in). Apps it can't reach are left out."""
    cache = _release_cache()
    try:
        data = json.loads(cache.read_text())
        if time.time() - data.get("time", 0) < RELEASE_CACHE_SECONDS:
            return data["versions"]
    except (OSError, ValueError, KeyError):
        data = {}
    versions = {}
    for pkg, (repo, prefix) in RELEASES.items():
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases?per_page=30",
                                         headers={"Accept": "application/vnd.github+json",
                                                  "User-Agent": "jace-os-updates"})
            with urllib.request.urlopen(req, timeout=10) as r:
                releases = json.load(r)
        except (OSError, ValueError):
            if pkg in data.get("versions", {}):
                versions[pkg] = data["versions"][pkg]    # keep what we knew
            continue
        for rel in releases:
            tag = rel.get("tag_name", "")
            if not rel.get("draft") and not rel.get("prerelease") and tag.startswith(prefix):
                versions[pkg] = tag[len(prefix):].replace("-", "_")
                break
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"time": time.time(), "versions": versions}))
    except OSError:
        pass
    return versions


def newer(candidate: str, installed: str) -> bool:
    """Is candidate (a version like 1.11.3) newer than installed (like 1.11.2-1)?"""
    if not candidate or not installed:
        return False
    out = run(["vercmp", candidate, installed.rsplit("-", 1)[0]]).strip()
    return out.lstrip("-").isdigit() and int(out) > 0


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
    released = latest_releases()
    for pkg, cmd, name, about in JACE_APPS + extra:
        cur = installed_version(pkg)
        upd = by_name.pop(pkg, None)
        new_ = upd["new"] if upd else ""
        # Released on GitHub but not in the Jace OS repository yet: still an update.
        if newer(released.get(pkg, ""), cur) and not newer(new_.rsplit("-", 1)[0], released[pkg]):
            new_ = released[pkg]
        apps.append({"package": pkg, "command": cmd, "name": name, "about": about, "installed": cur,
                     "new": new_, "update": bool(new_) and cur != new_})
    security = [u for u in system if u["name"] in ("linux", "linux-lts", "linux-firmware", "openssl",
                                                   "glibc", "systemd", "sudo", "openssh", "archlinux-keyring")]
    return {
        "apps": apps,
        "system": list(by_name.values()),
        "security": [u["name"] for u in security],
        "flatpak": flatpak_updates(),
        "aur": aur_updates(),
        "reboot_required": REBOOT_FLAG.exists(),
        "finish": finish_needed(),
        "auto": auto_enabled(),
        "last_update": _read(STATE / "last-update"),
        "last_error": _read(STATE / "last-error"),
    }


def count(result: dict) -> int:
    return (sum(1 for a in result["apps"] if a["update"]) + len(result["system"]) + len(result["flatpak"])
            + len(result.get("aur", [])))


def auto_enabled() -> bool:
    return ok(["systemctl", "is-enabled", "--quiet", "jace-update.timer"])


def _read(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def install(packages: list[str] | None = None) -> subprocess.Popen:
    """Starts jace-update for these packages, or ["--flatpak"] for just the Flatpaks (asks for a
    password unless you're an administrator). Returns the process."""
    return subprocess.Popen(["pkexec", "/usr/bin/jace-update", *(packages or [])],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def install_aur(packages: list[str]) -> subprocess.Popen:
    """Updates AUR apps with yay, as you (AUR apps can't be built as root). Installing what yay
    built asks for your password in the usual window (pkexec instead of sudo in a terminal)."""
    script = ('echo "@@ 0 98 Updating apps from the AUR"; '
              'PACMAN_AUTH=pkexec yay -S --needed --noconfirm --answerdiff None --answeredit None '
              '--answerclean None --removemake --sudo pkexec --color never "$@"; code=$?; '
              'echo "@@ 100 100 Done"; exit $code')
    return subprocess.Popen(["sh", "-c", script, "jace-aur-update", *packages],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


class Progress:
    """Turns the updater's output into a progress bar: jace-update prints stages
    ("@@ <from %> <to %> <what>"), and within a stage this follows pacman going through the
    packages ("(3/12) upgrading linux") or yay building AUR apps ("==> Making package: x")."""

    PREPARE = ("checking", "loading")
    INSTALL = {"upgrading": "Updating", "installing": "Installing", "reinstalling": "Reinstalling",
               "downgrading": "Installing", "removing": "Removing"}

    def __init__(self, aur: list[str] | None = None):
        self.lo, self.hi, self.value, self.text = 0.0, 0.0, 0.0, "Starting…"
        self.aur = aur or []
        self.built = 0

    def feed(self, line: str) -> bool:
        """Reads one line of output. Returns False for progress markers (not for the log)."""
        line = line.rstrip("\n")
        m = re.match(r"@@ (\d+) (\d+) (.*)", line)
        if m:
            self.lo, self.hi = int(m.group(1)) / 100, int(m.group(2)) / 100
            self.value, self.text = self.lo, m.group(3)
            return False
        m = re.match(r"\((\d+)/(\d+)\) (\w+)\s*(.*)", line.strip())
        if m and m.group(2) != "0":
            i, n, verb, rest = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4).strip()
            if verb in self.PREPARE:
                part = 0.3 * i / n
            elif verb in self.INSTALL:
                part = 0.3 + 0.7 * i / n
                self.text = f"{self.INSTALL[verb]} {rest.split()[0] if rest else ''} ({i} of {n})".replace("  ", " ")
            else:
                return True
            self.value = max(self.value, self.lo + (self.hi - self.lo) * part)
            return True
        m = re.match(r"==> Making package: (\S+)", line)
        if m and self.aur:
            self.built += 1
            self.value = self.lo + (self.hi - self.lo) * (self.built - 0.5) / max(len(self.aur), 1)
            self.text = f"Building {m.group(1)} ({min(self.built, len(self.aur))} of {len(self.aur)})"
        return True


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
    system = pacman_updates()
    in_repo = {u["name"] for u in system}
    released = latest_releases()
    early = [p for p, v in released.items() if p not in in_repo and newer(v, installed_version(p))]
    n = count({"apps": [{"update": True}] * len(early), "system": system, "flatpak": flatpak_updates(),
               "aur": aur_updates()})
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
    for u in result["aur"]:
        print(f"{u['name']} (AUR): {u['old']} -> {u['new']}")
    if not count(result):
        print("Everything is up to date.")
