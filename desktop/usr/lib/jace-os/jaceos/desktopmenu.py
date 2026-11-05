"""The menu you get when you right-click the desktop.

It opens where you clicked, in the Jace OS style (rounded, with icons, sliding in). Click an
item, press Esc or click anywhere else to close it. Arrow keys and Enter work too.
"""
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from jaceos import load_css, spawn  # noqa: E402
from jaceos.popup import animated, hide_animated, make_layer, outside_click, show_animated, strip_preload  # noqa: E402

strip_preload()

# (label, icon, command); None is a separator.
ITEMS = [
    ("Apps", "view-app-grid-symbolic", ["jace-menu"]),
    ("Files", "system-file-manager-symbolic", ["nautilus", "--new-window"]),
    ("Terminal", "utilities-terminal-symbolic", ["jace-terminal"]),
    None,
    ("Change wallpaper", "preferences-desktop-wallpaper-symbolic", ["jace-settings", "--page", "appearance"]),
    ("Display settings", "video-display-symbolic", ["jace-settings", "--page", "display"]),
    ("Top bar settings", "view-continuous-symbolic", ["jace-settings", "--page", "bar"]),
    ("Settings", "emblem-system-symbolic", ["jace-settings"]),
    None,
    ("Lock", "system-lock-screen-symbolic", ["jace-lock"]),
    ("Power", "system-shutdown-symbolic", ["jace-power"]),
]

WIDTH = 240


class DesktopMenu(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Desktop menu", css_classes=["jm-window"])
        self.layer = make_layer(self, "jace-desktop-menu", (WIDTH + 40, 420))
        backdrop = Gtk.Overlay(css_classes=["jm-backdrop"])
        backdrop.set_child(Gtk.Box(hexpand=True, vexpand=True))
        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["jm-panel", "jd-menu"],
                             width_request=WIDTH)
        for item in ITEMS:
            if item is None:
                self.panel.append(Gtk.Separator(css_classes=["jd-sep"]))
                continue
            label, icon, cmd = item
            content = Gtk.Box(spacing=12)
            content.append(Gtk.Image(icon_name=icon))
            content.append(Gtk.Label(label=label, xalign=0, hexpand=True))
            b = Gtk.Button(child=content, css_classes=["flat", "jd-item"])
            b.connect("clicked", lambda _b, c=cmd: self.launch(c))
            self.panel.append(b)
        self.revealer = animated(self.panel)
        self.revealer.set_halign(Gtk.Align.START)
        self.revealer.set_valign(Gtk.Align.START)
        backdrop.add_overlay(self.revealer)
        outside_click(backdrop, lambda: self.panel, self.dismiss)
        right = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right.connect("pressed", lambda *_: self.dismiss())
        backdrop.add_controller(right)

        # Open where the pointer is: the pointer "enters" this full-screen layer right away.
        self.placed = False
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda _c, x, y: self.place_at(x, y))
        motion.connect("motion", lambda _c, x, y: self.place_at(x, y))
        backdrop.add_controller(motion)
        if not self.layer:
            self.place_at(20, 20)
        # In case the pointer doesn't report where it is, open in the middle.
        GLib.timeout_add(250, lambda: (self.placed or self.place_center(), False)[1])

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, k, *_: (self.dismiss(), True)[1] if k == Gdk.KEY_Escape else False)
        self.add_controller(keys)
        self.set_child(backdrop)

    def place_at(self, x, y):
        if self.placed:
            return
        self.placed = True
        w, h = self.get_width() or 1920, self.get_height() or 1080
        _min, nat = self.panel.get_preferred_size()
        pw, ph = max(nat.width, WIDTH), nat.height or 400
        # Keep it on screen: open to the left of / above the pointer near the right or bottom edge.
        left = x if x + pw + 8 <= w else max(x - pw, 8)
        top = y if y + ph + 8 <= h else max(y - ph, 8)
        self.revealer.set_margin_start(int(left))
        self.revealer.set_margin_top(int(top))
        show_animated(self.revealer)
        self.panel.get_first_child().grab_focus()

    def place_center(self):
        if not self.placed:
            self.revealer.set_halign(Gtk.Align.CENTER)
            self.revealer.set_valign(Gtk.Align.CENTER)
            self.placed = True
            show_animated(self.revealer)

    def launch(self, cmd):
        spawn(cmd)
        self.dismiss()

    def dismiss(self):
        if getattr(self, "_closing", False):
            return
        self._closing = True
        hide_animated(self.revealer, self.close)


class DesktopMenuApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.jaceos.DesktopMenu", flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        load_css()

    def do_command_line(self, _cmdline):
        win = self.get_active_window()
        if win is not None:      # right-clicking again closes it
            win.dismiss()
            return 0
        DesktopMenu(self).present()
        return 0


def main():
    sys.exit(DesktopMenuApp().run(sys.argv))
