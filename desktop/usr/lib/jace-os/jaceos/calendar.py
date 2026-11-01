"""The calendar that drops down when you click the clock on the top bar.

The time and date, a month calendar (today highlighted), and buttons for the Calendar
app and Settings → Date & time. Clicking the clock again, pressing Esc or clicking
anywhere else closes it.
"""
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from jaceos import bar, load_css, spawn, which  # noqa: E402
from jaceos.popup import (animated, hide_animated, make_layer, outside_click, place,  # noqa: E402
                          show_animated, strip_preload)

strip_preload()


class CalendarWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Calendar", css_classes=["jm-window"])
        make_layer(self, "jace-calendar", (360, 460))
        backdrop = Gtk.Overlay(css_classes=["jm-backdrop"])
        backdrop.set_child(Gtk.Box(hexpand=True, vexpand=True))
        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, css_classes=["jm-panel"],
                             width_request=360)
        position = bar.load()["position"]
        self.revealer = animated(self.panel)
        place(self.revealer, "top-center")
        if position == "bottom":
            self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
            self.revealer.set_valign(Gtk.Align.END)
            self.revealer.set_margin_top(0)
            self.revealer.set_margin_bottom(48)
        backdrop.add_overlay(self.revealer)
        outside_click(backdrop, lambda: self.panel, self.dismiss)

        self.time = Gtk.Label(css_classes=["jcal-time"], xalign=0)
        self.date = Gtk.Label(css_classes=["dim-label"], xalign=0)
        self.panel.append(self.time)
        self.panel.append(self.date)

        cal = Gtk.Calendar(css_classes=["jcal-calendar"])
        self.panel.append(cal)
        today = Gtk.Button(label="Today", css_classes=["flat", "jm-pill"], halign=Gtk.Align.START)
        today.connect("clicked", lambda *_: cal.select_day(GLib.DateTime.new_now_local()))

        row = Gtk.Box(spacing=6)
        row.append(today)
        row.append(Gtk.Box(hexpand=True))
        if which("gnome-calendar"):
            open_cal = Gtk.Button(label="Open Calendar", css_classes=["flat", "jm-pill"])
            open_cal.connect("clicked", lambda *_: (spawn(["gnome-calendar"]), self.dismiss()))
            row.append(open_cal)
        settings = Gtk.Button(icon_name="emblem-system-symbolic", css_classes=["flat", "circular"],
                              tooltip_text="Date & time settings")
        settings.connect("clicked", lambda *_: (spawn(["jace-settings", "--page", "datetime"]), self.dismiss()))
        row.append(settings)
        self.panel.append(row)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, k, *_: (self.dismiss(), True)[1] if k == Gdk.KEY_Escape else False)
        self.add_controller(keys)
        self.set_child(backdrop)
        self.tick()
        GLib.timeout_add_seconds(1, self.tick)

    def dismiss(self):
        """Slides the panel away, then closes the pop-up."""
        if getattr(self, "_closing", False):
            return
        self._closing = True
        hide_animated(self.revealer, self.close)

    def tick(self):
        now = GLib.DateTime.new_now_local()
        clock = bar.load()["clock"]
        fmt = ("%H:%M:%S" if clock.get("24h", True) else "%-I:%M:%S %p")
        self.time.set_label(now.format(fmt))
        self.date.set_label(now.format("%A, %B %-d, %Y"))
        return True


class CalendarApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.jaceos.Calendar", flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        load_css()

    def do_command_line(self, _cmdline):
        win = self.get_active_window()
        if win is not None:      # clicking the clock again closes it
            win.dismiss()
            return 0
        win = CalendarWindow(self)
        win.present()
        show_animated(win.revealer)
        return 0


def main():
    sys.exit(CalendarApp().run(sys.argv))
