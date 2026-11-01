"""Profile pictures you can pick or build (Settings → Users → Picture).

A picture is a design: a background (one colour, or a gradient of two) with an emoji, or up
to two letters, in the middle. PRESETS are ready-made designs; the builder lets you make
your own. render() draws one as a 512 px PNG.
"""
import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo  # noqa: E402

COLORS = ["#3ddc84", "#1f9d5a", "#2bb6a3", "#4f8ef7", "#6c5ce7", "#a55eea", "#e84393", "#ff6b6b",
          "#ff9f43", "#f5c451", "#8b919c", "#1c1f26"]

PRESETS = [
    {"bg": ["#3ddc84", "#1f9d5a"], "emoji": "🐸"},
    {"bg": ["#4f8ef7", "#6c5ce7"], "emoji": "🚀"},
    {"bg": ["#ff9f43", "#ff6b6b"], "emoji": "🦊"},
    {"bg": ["#f5c451", "#ff9f43"], "emoji": "🐱"},
    {"bg": ["#2bb6a3", "#4f8ef7"], "emoji": "🐬"},
    {"bg": ["#a55eea", "#e84393"], "emoji": "🦄"},
    {"bg": ["#1c1f26", "#2a2f38"], "emoji": "🎮"},
    {"bg": ["#6c5ce7", "#1c1f26"], "emoji": "🌙"},
    {"bg": ["#3ddc84", "#2bb6a3"], "emoji": "🌵"},
    {"bg": ["#ff6b6b", "#e84393"], "emoji": "🍓"},
    {"bg": ["#4f8ef7", "#2bb6a3"], "emoji": "🌊"},
    {"bg": ["#8b919c", "#1c1f26"], "emoji": "🐺"},
    {"bg": ["#f5c451", "#3ddc84"], "emoji": "🌻"},
    {"bg": ["#e84393", "#6c5ce7"], "emoji": "🎧"},
    {"bg": ["#1f9d5a", "#1c1f26"], "emoji": "⛏️"},
    {"bg": ["#ff9f43", "#f5c451"], "emoji": "🍕"},
]


def _rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def render(design: dict, path: str, size: int = 512):
    """design: {"bg": [color] or [color, color], "emoji": "🐸"} or {"bg": [...], "text": "JC"}."""
    import cairo
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    bg = design.get("bg") or ["#3ddc84"]
    if len(bg) > 1:
        grad = cairo.LinearGradient(0, 0, size, size)
        grad.add_color_stop_rgb(0, *_rgb(bg[0]))
        grad.add_color_stop_rgb(1, *_rgb(bg[1]))
        cr.set_source(grad)
    else:
        cr.set_source_rgb(*_rgb(bg[0]))
    cr.paint()

    text = design.get("emoji") or (design.get("text") or "").strip()[:2].upper() or "?"
    emoji = bool(design.get("emoji"))
    layout = PangoCairo.create_layout(cr)
    font = Pango.FontDescription.from_string(("Noto Color Emoji" if emoji else "Inter Bold") + " 10")
    font.set_absolute_size(size * (0.5 if emoji else (0.36 if len(text) > 1 else 0.46)) * Pango.SCALE)
    layout.set_font_description(font)
    layout.set_text(text, -1)
    _ink, logical = layout.get_pixel_extents()
    if not emoji:
        # White letters on dark or coloured backgrounds; dark letters on light ones.
        r, g, b = _rgb(bg[0])
        light = 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.6
        cr.set_source_rgb(*((0.05, 0.08, 0.06) if light else (1, 1, 1)))
    cr.move_to((size - logical.width) / 2 - logical.x, (size - logical.height) / 2 - logical.y)
    PangoCairo.show_layout(cr, layout)
    surface.write_to_png(path)
