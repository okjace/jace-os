"""The Jace OS start menu: tap the Windows (Super) key or click J on the top bar.

Pinned apps on top, every app a click away, search as you type, your Jace
account and the power buttons at the bottom, like the Windows start menu.
Right-click an app to pin, unpin or hide it. Settings → Start menu changes
where it opens, its layout and size, and which apps are pinned or hidden; that
lives in ~/.config/jace-os/menu.json.

It stays running in the background (started at login with --daemon), so it
opens instantly. Running jace-menu again while it's open closes it.
"""
import json
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell
except (ValueError, ImportError):
    LayerShell = None
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from jaceos import account, config_dir, load_css, spawn  # noqa: E402

from jaceos.popup import animated, hide_animated, show_animated, strip_preload  # noqa: E402

strip_preload()   # apps started from the menu must not inherit gtk4-layer-shell

DEFAULTS = {
    "position": "top-left",        # top-left | top-center | center
    "layout": "grid",              # grid | list (how pinned apps are shown)
    "size": "normal",              # compact | normal | large
    "columns": 6,
    "show_labels": True,
    "show_account": True,
    "start_on_all_apps": False,
    "opacity": 0.94,
    "pinned": [
        "jace-browser.desktop",
        "jace-launcher.desktop",
        "jace-social.desktop",
        "jace-store.desktop",
        "org.gnome.Nautilus.desktop",
        "org.gnome.Ptyxis.desktop",
        "jace-settings.desktop",
        "org.gnome.TextEditor.desktop",
        "org.gnome.Loupe.desktop",
        "org.gnome.Calculator.desktop",
        "org.gnome.Showtime.desktop",
        "org.gnome.SystemMonitor.desktop",
    ],
    "hidden": [],
    "shown": [],    # helper tools you chose to show again (see SYSTEM_HIDDEN)
}

SIZES = {  # icon size, menu width, menu height
    "compact": (32, 520, 560),
    "normal": (44, 640, 680),
    "large": (56, 780, 800),
}


def _file():
    return config_dir() / "menu.json"


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        cfg.update(json.loads(_file().read_text()))
    except (OSError, ValueError):
        pass
    # Pins of apps Jace OS replaced move to the new app once the old one is gone.
    replaced = {"thunar.desktop": "org.gnome.Nautilus.desktop", "foot.desktop": "org.gnome.Ptyxis.desktop",
                "mpv.desktop": "org.gnome.Showtime.desktop", "zen.desktop": "jace-browser.desktop"}
    pinned = []
    for app_id in cfg.get("pinned", []):
        if app_id in replaced and app_by_id(app_id) is None:
            app_id = replaced[app_id]
        if app_id not in pinned:
            pinned.append(app_id)
    cfg["pinned"] = pinned
    return cfg


def save_config(cfg: dict):
    _file().write_text(json.dumps(cfg, indent=2))
    # Tell a running menu to pick up the change.
    try:
        Gio.bus_get_sync(Gio.BusType.SESSION).call_sync(
            "org.jaceos.Menu", "/org/jaceos/Menu", "org.gtk.Actions", "Activate",
            GLib.Variant("(sava{sv})", ("reload", [], {})), None, Gio.DBusCallFlags.NONE, 500)
    except GLib.Error:
        pass


# Tools that Settings already covers, or that are for developers and system admins. They stay
# installed and work; they're just not in the start menu unless you show them again
# (Settings → Start menu → Hidden apps).
SYSTEM_HIDDEN = {
    # Settings covers these
    "nm-connection-editor.desktop", "blueman-manager.desktop", "blueman-adapters.desktop",
    "network.cycles.wdisplays.desktop", "wdisplays.desktop", "org.pulseaudio.pavucontrol.desktop",
    "pavucontrol.desktop", "jace-updates.desktop", "jace-appimage.desktop",
    # Opened from other apps when needed
    "org.gnome.FileRoller.desktop", "thunar-bulk-rename.desktop", "thunar-settings.desktop",
    "thunar-volman-settings.desktop", "foot-server.desktop", "footclient.desktop",
    # Developer and system tools
    "lstopo.desktop", "qv4l2.desktop", "qvidcap.desktop", "xgps.desktop", "xgpsspeed.desktop",
    "cups.desktop", "avahi-discover.desktop", "bssh.desktop", "bvnc.desktop", "qt5ct.desktop",
    "qt6ct.desktop", "cmake-gui.desktop", "assistant.desktop", "designer.desktop", "linguist.desktop",
    "qdbusviewer.desktop", "electron.desktop", "uxterm.desktop", "xterm.desktop", "htop.desktop",
    "btop.desktop", "nvim.desktop", "vim.desktop", "gvim.desktop", "rofi.desktop", "rofi-theme-selector.desktop",
}


def hidden_ids(cfg: dict | None) -> set[str]:
    cfg = cfg or {}
    return (SYSTEM_HIDDEN - set(cfg.get("shown", []))) | set(cfg.get("hidden", []))


def all_apps(cfg: dict | None = None) -> list[Gio.AppInfo]:
    hidden = hidden_ids(cfg)
    apps = [a for a in Gio.AppInfo.get_all() if a.should_show() and a.get_id() not in hidden]
    return sorted(apps, key=lambda a: a.get_display_name().lower())


def app_by_id(app_id: str) -> Gio.AppInfo | None:
    try:
        return Gio.DesktopAppInfo.new(app_id)
    except TypeError:
        return None


class AppButton(Gtk.Button):
    def __init__(self, menu: "MenuWindow", app: Gio.AppInfo, icon_size: int, vertical: bool, show_label=True):
        super().__init__(css_classes=["flat", "jm-app"])
        self.menu, self.app = menu, app
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                      spacing=6 if vertical else 12)
        icon = Gtk.Image.new_from_gicon(app.get_icon() or Gio.ThemedIcon.new("application-x-executable"))
        icon.set_pixel_size(icon_size)
        box.append(icon)
        if show_label or not vertical:
            label = Gtk.Label(label=app.get_display_name(), ellipsize=Pango.EllipsizeMode.END,
                              max_width_chars=12 if vertical else 40, xalign=0.5 if vertical else 0)
            if vertical:
                label.set_lines(2)
                label.set_wrap(True)
                label.set_justify(Gtk.Justification.CENTER)
            box.append(label)
        self.set_child(box)
        self.set_tooltip_text(app.get_description() or app.get_display_name())
        self.connect("clicked", lambda *_: menu.launch(app))

        click = Gtk.GestureClick(button=3)
        click.connect("pressed", self._context)
        self.add_controller(click)

    def _context(self, *_):
        app_id = self.app.get_id()
        pinned = app_id in self.menu.cfg["pinned"]
        m = Gio.Menu()
        m.append("Unpin from Start" if pinned else "Pin to Start", f"menu.pin::{app_id}")
        if pinned:
            m.append("Move to front", f"menu.front::{app_id}")
        m.append("Edit…", f"menu.edit::{app_id}")
        m.append("Hide from the menu", f"menu.hide::{app_id}")
        pop = Gtk.PopoverMenu.new_from_model(m)
        pop.set_parent(self)
        pop.set_has_arrow(False)
        pop.popup()


class MenuWindow(Gtk.ApplicationWindow):
    def __init__(self, app: "MenuApp"):
        super().__init__(application=app, title="Start", css_classes=["jm-window"])
        self.cfg = load_config()
        self.set_decorated(False)
        if LayerShell and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            LayerShell.set_namespace(self, "jace-menu")
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.EXCLUSIVE)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(self, edge, True)
            LayerShell.set_exclusive_zone(self, -1)
        else:
            # Not on a layer-shell compositor (e.g. testing elsewhere): a normal window.
            self.set_default_size(*SIZES[self.cfg["size"]][1:])

        for name, fn in (("pin", self._toggle_pin), ("front", self._move_front), ("hide", self._hide),
                         ("edit", self._edit)):
            act = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            act.connect("activate", fn)
            self.add_action(act)
        group = Gio.SimpleActionGroup()
        for name in ("pin", "front", "hide", "edit"):
            group.add_action(self.lookup_action(name))
        self.insert_action_group("menu", group)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.build()

    # --- layout ----------------------------------------------------------------------------------

    def build(self):
        self.cfg = load_config()
        icon, width, height = SIZES.get(self.cfg["size"], SIZES["normal"])
        self.icon_size = icon

        # A full-screen transparent layer: clicking anywhere outside the menu closes it.
        backdrop = Gtk.Overlay(css_classes=["jm-backdrop"])
        outside = Gtk.GestureClick()
        outside.connect("pressed", self._clicked_outside)
        backdrop.add_controller(outside)
        backdrop.set_child(Gtk.Box(hexpand=True, vexpand=True))

        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, css_classes=["jm-panel"],
                             width_request=width, height_request=height)
        self.panel.set_opacity(1)
        pos = self.cfg["position"]
        # The panel slides and fades in (popup.animated); the revealer is what gets placed.
        self.revealer = animated(self.panel)
        self.revealer.set_halign(Gtk.Align.START if pos == "top-left" else Gtk.Align.CENTER)
        self.revealer.set_valign(Gtk.Align.CENTER if pos == "center" else Gtk.Align.START)
        self.revealer.set_margin_top(48 if pos != "center" else 0)
        self.revealer.set_margin_start(8 if pos == "top-left" else 0)
        backdrop.add_overlay(self.revealer)
        alpha = max(0.6, min(1.0, float(self.cfg.get("opacity", 0.94))))
        self._panel_css = Gtk.CssProvider()
        self._panel_css.load_from_string(f".jm-panel {{ background-color: rgba(22, 24, 29, {alpha}); }}")
        self.panel.get_style_context().add_provider(self._panel_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

        self.search = Gtk.SearchEntry(placeholder_text="Search apps, settings and files", css_classes=["jm-search"])
        self.search.connect("search-changed", lambda *_: self.refresh())
        self.search.connect("activate", self._launch_first)
        self.panel.append(self.search)

        header = Gtk.Box(spacing=6)
        self.title = Gtk.Label(label="Pinned", xalign=0, hexpand=True, css_classes=["jm-heading"])
        self.toggle = Gtk.Button(label="All apps  ›", css_classes=["flat", "jm-pill"])
        self.toggle.connect("clicked", lambda *_: self.show_all(not self.showing_all))
        header.append(self.title)
        header.append(self.toggle)
        self.panel.append(header)

        self.scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.panel.append(self.scroller)

        footer = Gtk.Box(spacing=8, css_classes=["jm-footer"])
        self.account_button = Gtk.Button(css_classes=["flat", "jm-account"], hexpand=True)
        self.account_button.connect("clicked", lambda *_: self.run(["jace-settings", "--page", "account"]))
        footer.append(self.account_button)
        for icon_name, tip, cmd in (
            ("folder-symbolic", "Files", ["nautilus", "--new-window"]),
            ("emblem-system-symbolic", "Settings", ["jace-settings"]),
            ("system-shutdown-symbolic", "Power", ["jace-power"]),
        ):
            b = Gtk.Button(icon_name=icon_name, tooltip_text=tip, css_classes=["flat", "circular", "jm-round"])
            b.connect("clicked", lambda _b, c=cmd: self.run(c))
            footer.append(b)
        self.panel.append(footer)

        self.set_child(backdrop)
        self.showing_all = bool(self.cfg.get("start_on_all_apps"))
        self.update_account()
        self.refresh()

    def update_account(self):
        box = Gtk.Box(spacing=10)
        acct = account.load() if self.cfg.get("show_account", True) else None
        av = Adw.Avatar(size=32, show_initials=True, text=account.display_name(acct) or "Jace")
        pic = account.avatar_path()
        if acct and pic:
            try:
                av.set_custom_image(Gdk.Texture.new_from_filename(str(pic)))
            except GLib.Error:
                pass
        box.append(av)
        box.append(Gtk.Label(label=account.display_name(acct) if acct else "Sign in with Jace", xalign=0))
        self.account_button.set_child(box)
        self.account_button.set_visible(self.cfg.get("show_account", True))

    def show_all(self, on: bool):
        self.showing_all = on
        self.refresh()

    def refresh(self):
        query = self.search.get_text().strip().lower()
        if query:
            self.title.set_label("Best matches")
            self.toggle.set_visible(False)
            apps = [a for a in all_apps(self.cfg) if self._matches(a, query)]
            self.scroller.set_child(self._list(apps[:30]))
            return
        self.toggle.set_visible(True)
        if self.showing_all:
            self.title.set_label("All apps")
            self.toggle.set_label("‹  Back")
            self.scroller.set_child(self._list(all_apps(self.cfg), letters=True))
        else:
            self.title.set_label("Pinned")
            self.toggle.set_label("All apps  ›")
            pinned = [a for a in (app_by_id(i) for i in self.cfg["pinned"]) if a]
            if self.cfg["layout"] == "list":
                self.scroller.set_child(self._list(pinned))
            else:
                self.scroller.set_child(self._grid(pinned))

    @staticmethod
    def _matches(app: Gio.AppInfo, q: str) -> bool:
        hay = " ".join(filter(None, [app.get_display_name(), app.get_name(), app.get_description() or "",
                                      app.get_executable() or "",
                                      " ".join(app.get_keywords() or []) if hasattr(app, "get_keywords") else ""]))
        return all(word in hay.lower() for word in q.split())

    def _grid(self, apps):
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, valign=Gtk.Align.START,
                           max_children_per_line=int(self.cfg.get("columns", 6)),
                           min_children_per_line=int(self.cfg.get("columns", 6)),
                           row_spacing=4, column_spacing=4)
        for a in apps:
            flow.append(AppButton(self, a, self.icon_size, True, self.cfg.get("show_labels", True)))
        if not apps:
            return Adw.StatusPage(icon_name="view-pin-symbolic", title="Nothing pinned",
                                  description="Right-click an app in All apps and choose Pin to Start.")
        return flow

    def _list(self, apps, letters=False):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        last = None
        for a in apps:
            first = a.get_display_name()[:1].upper()
            if letters and first != last:
                last = first
                box.append(Gtk.Label(label=first if first.isalpha() else "#", xalign=0, css_classes=["jm-letter"]))
            box.append(AppButton(self, a, max(24, self.icon_size - 12), False))
        if not apps:
            box.append(Gtk.Label(label="No apps match that.", css_classes=["dim-label"], margin_top=24))
        return box

    # --- actions ---------------------------------------------------------------------------------

    def launch(self, app: Gio.AppInfo):
        try:
            ctx = Gdk.Display.get_default().get_app_launch_context()
            ctx.unsetenv("LD_PRELOAD")
            app.launch([], ctx)
        except GLib.Error:
            pass
        self.close_menu()

    def run(self, cmd):
        spawn(cmd)
        self.close_menu()

    def _launch_first(self, *_):
        child = self.scroller.get_child()
        if isinstance(child, Gtk.Viewport):
            child = child.get_child()
        if child is None:
            return
        first = child.get_first_child()
        while first is not None and not isinstance(first, AppButton):
            first = first.get_first_child() if isinstance(first, Gtk.FlowBoxChild) else first.get_next_sibling()
        if isinstance(first, AppButton):
            self.launch(first.app)

    def _toggle_pin(self, _a, value):
        app_id = value.get_string()
        if app_id in self.cfg["pinned"]:
            self.cfg["pinned"].remove(app_id)
        else:
            self.cfg["pinned"].append(app_id)
        save_config(self.cfg)
        self.refresh()

    def _move_front(self, _a, value):
        app_id = value.get_string()
        self.cfg["pinned"] = [app_id] + [i for i in self.cfg["pinned"] if i != app_id]
        save_config(self.cfg)
        self.refresh()

    def _edit(self, _a, value):
        self.run(["jace-settings", "--page", "menu", "--edit", value.get_string()])

    def _hide(self, _a, value):
        app_id = value.get_string()
        if app_id not in self.cfg["hidden"]:
            self.cfg["hidden"].append(app_id)
        self.cfg["pinned"] = [i for i in self.cfg["pinned"] if i != app_id]
        save_config(self.cfg)
        self.refresh()

    def _key(self, _ctrl, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            if self.search.get_text():
                self.search.set_text("")
            else:
                self.close_menu()
            return True
        # Start typing anywhere to search, like Windows.
        if not self.search.has_focus() and Gdk.keyval_to_unicode(keyval):
            self.search.grab_focus()
        return False

    def _clicked_outside(self, gesture, _n, x, y):
        ok, r = self.panel.compute_bounds(self.get_child())
        inside = ok and r.get_x() <= x <= r.get_x() + r.get_width() and r.get_y() <= y <= r.get_y() + r.get_height()
        if not inside:
            self.close_menu()

    def open_menu(self):
        self.search.set_text("")
        self.showing_all = bool(self.cfg.get("start_on_all_apps"))
        self.update_account()
        self.refresh()
        self.present()
        show_animated(self.revealer)
        self.search.grab_focus()

    def close_menu(self):
        if not self.get_visible():
            return
        hide_animated(self.revealer, lambda: None if self.revealer.get_reveal_child() else self.set_visible(False))


class MenuApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.jaceos.Menu", flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        reload = Gio.SimpleAction.new("reload", None)
        reload.connect("activate", lambda *_: self.window and self.window.build())
        self.add_action(reload)

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        load_css()
        self.hold()  # keep running in the background so the menu opens instantly

    def do_command_line(self, cmdline):
        args = cmdline.get_arguments()[1:]
        if self.window is None:
            self.window = MenuWindow(self)
            if "--daemon" in args:
                return 0
        if "--daemon" in args:
            return 0
        if self.window.get_visible():
            self.window.close_menu()
        else:
            self.window.open_menu()
        return 0


def main():
    sys.exit(MenuApp().run(sys.argv))
