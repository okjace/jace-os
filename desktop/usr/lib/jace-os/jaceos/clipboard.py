"""The Jace OS clipboard manager: press Win+V (or click the clipboard in the top bar).

Everything you copy is kept by cliphist (started at sign-in, see the labwc autostart).
The pop-up shows it newest first, with pictures previewed. Click an item, or press Enter,
to paste it into the app you were using; pin items to keep them at the top for good;
search; delete one; or clear everything. Settings → Keyboard & mouse → Clipboard turns
history on or off.

Pins are kept in ~/.local/share/jace-os/clipboard-pins/.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from jaceos import config_dir, data_dir, load_css, which  # noqa: E402
from jaceos.popup import (animated, hide_animated, make_layer, outside_click, place,  # noqa: E402
                          show_animated, strip_preload)

strip_preload()

MAX_ITEMS = 100


# --- history (cliphist) and pins --------------------------------------------------------------------

def history_on() -> bool:
    return not (config_dir() / "clipboard-off").exists()


def set_history(on: bool):
    flag = config_dir() / "clipboard-off"
    if on:
        flag.unlink(missing_ok=True)
        if not watching():
            subprocess.Popen(["wl-paste", "--watch", "cliphist", "store"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        flag.touch()
        subprocess.run(["pkill", "-f", "wl-paste --watch cliphist store"], capture_output=True)


def watching() -> bool:
    return subprocess.run(["pgrep", "-f", "wl-paste --watch cliphist store"], capture_output=True).returncode == 0


def history() -> list[dict]:
    """[{"line": raw cliphist line, "text": preview, "image": bool}] newest first."""
    if not which("cliphist"):
        return []
    try:
        out = subprocess.run(["cliphist", "list"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    items = []
    for line in out.splitlines()[:MAX_ITEMS]:
        _id, _, preview = line.partition("\t")
        items.append({"line": line, "text": preview, "image": preview.startswith("[[ binary data")})
    return items


def decode(line: str) -> bytes:
    return subprocess.run(["cliphist", "decode"], input=line.encode(), capture_output=True, timeout=5).stdout


def delete(line: str):
    subprocess.run(["cliphist", "delete"], input=line.encode(), capture_output=True, timeout=5)


def clear():
    subprocess.run(["cliphist", "wipe"], capture_output=True, timeout=5)


def _pins_dir() -> Path:
    d = data_dir() / "clipboard-pins"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pins() -> list[dict]:
    out = []
    for meta in sorted(_pins_dir().glob("*.json"), reverse=True):
        try:
            info = json.loads(meta.read_text())
        except (OSError, ValueError):
            continue
        info["file"] = str(meta.with_suffix(".bin"))
        if Path(info["file"]).exists():
            out.append(info)
    return out


def pin(data: bytes, preview: str, image: bool):
    stem = _pins_dir() / f"{int(time.time() * 1000)}"
    stem.with_suffix(".bin").write_bytes(data)
    stem.with_suffix(".json").write_text(json.dumps({"text": preview, "image": image}))


def unpin(item: dict):
    Path(item["file"]).unlink(missing_ok=True)
    Path(item["file"]).with_suffix(".json").unlink(missing_ok=True)


def copy(data: bytes):
    subprocess.run(["wl-copy"], input=data, timeout=5)


def paste_soon():
    """Pastes into the app you were using once the pop-up has closed (Ctrl+V)."""
    if which("wtype") and (config_dir() / "clipboard-no-autopaste").exists() is False:
        subprocess.Popen(["sh", "-c", "sleep 0.45; wtype -M ctrl -k v -m ctrl"], start_new_session=True)


# --- the pop-up -------------------------------------------------------------------------------------

class ClipboardWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Clipboard", css_classes=["jm-window"])
        make_layer(self, "jace-clipboard", (440, 560))
        backdrop = Gtk.Overlay(css_classes=["jm-backdrop"])
        backdrop.set_child(Gtk.Box(hexpand=True, vexpand=True))
        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["jm-panel", "jc-panel"],
                             width_request=440, height_request=560)
        self.revealer = animated(self.panel)
        place(self.revealer, "top-center")
        backdrop.add_overlay(self.revealer)
        outside_click(backdrop, lambda: self.panel, self.dismiss)

        head = Gtk.Box(spacing=8)
        head.append(Gtk.Label(label="Clipboard", css_classes=["jm-heading"], xalign=0, hexpand=True))
        clear_b = Gtk.Button(label="Clear all", css_classes=["flat", "jm-pill"])
        clear_b.connect("clicked", self._clear)
        head.append(clear_b)
        self.panel.append(head)

        self.search = Gtk.SearchEntry(placeholder_text="Search what you copied", css_classes=["jm-search"])
        self.search.connect("search-changed", lambda *_: self.list.invalidate_filter())
        self.search.connect("activate", lambda *_: self._activate_first())
        self.panel.append(self.search)

        self.list = Gtk.ListBox(css_classes=["jc-list"], selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.set_filter_func(self._filter)
        self.list.connect("row-activated", lambda _l, row: self._use(row))
        self.panel.append(Gtk.ScrolledWindow(child=self.list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.panel.append(Gtk.Label(label="Click to paste · Enter pastes the first match · Esc closes",
                                    css_classes=["dim-label", "caption"]))

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.set_child(backdrop)
        self.fill()

    def dismiss(self):
        """Slides the panel away, then closes the pop-up."""
        if getattr(self, "_closing", False):
            return
        self._closing = True
        hide_animated(self.revealer, self.close)

    def fill(self):
        self.list.remove_all()
        if not history_on():
            row = Gtk.ListBoxRow(activatable=False)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=30)
            box.append(Gtk.Label(label="Clipboard history is off", css_classes=["title-4"]))
            b = Gtk.Button(label="Turn it on", halign=Gtk.Align.CENTER, css_classes=["suggested-action", "pill"])
            b.connect("clicked", lambda *_: (set_history(True), self.fill()))
            box.append(b)
            row.set_child(box)
            self.list.append(row)
            return
        for item in pins():
            self.list.append(self._row(item, pinned=True))
        for item in history():
            self.list.append(self._row(item, pinned=False))
        if self.list.get_first_child() is None:
            self.list.append(Gtk.ListBoxRow(child=Gtk.Label(label="Nothing copied yet. Copy something with Ctrl+C.",
                                                            css_classes=["dim-label"], margin_top=30),
                                            activatable=False))

    def _row(self, item, pinned):
        row = Gtk.ListBoxRow(css_classes=["jc-row"])
        row.item, row.pinned = item, pinned
        row.search_text = item["text"].lower()
        box = Gtk.Box(spacing=10, margin_top=6, margin_bottom=6, margin_start=8, margin_end=6)
        if item["image"]:
            data = Path(item["file"]).read_bytes() if pinned else decode(item["line"])
            try:
                pic = Gtk.Picture.new_for_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(data)))
                pic.set_content_fit(Gtk.ContentFit.CONTAIN)
                pic.set_size_request(-1, 72)
                pic.set_hexpand(True)
                pic.set_halign(Gtk.Align.START)
                box.append(pic)
            except GLib.Error:
                box.append(Gtk.Label(label="Picture", xalign=0, hexpand=True))
        else:
            text = item["text"].strip() or " "
            label = Gtk.Label(label=text, xalign=0, hexpand=True, wrap=True, lines=3,
                              ellipsize=Pango.EllipsizeMode.END, wrap_mode=Pango.WrapMode.WORD_CHAR)
            box.append(label)
        pin_b = Gtk.Button(icon_name="view-pin-symbolic", valign=Gtk.Align.START,
                           css_classes=["flat", "circular"] + (["jc-pinned"] if pinned else []),
                           tooltip_text="Unpin" if pinned else "Pin (keep it at the top)")
        pin_b.connect("clicked", lambda *_: self._toggle_pin(row))
        del_b = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.START, css_classes=["flat", "circular"],
                           tooltip_text="Delete")
        del_b.connect("clicked", lambda *_: self._delete(row))
        box.append(pin_b)
        box.append(del_b)
        row.set_child(box)
        return row

    def _filter(self, row):
        q = self.search.get_text().strip().lower()
        return not q or q in getattr(row, "search_text", "")

    def _data(self, row) -> bytes:
        return Path(row.item["file"]).read_bytes() if row.pinned else decode(row.item["line"])

    def _use(self, row):
        if not hasattr(row, "item"):
            return
        copy(self._data(row))
        self.dismiss()
        paste_soon()

    def _activate_first(self):
        child = self.list.get_first_child()
        while child is not None:
            if child.get_child_visible() and hasattr(child, "item"):
                self._use(child)
                return
            child = child.get_next_sibling()

    def _toggle_pin(self, row):
        if row.pinned:
            unpin(row.item)
        else:
            pin(self._data(row), row.item["text"], row.item["image"])
        self.fill()

    def _delete(self, row):
        if row.pinned:
            unpin(row.item)
        else:
            delete(row.item["line"])
        self.fill()

    def _clear(self, *_):
        clear()
        self.fill()

    def _key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.dismiss()
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            return False
        if not self.search.has_focus() and Gdk.keyval_to_unicode(keyval):
            self.search.grab_focus()
        return False


class ClipboardApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.jaceos.Clipboard", flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        load_css()

    def do_command_line(self, _cmdline):
        win = self.get_active_window()
        if win is not None:      # pressing Win+V again closes it
            win.dismiss()
            return 0
        win = ClipboardWindow(self)
        win.present()
        show_animated(win.revealer)
        win.search.grab_focus()
        return 0


def main():
    sys.exit(ClipboardApp().run(sys.argv))
