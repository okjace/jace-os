"""One-time changes after updating Jace OS (run at sign-in as jace-migrate).

Jace Browser replaced Zen Browser as the default browser: the first time you sign in with
Jace Browser installed, it becomes your default browser, and if Zen Browser is still
installed you're asked whether to remove it (and the other apps Jace OS replaced:
Thunar, foot and mpv). You're only asked once.
"""
import os
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from jaceos import background, config_dir, load_css, which  # noqa: E402

FLAG = "migrated-jace-browser"
BROWSER = "jace-browser.desktop"
WEB_TYPES = ["x-scheme-handler/http", "x-scheme-handler/https", "text/html", "application/xhtml+xml"]

# (title, what it was replaced by, packages)
OLD_APPS = [
    ("Zen Browser", "Jace Browser", ["zen-browser-bin"]),
    ("Thunar", "Files", ["thunar", "thunar-volman", "thunar-archive-plugin", "tumbler"]),
    ("foot", "Ptyxis", ["foot"]),
    ("mpv", "Video Player and Audio Player", ["mpv"]),
]


def installed(pkg: str) -> bool:
    return subprocess.run(["pacman", "-Q", pkg], capture_output=True).returncode == 0


def needed_by(pkgs: list[str]) -> list[str]:
    """Other installed packages that need these (e.g. an app that uses mpv's libmpv), so they stay."""
    ours = {p for _t, _b, ps in OLD_APPS for p in ps}
    out = subprocess.run(["pacman", "-Qi", *pkgs], capture_output=True, text=True,
                         env={**os.environ, "LC_ALL": "C"}).stdout
    users = set()
    for line in out.splitlines():
        if line.startswith("Required By"):
            users.update(n for n in line.split(":", 1)[1].split() if n != "None")
    return sorted(users - ours)


def make_default_browser():
    app = Gio.DesktopAppInfo.new(BROWSER)
    if app is None:
        return
    for t in WEB_TYPES:
        try:
            app.set_as_default_for_type(t)
        except Exception:  # noqa: BLE001 - a type it can't take is fine
            pass
    subprocess.run(["xdg-settings", "set", "default-web-browser", BROWSER], capture_output=True)


class MigrateWindow(Adw.ApplicationWindow):
    def __init__(self, app, old):
        super().__init__(application=app, title="Jace Browser", default_width=520, default_height=520)
        self.set_icon_name("jace-browser")
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar(show_title=False))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_start=24, margin_end=24,
                      margin_bottom=24)
        box.append(Gtk.Image(icon_name="jace-browser", pixel_size=88))
        box.append(Gtk.Label(label="Jace Browser is your browser now", css_classes=["title-1"], wrap=True,
                             justify=Gtk.Justification.CENTER))
        box.append(Gtk.Label(label="It opens your links from now on. Do you want to remove the apps it and "
                                   "the other new apps replaced?", css_classes=["dim-label"], wrap=True,
                             justify=Gtk.Justification.CENTER))
        lb = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        self.choices = []
        for title, by, pkgs in old:
            users = needed_by(pkgs)
            if users:
                row = Adw.SwitchRow(title=f"Remove {title}", active=False, sensitive=False,
                                    subtitle=f"Kept, because {', '.join(users)} needs it")
            else:
                row = Adw.SwitchRow(title=f"Remove {title}", subtitle=f"Replaced by {by}", active=True)
            self.choices.append((row, pkgs))
            lb.append(row)
        box.append(lb)
        self.status = Gtk.Label(css_classes=["dim-label"], wrap=True, visible=False)
        box.append(self.status)
        buttons = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, margin_top=6)
        self.keep = Gtk.Button(label="Keep them", css_classes=["pill"])
        self.keep.connect("clicked", lambda *_: self.close())
        self.remove = Gtk.Button(label="Remove", css_classes=["pill", "destructive-action"])
        self.remove.connect("clicked", self.do_remove)
        buttons.append(self.keep)
        buttons.append(self.remove)
        box.append(buttons)
        view.set_content(box)
        self.set_content(view)

    def do_remove(self, *_):
        pkgs = [p for row, ps in self.choices if row.get_active() for p in ps if installed(p)]
        if not pkgs:
            self.close()
            return
        self.remove.set_sensitive(False)
        self.keep.set_sensitive(False)
        self.status.set_visible(True)
        self.status.set_label("Removing…")
        cmd = ["pkexec", "/usr/bin/jace-update", "--remove", *pkgs]

        def done(result):
            ok = result is True
            self.status.set_label("Done. They're removed." if ok else "They weren't removed. You can remove them "
                                  "later from a terminal with: sudo pacman -Rns " + " ".join(pkgs))
            self.keep.set_label("Close")
            self.keep.set_sensitive(True)

        background(lambda: subprocess.run(cmd, capture_output=True).returncode == 0, done)


class MigrateApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.jaceos.Migrate", flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.old = []

    def do_startup(self):
        Adw.Application.do_startup(self)
        load_css()

    def do_activate(self):
        MigrateWindow(self, self.old).present()


def main():
    flag = config_dir() / FLAG
    if flag.exists() or not which("jace-browser"):
        return      # done before, or Jace Browser isn't installed yet (try again next sign-in)
    make_default_browser()
    flag.touch()
    if not installed("zen-browser-bin"):
        return      # a new install, or Zen is already gone: nothing to ask
    app = MigrateApp()
    app.old = [(t, by, [p for p in pkgs if installed(p)]) for t, by, pkgs in OLD_APPS
               if any(installed(p) for p in pkgs)]
    sys.exit(app.run([sys.argv[0]]))
