"""Shared bits for Jace OS's pop-ups that sit above windows (start menu, clipboard,
calendar). They use gtk4-layer-shell, which /usr/lib/jace-os/run-popup preloads.

Each pop-up is a full-screen, see-through layer surface with its panel placed on it, so a
click anywhere outside the panel closes it (see outside_click).
"""
import os

import gi

gi.require_version("Gtk", "4.0")
try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell
except (ValueError, ImportError):
    LayerShell = None
from gi.repository import GLib, Gtk  # noqa: E402


def strip_preload():
    """gtk4-layer-shell is loaded now: keep it out of apps we start (it crashes GTK 3 apps)."""
    os.environ.pop("LD_PRELOAD", None)
    GLib.unsetenv("LD_PRELOAD")


def make_layer(window: Gtk.Window, namespace: str, fallback_size=(640, 680)) -> bool:
    """Turns a window into a full-screen overlay layer. False if layer-shell isn't available."""
    window.set_decorated(False)
    from gi.repository import Gdk
    wayland = type(Gdk.Display.get_default()).__name__ == "WaylandDisplay"
    if LayerShell and wayland and LayerShell.is_supported():
        LayerShell.init_for_window(window)
        LayerShell.set_namespace(window, namespace)
        LayerShell.set_layer(window, LayerShell.Layer.OVERLAY)
        LayerShell.set_keyboard_mode(window, LayerShell.KeyboardMode.EXCLUSIVE)
        for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
            LayerShell.set_anchor(window, edge, True)
        LayerShell.set_exclusive_zone(window, -1)
        return True
    window.set_default_size(*fallback_size)   # testing somewhere without layer-shell
    return False


def place(panel: Gtk.Widget, where: str, top_margin: int = 48):
    """where: top-left | top-center | top-right | center."""
    panel.set_halign({"top-left": Gtk.Align.START, "top-right": Gtk.Align.END}.get(where, Gtk.Align.CENTER))
    panel.set_valign(Gtk.Align.CENTER if where == "center" else Gtk.Align.START)
    panel.set_margin_top(0 if where == "center" else top_margin)
    panel.set_margin_start(8 if where == "top-left" else 0)
    panel.set_margin_end(8 if where == "top-right" else 0)


def outside_click(backdrop: Gtk.Widget, panel_getter, on_outside):
    """Calls on_outside() for a click on the backdrop that isn't on the panel."""
    def pressed(_g, _n, x, y):
        panel = panel_getter()
        ok, r = panel.compute_bounds(backdrop)
        inside = ok and r.get_x() <= x <= r.get_x() + r.get_width() and r.get_y() <= y <= r.get_y() + r.get_height()
        if not inside:
            on_outside()
    gesture = Gtk.GestureClick()
    gesture.connect("pressed", pressed)
    backdrop.add_controller(gesture)


ANIMATION_MS = 180


def animated(panel: Gtk.Widget) -> Gtk.Revealer:
    """Wraps a pop-up's panel so it slides and fades in and out. Place the returned revealer
    (not the panel) on the backdrop."""
    revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                            transition_duration=ANIMATION_MS, child=panel, reveal_child=False)
    revealer.add_css_class("jp-revealer")
    panel.add_css_class("jp-hidden")      # fades in as it slides (apps.css)
    return revealer


def show_animated(revealer: Gtk.Revealer):
    """Call after presenting the window: the panel slides in."""
    revealer.get_child().remove_css_class("jp-hidden")
    GLib.idle_add(lambda: (revealer.set_reveal_child(True), False)[1])


def hide_animated(revealer: Gtk.Revealer, then):
    """The panel slides out, then then() runs (hide or close the window)."""
    revealer.get_child().add_css_class("jp-hidden")
    revealer.set_reveal_child(False)
    GLib.timeout_add(ANIMATION_MS + 20, lambda: (then(), False)[1])
