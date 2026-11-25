"""The "Welcome back" animation after you sign in with Jace (or switch accounts), like Jace's own:
the screen dims and blurs, your picture pops in, a green ring draws around it, the Jace apps pop
in and circle it, and "Welcome back, <name>" rises in, with a whoosh and a chime. A click or any
key skips it; it's gone after 2.8 seconds. With animations turned off, it's a still card.

    welcome.show(app, name, picture_path, first_time=False, then=callback)

It's drawn with Cairo on a full-screen overlay (gtk4-layer-shell; jace-signin preloads it).
"""
import math
import os
import struct
import subprocess
import time
import wave

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from jaceos import JACE_APPS  # noqa: E402

ACCENT = (0x3d / 255, 0xdc / 255, 0x84 / 255)
SHOW = 2.8            # seconds on screen
FADE = 0.45           # fading out
ICONS = [cmd for _pkg, cmd, _name, _about in JACE_APPS] + ["jace-browser", "jace-settings"]


def _ease_pop(t):
    """0→1 with a little overshoot (Jace's cubic-bezier(.2,.9,.25,1.15))."""
    t = min(max(t, 0.0), 1.0)
    return 1 - (1 - t) ** 3 + 0.15 * math.sin(math.pi * t) * (1 - t)


def _ease_out(t):
    t = min(max(t, 0.0), 1.0)
    return 1 - (1 - t) ** 3


def _span(now, start, length):
    return min(max((now - start) / length, 0.0), 1.0)


def _pixbuf(path, size):
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, False)
    except (GLib.Error, TypeError):
        return None


def _icon(name, size):
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    if not theme.has_icon(name):
        return None
    f = theme.lookup_icon(name, None, size, 1, Gtk.TextDirection.NONE, 0).get_file()
    return _pixbuf(f.get_path(), size) if f and f.get_path() else None


def _rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


# --- the sound: a whoosh, then a two-note chime (E6, B6), made here, no files needed -------------------

def _sound_file() -> str | None:
    path = os.path.join(GLib.get_user_cache_dir(), "jace-os", "welcome.wav")
    if os.path.exists(path):
        return path
    rate, length = 44100, 2.2
    n = int(rate * length)
    import random
    rnd = random.Random(7)
    samples = [0.0] * n
    # Whoosh: noise through a band-pass whose centre sweeps 400 → 2400 Hz over 0.7 s.
    x1 = x2 = y1 = y2 = 0.0
    for i in range(int(rate * 0.75)):
        t = i / rate
        f = 400 * (6 ** (t / 0.7))
        w = 2 * math.pi * f / rate
        q = 1.2
        alpha = math.sin(w) / (2 * q)
        a0 = 1 + alpha
        x = rnd.uniform(-1, 1)
        y = (alpha * x - alpha * x2 + 2 * math.cos(w) * y1 - (1 - alpha) * y2) / a0
        x2, x1 = x1, x
        y2, y1 = y1, y
        env = math.sin(math.pi * min(t / 0.75, 1)) ** 2
        samples[i] += 0.35 * y * env
    # Chime: E6 at 1.15 s and B6 at 1.3 s, each with a quiet overtone, ringing out.
    for start, f in ((0.80, 1318.5), (0.95, 1975.5)):
        s0 = int(rate * start)
        for i in range(int(rate * 1.2)):
            if s0 + i >= n:
                break
            t = i / rate
            env = min(t / 0.008, 1) * math.exp(-t * 3.2)
            samples[s0 + i] += 0.16 * env * (math.sin(2 * math.pi * f * t) + 0.25 * math.sin(2 * math.pi * f * 2.01 * t))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with wave.open(path + ".tmp", "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 30000)) for s in samples))
        os.replace(path + ".tmp", path)
        return path
    except OSError:
        return None


def _play_sound():
    path = _sound_file()
    if not path:
        return
    for player in ("pw-play", "paplay"):
        try:
            subprocess.Popen([player, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            continue


# --- the overlay ---------------------------------------------------------------------------------------

class Welcome(Gtk.Window):
    def __init__(self, app, name, picture, first_time=False, then=None):
        super().__init__(application=app, title="Welcome")
        from jaceos.popup import make_layer
        self.layer = make_layer(self, "jace-welcome", fallback_size=(900, 640))
        self.add_css_class("jace-welcome")
        css = Gtk.CssProvider()
        css.load_from_string(".jace-welcome { background: transparent; }")
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.name = name or "Jace"
        self.greeting = "Welcome to Jace," if first_time else "Welcome back,"
        self.then = then
        self.closing_at = None
        self.done = False
        self.still = not Gtk.Settings.get_default().get_property("gtk-enable-animations")
        self.avatar = _pixbuf(picture, 112) if picture else None
        self.icons = [p for p in (_icon(n, 44) for n in ICONS) if p][:8]

        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.area.set_draw_func(self.draw)
        self.set_child(self.area)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda *_: self.finish())
        self.area.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda *_: (self.finish(), True)[1])
        self.add_controller(keys)

        self.start = time.monotonic()
        self.area.add_tick_callback(self.tick)
        GLib.timeout_add(int(SHOW * 1000), lambda: (self.finish(), False)[1])
        if not self.still:
            _play_sound()

    def tick(self, _widget, _clock):
        self.area.queue_draw()
        if self.closing_at and time.monotonic() - self.closing_at >= FADE:
            self.close_now()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def finish(self):
        if self.closing_at is None:
            self.closing_at = time.monotonic()

    def close_now(self):
        if self.done:
            return
        self.done = True
        self.close()
        if self.then:
            self.then()

    def draw(self, _area, cr, width, height):
        now = 10.0 if self.still else time.monotonic() - self.start
        fade_out = 1.0
        if self.closing_at:
            fade_out = 1 - _span(time.monotonic(), self.closing_at, FADE)
        cx, cy = width / 2, height / 2 - 30

        # The backdrop: dims in over 0.35 s.
        cr.set_source_rgba(0.05, 0.06, 0.08, 0.82 * _ease_out(_span(now, 0, 0.35)) * fade_out)
        cr.paint()
        cr.push_group()

        # The ring draws itself around the picture.
        ring = _ease_out(_span(now, 0.35, 0.9))
        if ring > 0:
            cr.set_line_width(4)
            cr.set_line_cap(1)          # round
            cr.set_source_rgba(*ACCENT, 0.18)
            cr.arc(cx, cy, 70, 0, 2 * math.pi)
            cr.stroke()
            cr.set_source_rgb(*ACCENT)
            cr.arc(cx, cy, 70, -math.pi / 2, -math.pi / 2 + 2 * math.pi * ring)
            cr.stroke()

        # The picture pops in (scale 0.4 → 1).
        pop = _span(now, 0, 0.6)
        if pop > 0:
            scale = 0.4 + 0.6 * _ease_pop(pop)
            cr.save()
            cr.translate(cx, cy)
            cr.scale(scale, scale)
            cr.arc(0, 0, 56, 0, 2 * math.pi)
            cr.clip()
            if self.avatar:
                Gdk.cairo_set_source_pixbuf(cr, self.avatar, -56, -56)
                cr.paint_with_alpha(min(pop * 2, 1))
            else:
                cr.set_source_rgb(*ACCENT)
                cr.paint_with_alpha(min(pop * 2, 1))
                self.text(cr, self.name[:1].upper(), 0, 0, 44, (0.07, 0.08, 0.09), bold=True, center_y=True)
            cr.restore()

        # The Jace apps pop in one by one and circle the picture (each stays upright).
        spin = (now / 24.0) * 2 * math.pi
        for i, pix in enumerate(self.icons):
            p = _span(now, 0.45 + i * 0.09, 0.5)
            if p <= 0:
                continue
            angle = spin + i * 2 * math.pi / len(self.icons) - math.pi / 2
            x, y = cx + 118 * math.cos(angle), cy + 118 * math.sin(angle)
            s = _ease_pop(p)
            cr.save()
            cr.translate(x, y)
            cr.scale(s, s)
            _rounded(cr, -22, -22, 44, 44, 11)
            cr.clip()
            Gdk.cairo_set_source_pixbuf(cr, pix, -22, -22)
            cr.paint_with_alpha(min(p * 2, 1))
            cr.restore()

        # "Welcome back," then your name rise in.
        for start, text, size, colour, bold, dy in (
                (0.95, self.greeting, 17, (0.75, 0.78, 0.82), False, 172),
                (1.10, self.name, 30, (1, 1, 1), True, 206)):
            t = _ease_out(_span(now, start, 0.6))
            if t > 0:
                cr.save()
                self.text(cr, text, cx, cy + dy + 8 * (1 - t), size, colour, bold, alpha=t)
                cr.restore()

        cr.pop_group_to_source()
        cr.paint_with_alpha(fade_out)

    @staticmethod
    def text(cr, text, x, y, size, colour, bold=False, alpha=1.0, center_y=False):
        layout = PangoCairo.create_layout(cr)
        font = Pango.FontDescription.from_string(f"Sans {'Bold' if bold else ''} {size}")
        layout.set_font_description(font)
        layout.set_text(text, -1)
        w, h = layout.get_pixel_size()
        cr.move_to(x - w / 2, y - (h / 2 if center_y else 0))
        cr.set_source_rgba(*colour, alpha)
        PangoCairo.show_layout(cr, layout)


def show(app, name, picture=None, first_time=False, then=None) -> Welcome:
    w = Welcome(app, name, picture, first_time, then)
    w.present()
    return w


def main():
    """jace-signin --welcome-demo: shows the animation for the account that's signed in."""
    from jaceos import account
    app = Gtk.Application(application_id=None)

    def activate(a):
        acct = account.load()
        pic = account.avatar_path()
        a.hold()
        show(a, account.display_name(acct) or "Jace", str(pic) if pic else None, then=a.release)
    app.connect("activate", activate)
    app.run([])
