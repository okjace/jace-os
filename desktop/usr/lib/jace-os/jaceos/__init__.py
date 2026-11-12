"""Jace OS's own apps: Settings, Setup, the Jace account and updates.

They're GTK 4 + libadwaita apps written in Python. The launchers in /usr/bin
(jace-settings, jace-setup, jace-account, jace-updates) add this folder to
sys.path and call main() in the matching module.
"""
import os
import shutil
import subprocess
import threading
from pathlib import Path

VERSION = "1.0.0"
WEBSITE = "https://okjace.github.io/jace-os/"
GITHUB = "https://github.com/okjace/jace-os"
JACE_URL = "https://jaceo.vercel.app"
STORE_URL = "https://jace-store-deb.vercel.app"
# JACE_OS_ROOT lets you run these apps from a checkout of the repository (it points at desktop/).
ROOT = Path(os.environ.get("JACE_OS_ROOT", "/"))
SHARE = ROOT / "usr/share/jace-os"
LOGO = SHARE / "branding" / "jace-os-logo.png"
BACKGROUNDS = ROOT / "usr/share/backgrounds/jace-os"

# The Jace apps that come with Jace OS: (package, command, name, what it is).
JACE_APPS = [
    ("jace-launcher-bin", "jace-launcher", "Jace Launcher", "Minecraft with mods, modpacks and skins"),
    ("jace-social-bin", "jace-social", "Jace Social", "Friends, chats, servers and voice calls"),
    ("jace-store-bin", "jace-store", "Jace Store", "Get apps and games"),
]


def config_dir() -> Path:
    d = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "jace-os"
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_dir() -> Path:
    d = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "jace-os"
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_live() -> bool:
    """True on the live USB (archiso boots with this on the kernel command line)."""
    try:
        return "archisobasedir=" in Path("/proc/cmdline").read_text()
    except OSError:
        return False


def which(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def run(cmd: list[str], timeout: float = 30) -> str:
    """Runs a command and returns its output ("" if it failed or isn't installed)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def ok(cmd: list[str], timeout: float = 60) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def spawn(cmd: list[str]) -> None:
    """Starts an app and leaves it running after we quit."""
    try:
        subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def background(work, done=None):
    """Runs work() on a thread, then done(result) on the GTK main loop."""
    from gi.repository import GLib

    def thread():
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - shown to the user by done()
            result = exc
        if done:
            GLib.idle_add(lambda: (done(result), False)[1])

    threading.Thread(target=thread, daemon=True).start()


def os_release() -> dict:
    info = {}
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            for line in Path(path).read_text().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    info[k] = v.strip().strip('"')
            break
        except OSError:
            continue
    return info


def load_css():
    from gi.repository import Gdk, Gtk
    provider = Gtk.CssProvider()
    provider.load_from_path(str(SHARE / "apps.css"))
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
