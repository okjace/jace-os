"""Jace OS Settings: every setting in one place.

  jace-settings [--page <name>]

Pages: account, updates, security, shortcuts, bar, face, network, bluetooth, display, sound, appearance, menu,
windows, keyboard, power, datetime, apps, users, about.
"""
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from jaceos import (BACKGROUNDS, GITHUB, JACE_URL, LOGO, VERSION, WEBSITE, account, background,  # noqa: E402
                    config_dir, load_css, ok, os_release, run, spawn, updates, which)
from jaceos import menu as start_menu  # noqa: E402
from jaceos import antivirus, avatars, bar, clipboard, menu_edits, profile, shortcuts, store  # noqa: E402

PAGES = [
    # (id, title, icon)
    ("account", "Jace account", "avatar-default-symbolic"),
    ("updates", "Updates", "software-update-available-symbolic"),
    ("security", "Virus protection", "security-high-symbolic"),
    ("network", "Wi-Fi & network", "network-wireless-symbolic"),
    ("bluetooth", "Bluetooth", "bluetooth-symbolic"),
    ("display", "Display", "video-display-symbolic"),
    ("sound", "Sound", "audio-volume-high-symbolic"),
    ("appearance", "Appearance", "applications-graphics-symbolic"),
    ("menu", "Start menu", "view-app-grid-symbolic"),
    ("bar", "Top bar", "view-continuous-symbolic"),
    ("windows", "Windows & desktops", "preferences-desktop-apps-symbolic"),
    ("keyboard", "Keyboard & mouse", "input-keyboard-symbolic"),
    ("shortcuts", "Keyboard shortcuts", "preferences-desktop-keyboard-shortcuts-symbolic"),
    ("power", "Power & battery", "battery-good-symbolic"),
    ("datetime", "Date & time", "preferences-system-time-symbolic"),
    ("apps", "Apps", "application-x-addon-symbolic"),
    ("users", "Users", "system-users-symbolic"),
    ("face", "Face unlock", "camera-web-symbolic"),
    ("about", "About", "help-about-symbolic"),
]

LABWC_USER = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "labwc"


# --- small helpers --------------------------------------------------------------------------------

def plain_row(**props):
    """An ActionRow that shows text as-is: network, device and app names can contain & or <."""
    title, subtitle = props.pop("title", ""), props.pop("subtitle", "")
    row = Adw.ActionRow(use_markup=False, **props)
    # Set the text after use_markup, or GTK still parses it as markup while constructing.
    row.set_title(title)
    row.set_subtitle(subtitle or "")
    return row


def group(title="", description=None, *rows):
    g = Adw.PreferencesGroup(title=title)
    if description:
        g.set_description(description)
    for r in rows:
        g.add(r)
    return g


def button_row(title, subtitle, label, callback, suggested=False, icon=None):
    row = plain_row(title=title, subtitle=subtitle or "")
    if icon:
        row.add_prefix(Gtk.Image(icon_name=icon))
    b = Gtk.Button(label=label, valign=Gtk.Align.CENTER, css_classes=["suggested-action"] if suggested else [])
    b.connect("clicked", lambda *_: callback())
    row.add_suffix(b)
    return row


def link_row(title, subtitle, cmd_or_url, icon=None):
    row = plain_row(title=title, subtitle=subtitle or "", activatable=True)
    if icon:
        row.add_prefix(Gtk.Image(icon_name=icon))
    row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
    if isinstance(cmd_or_url, str):
        row.connect("activated", lambda *_: Gio.AppInfo.launch_default_for_uri(cmd_or_url, None))
    else:
        row.connect("activated", lambda *_: spawn(cmd_or_url))
    return row


def status_header(icon, title, description="", child=None, fallback="dialog-information-symbolic"):
    """A big icon, a title and a line of text at the top of a page."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=12)
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    box.append(Gtk.Image(icon_name=icon if theme.has_icon(icon) else fallback, pixel_size=64,
                         css_classes=["dim-label"]))
    box.append(Gtk.Label(label=title, css_classes=["title-2"], wrap=True, justify=Gtk.Justification.CENTER))
    if description:
        box.append(Gtk.Label(label=description, css_classes=["dim-label"], wrap=True,
                             justify=Gtk.Justification.CENTER))
    if child:
        child.set_margin_top(6)
        box.append(child)
    g = Adw.PreferencesGroup()
    g.add(box)
    return g


def switch_row(title, subtitle, active, on_change):
    row = Adw.SwitchRow(title=title, subtitle=subtitle or "", active=active)
    row.connect("notify::active", lambda r, _p: on_change(r.get_active()))
    return row


def combo_row(title, subtitle, options, selected, on_change):
    """options: list of (value, label). on_change(value)."""
    row = Adw.ComboRow(title=title, subtitle=subtitle or "", model=Gtk.StringList.new([lbl for _v, lbl in options]))
    values = [v for v, _l in options]
    if selected in values:
        row.set_selected(values.index(selected))
    row.connect("notify::selected", lambda r, _p: on_change(values[r.get_selected()]))
    return row


def cursor_themes() -> list[tuple[str, Path]]:
    """Installed pointer themes: (name, folder)."""
    found = {}
    for base in (Path.home() / ".local/share/icons", Path.home() / ".icons", Path("/usr/share/icons")):
        if base.is_dir():
            for d in sorted(base.iterdir()):
                if (d / "cursors").is_dir() and d.name not in found and d.name != "default":
                    found[d.name] = d
    return sorted(found.items(), key=lambda kv: (kv[0] != "Adwaita", kv[0].lower()))


def cursor_preview(folder: Path) -> str | None:
    """A PNG of the theme's arrow, made once with xcur2png if it's installed (else None)."""
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "jace-os" / "cursors"
    out = cache / f"{folder.name}.png"
    if out.exists():
        return str(out)
    src = folder / "cursors" / "left_ptr"
    if not src.exists() or not which("xcur2png"):
        return None
    cache.mkdir(parents=True, exist_ok=True)
    tmp = cache / f".{folder.name}"
    tmp.mkdir(exist_ok=True)
    subprocess.run(["xcur2png", "-q", "-d", str(tmp), "-c", "-", str(src)], capture_output=True, timeout=10)
    pngs = sorted(tmp.glob("*.png"), key=lambda p: p.stat().st_size)
    if pngs:
        pngs[-1].replace(out)
    for leftover in tmp.glob("*"):
        leftover.unlink(missing_ok=True)
    tmp.rmdir()
    return str(out) if out.exists() else None


def capture_cameras() -> list[tuple[str, str, bool]]:
    """Cameras that give a picture: (device, name, infrared). A webcam usually also shows up
    as a second /dev/video device (index 1) that only carries metadata; those are left out."""
    out = []
    for dev in sorted(Path("/sys/class/video4linux").glob("video*"), key=lambda p: int(p.name[5:] or 0)):
        try:
            if (dev / "index").read_text().strip() != "0":
                continue
            name = (dev / "name").read_text().strip()
        except OSError:
            continue
        ir = bool(re.search(r"\bIR\b|infrared", name, re.I))
        out.append((f"/dev/{dev.name}", name.split(":")[0], ir))
    return out


def gsettings_get(schema, key) -> str:
    return run(["gsettings", "get", schema, key]).strip().strip("'")


def gsettings_set(schema, key, value):
    subprocess.run(["gsettings", "set", schema, key, value], capture_output=True)


def labwc_reload():
    subprocess.run(["labwc", "--reconfigure"], capture_output=True)


# --- your labwc overrides (~/.config/labwc/rc.xml, merged over the Jace OS defaults) ---------------

class LabwcConfig:
    def __init__(self):
        self.path = LABWC_USER / "rc.xml"
        try:
            self.root = ET.parse(self.path).getroot()
        except (OSError, ET.ParseError):
            self.root = ET.Element("labwc_config")

    def node(self, path: str) -> ET.Element:
        cur = self.root
        for part in path.split("/"):
            nxt = cur.find(part)
            if nxt is None:
                nxt = ET.SubElement(cur, part)
            cur = nxt
        return cur

    def get(self, path: str, default: str) -> str:
        n = self.root.find(path)
        return n.text if n is not None and n.text else default

    def set(self, path: str, value: str):
        self.node(path).text = value
        self.save()

    def save(self):
        LABWC_USER.mkdir(parents=True, exist_ok=True)
        ET.indent(self.root)
        ET.ElementTree(self.root).write(self.path, encoding="unicode", xml_declaration=True)
        labwc_reload()


def default_labwc(path: str, default: str) -> str:
    """A value from the Jace OS default rc.xml, so switches show what's really in effect."""
    try:
        n = ET.parse("/usr/share/jace-os/xdg/labwc/rc.xml").getroot().find(path)
        return n.text if n is not None and n.text else default
    except (OSError, ET.ParseError):
        return default


# --- the window -----------------------------------------------------------------------------------

class SettingsWindow(Adw.ApplicationWindow):
    def __init__(self, app, page="account"):
        super().__init__(application=app, title="Settings", default_width=1060, default_height=720)
        self.set_icon_name("jace-settings")
        self.toasts = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView(min_sidebar_width=240, max_sidebar_width=280)

        sidebar_view = Adw.ToolbarView()
        sidebar_header = Adw.HeaderBar(show_title=True)
        sidebar_header.set_title_widget(Adw.WindowTitle(title="Settings"))
        sidebar_view.add_top_bar(sidebar_header)
        self.search = Gtk.SearchEntry(placeholder_text="Find a setting", margin_start=10, margin_end=10,
                                      margin_bottom=6)
        self.search.connect("search-changed", lambda *_: self.listbox.invalidate_filter())
        self.listbox = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.listbox.set_filter_func(self._filter)
        for pid, title, icon in PAGES:
            row = Gtk.ListBoxRow()
            row.page_id = pid
            row.page_title = title
            box = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=6)
            box.append(Gtk.Image(icon_name=icon))
            box.append(Gtk.Label(label=title, xalign=0))
            row.set_child(box)
            self.listbox.append(row)
        self.listbox.connect("row-selected", self._selected)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        side.append(self.search)
        side.append(Gtk.ScrolledWindow(child=self.listbox, vexpand=True))
        sidebar_view.set_content(side)
        self.split.set_sidebar(Adw.NavigationPage(title="Settings", child=sidebar_view))

        self.content = Adw.ToolbarView()
        self.content_title = Adw.WindowTitle()
        header = Adw.HeaderBar()
        header.set_title_widget(self.content_title)
        self.content.add_top_bar(header)
        self.content_page = Adw.NavigationPage(title="", child=self.content)
        self.split.set_content(self.content_page)

        self.toasts.set_child(self.split)
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)
        self.set_content(self.toasts)
        self.open_page(page)

    def _filter(self, row):
        q = self.search.get_text().strip().lower()
        return not q or q in row.page_title.lower() or q in row.page_id

    def _selected(self, _lb, row):
        if row:
            self.show(row.page_id)

    def open_page(self, pid):
        for i, (p, *_rest) in enumerate(PAGES):
            if p == pid:
                self.listbox.select_row(self.listbox.get_row_at_index(i))
                return
        self.listbox.select_row(self.listbox.get_row_at_index(0))

    def show(self, pid):
        title = next(t for p, t, _i in PAGES if p == pid)
        self.content_title.set_title(title)
        self.content_page.set_title(title)
        builder = getattr(self, f"page_{pid}")
        self.current = pid
        self._set_page(builder())
        self.split.set_show_content(True)

    def _set_page(self, page):
        """Shows a page with a short crossfade from the one before."""
        stack = getattr(self, "_pages", None)
        if stack is None:
            stack = self._pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                                            transition_duration=160)
            self.content.set_content(stack)
        old = stack.get_visible_child()
        stack.add_child(page)
        stack.set_visible_child(page)
        if old is not None:
            GLib.timeout_add(220, lambda: (stack.remove(old) if old.get_parent() is stack else None, False)[1])

    def reload(self):
        self.show(self.current)

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

    # --- Jace account ------------------------------------------------------------------------------

    def page_account(self):
        page = Adw.PreferencesPage()
        acct = account.load()
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_bottom=12)
        av = Adw.Avatar(size=96, show_initials=True, text=account.display_name(acct) or "Jace OS")
        pic = account.avatar_path()
        if acct and pic:
            try:
                av.set_custom_image(Gdk.Texture.new_from_filename(str(pic)))
            except GLib.Error:
                pass
        top.append(av)
        if acct:
            u = acct.get("user", {})
            top.append(Gtk.Label(label=account.display_name(acct), css_classes=["title-1"]))
            top.append(Gtk.Label(label=f"@{u.get('username', '')}" + (f"  ·  {u['email']}" if u.get("email") else ""),
                                 css_classes=["dim-label"]))
        else:
            top.append(Gtk.Label(label="Sign in with Jace", css_classes=["title-1"]))
            top.append(Gtk.Label(label="Use one Jace account for Jace OS, Jace Social, Jace Store and Jace Launcher.",
                                 css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER))
        g = Adw.PreferencesGroup()
        g.add(top)
        page.add(g)

        if acct:
            page.add(group("This computer", None,
                           switch_row("Use my Jace name and picture", "For this computer's account, the login "
                                      "screen and the top bar", profile.sync_on(), self._set_profile_sync)))
            page.add(group("Your account", None,
                           link_row("Open your Jace profile", JACE_URL, f"{JACE_URL}/account", "web-browser-symbolic"),
                           button_row("Refresh", "Update your name and picture from Jace", "Refresh",
                                      lambda: background(account.refresh_profile, lambda _r: self.reload())),
                           button_row("Sign out", "Removes your Jace account from this computer", "Sign out",
                                      self._sign_out)))
        else:
            self.signin_status = Gtk.Label(css_classes=["dim-label"], wrap=True)
            b = Gtk.Button(label="Sign in with Jace", css_classes=["suggested-action", "pill"],
                           halign=Gtk.Align.CENTER)
            b.connect("clicked", self._sign_in)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.append(b)
            box.append(self.signin_status)
            g2 = Adw.PreferencesGroup()
            g2.add(box)
            page.add(g2)
            page.add(group("No account yet?", None,
                           link_row("Create a Jace account", "It's free", f"{JACE_URL}/sign-up", "list-add-symbolic")))

        page.add(group("Jace apps", "Each app signs in with the same Jace account.",
                       *[link_row(name, about, [cmd], cmd) for _p, cmd, name, about in
                         __import__("jaceos").JACE_APPS]))
        return page

    def _sign_in(self, button):
        button.set_sensitive(False)
        self.signin_status.set_label("Finish signing in in your browser…")
        background(account.sign_in, lambda r: self._signed_in(r, button))

    def _signed_in(self, result, button):
        if isinstance(result, Exception):
            button.set_sensitive(True)
            self.signin_status.set_label(f"Couldn't sign in: {result}")
            return
        self.present()
        self.toast(f"Signed in as {account.display_name(result)}")
        self.reload()

    def _sign_out(self):
        account.sign_out()
        self.toast("Signed out")
        self.reload()

    # --- Updates -----------------------------------------------------------------------------------

    def page_updates(self):
        page = Adw.PreferencesPage()
        spinner = Adw.Spinner(halign=Gtk.Align.CENTER, width_request=32, height_request=32) \
            if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER)
        page.add(status_header("software-update-available-symbolic", "Checking for updates…",
                               "Jace OS, the Jace apps, Jace Browser and Flatpaks", spinner))
        background(updates.check, self._show_updates)
        return page

    def _show_updates(self, result):
        if getattr(self, "current", "") != "updates":
            return
        page = Adw.PreferencesPage()
        if isinstance(result, Exception):
            page.add(group("", None, plain_row(title="Couldn't check for updates", subtitle=str(result))))
            self._set_page(page)
            return

        n = updates.count(result)
        if result.get("finish"):
            restart = result["finish"] == "restart"
            banner = Adw.Banner(title="Restart to finish installing updates" if restart else
                                "Sign out and back in to finish installing updates",
                                button_label="Restart now" if restart else "Sign out now", revealed=True)
            banner.connect("button-clicked", lambda *_: spawn(["systemctl", "reboot"] if restart
                                                              else ["labwc", "--exit"]))
            g = Adw.PreferencesGroup()
            g.add(banner)
            page.add(g)

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        check = Gtk.Button(label="Check again", css_classes=["pill"])
        check.connect("clicked", lambda *_: self.show("updates"))
        actions.append(check)
        if n:
            allb = Gtk.Button(label="Update everything", css_classes=["pill", "suggested-action"])
            allb.connect("clicked", lambda *_: self._run_update(None))
            actions.append(allb)
        g = status_header(
            "emblem-ok-symbolic" if n == 0 else "software-update-available-symbolic",
            "You're up to date" if n == 0 else f"{n} update{'s' if n != 1 else ''} available",
            (f"Last updated {result['last_update']}" if result["last_update"] else
             "Jace OS installs updates by itself, including Linux and security fixes."), actions)
        page.add(g)

        self.update_log = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                       css_classes=["card"], top_margin=8, bottom_margin=8, left_margin=8)
        self.update_log_box = Gtk.ScrolledWindow(child=self.update_log, min_content_height=180, visible=False)
        g.add(self.update_log_box)

        apps = Adw.PreferencesGroup(title="Jace apps", description="Jace Launcher, Jace Social, Jace Store, "
                                                                   "Jace Browser and yay update through Jace OS.")
        for a in result["apps"]:
            row = plain_row(title=a["name"], subtitle=(
                f"{a['installed']}  →  {a['new']}" if a["update"] else
                (f"Version {a['installed']} · up to date" if a["installed"] else "Not installed")))
            if a["command"]:
                row.add_prefix(Gtk.Image(icon_name=a["command"], pixel_size=32))
            if a["update"] or not a["installed"]:
                b = Gtk.Button(label="Update" if a["installed"] else "Install", valign=Gtk.Align.CENTER,
                               css_classes=["suggested-action"])
                b.connect("clicked", lambda _b, p=a["package"]: self._run_update([p]))
                row.add_suffix(b)
            else:
                row.add_suffix(Gtk.Image(icon_name="emblem-ok-symbolic", css_classes=["success"]))
            apps.add(row)
        page.add(apps)

        if result["system"]:
            sysg = Adw.PreferencesGroup(title="System", description=(
                "Includes security fixes for " + ", ".join(result["security"]) if result["security"]
                else "Linux, drivers and everything else from Arch Linux."))
            exp = Adw.ExpanderRow(title=f"{len(result['system'])} system packages",
                                  subtitle="Updated together, so nothing ends up half-updated")
            for u in result["system"]:
                r = plain_row(title=u["name"], subtitle=f"{u['old']}  →  {u['new']}")
                if u["name"] in result["security"]:
                    r.add_suffix(Gtk.Label(label="Security", css_classes=["accent", "caption-heading"]))
                exp.add_row(r)
            sysg.add(exp)
            page.add(sysg)

        if result["flatpak"]:
            fg = Adw.PreferencesGroup(title="Flatpak apps")
            fb = Gtk.Button(label="Update", valign=Gtk.Align.CENTER, css_classes=["pill"])
            fb.connect("clicked", lambda *_: self._run_update(["--flatpak"]))
            fg.set_header_suffix(fb)
            for u in result["flatpak"]:
                fg.add(plain_row(title=u["title"], subtitle=u["new"] or u["name"]))
            page.add(fg)

        auto = Adw.PreferencesGroup(title="Automatic updates")
        auto.add(switch_row("Install updates automatically",
                            "Every few hours when plugged in: Linux, security fixes, Jace apps and everything else",
                            result["auto"], self._set_auto))
        auto.add(link_row("Update history", "/var/log/jace-update.log",
                          ["jace-terminal", "--", "less", "+G", "/var/log/jace-update.log"], "document-open-recent-symbolic"))
        if result["last_error"]:
            auto.add(plain_row(title="The last update didn't finish",
                                   subtitle=result["last_error"].splitlines()[-1][:200]))
        page.add(auto)
        self._set_page(page)

    def _set_auto(self, on):
        background(lambda: updates.set_auto(on),
                   lambda res: self.toast(("Automatic updates on" if on else "Automatic updates off")
                                          if res is True else "Couldn't change automatic updates"))

    def _run_update(self, packages):
        self.update_log_box.set_visible(True)
        buf = self.update_log.get_buffer()
        buf.set_text("Starting the update…\n")
        proc = updates.install(packages)

        def read_line(stream, _cond):
            line = stream.readline()
            if line:
                buf.insert(buf.get_end_iter(), line)
                self.update_log.scroll_to_iter(buf.get_end_iter(), 0, False, 0, 0)
                return True
            code = proc.wait()
            self.toast("Updates installed" if code == 0 else "The update didn't finish; see the log")
            GLib.timeout_add_seconds(2, lambda: (self.current == "updates" and self.show("updates"), False)[1])
            return False

        GLib.io_add_watch(proc.stdout, GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP, read_line)

    # --- Virus protection ---------------------------------------------------------------------------

    def page_security(self):
        page = Adw.PreferencesPage()
        if not antivirus.installed():
            page.add(group("", None, plain_row(title="ClamAV isn't installed",
                                                subtitle="Install the clamav package to use virus protection")))
            return page
        rt_on, rt_running = antivirus.realtime_enabled(), antivirus.realtime_running()
        sigs = antivirus.signatures()
        if rt_running and sigs.get("have") and sigs.get("age_days", 99) < 3:
            icon, title, desc = "security-high-symbolic", "You're protected", "Real-time protection is on."
        elif rt_on and not rt_running:
            icon, title, desc = "security-medium-symbolic", "Starting protection…", \
                "Downloading virus signatures the first time takes a few minutes."
        elif not rt_on:
            icon, title, desc = "security-low-symbolic", "Real-time protection is off", \
                "Turn it on to scan files as soon as they arrive."
        else:
            icon, title, desc = "security-medium-symbolic", "Virus signatures are out of date", \
                "Click Update signatures, or check your internet connection."
        page.add(status_header(icon, title, desc, fallback="emblem-ok-symbolic" if "protected" in title
                               else "dialog-warning-symbolic"))

        sg = Adw.PreferencesGroup(title="Scan", description="Scans run as you, in the background.")
        self.av_status = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"], visible=False)
        for title_, sub, paths in (
            ("Quick scan", "Downloads, Desktop, Documents, Applications and startup apps", None),
            ("Full scan", "Everything in your home folder", [str(Path.home())]),
        ):
            sg.add(button_row(title_, sub, "Scan", lambda p=paths: self._av_scan(p or antivirus.quick_scan_paths()),
                              suggested=paths is None))
        sg.add(button_row("Scan a folder or file", None, "Choose…", self._av_choose))
        sg.add(self.av_status)
        self.av_results = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE,
                                      visible=False, margin_top=6)
        sg.add(self.av_results)
        page.add(sg)

        pg = Adw.PreferencesGroup(title="Protection")
        pg.add(switch_row("Real-time protection", "Scans files in your home folder as soon as they're downloaded, "
                          "created or opened, and quarantines threats", rt_on,
                          lambda on: self._av_helper(["realtime", "on" if on else "off"],
                                                     "Real-time protection " + ("on" if on else "off"))))
        last = antivirus.last_scheduled_scan()
        sub = "Every day, when plugged in"
        if last:
            when = GLib.DateTime.new_from_unix_local(last["time"]).format("%b %-d, %H:%M")
            sub += f" · last scan {when}: " + (f"{last['found']} threat(s) quarantined" if last["found"]
                                                 else "nothing found")
        pg.add(switch_row("Scan every day", sub, antivirus.autoscan_enabled(),
                          lambda on: self._av_helper(["autoscan", "on" if on else "off"],
                                                     "Daily scans " + ("on" if on else "off"))))
        sig_row = plain_row(title="Virus signatures", subtitle=(
            f"Version {sigs['version']} · {sigs['date']}" if sigs.get("have") else "Not downloaded yet"))
        b = Gtk.Button(label="Update signatures", valign=Gtk.Align.CENTER)
        b.connect("clicked", lambda *_: self._av_helper(["update"], "Virus signatures updated"))
        sig_row.add_suffix(b)
        pg.add(sig_row)
        page.add(pg)

        mine = antivirus.user_quarantine()
        qg = Adw.PreferencesGroup(title="Quarantine", description="Threats are moved here, where they can't run.")
        for item in mine:
            row = plain_row(title=Path(item["original"]).name, subtitle=f"{item['virus']} · from {item['original']}")
            for label, fn, style in (("Restore", self._av_restore, None), ("Delete", self._av_delete,
                                                                          "destructive-action")):
                bt = Gtk.Button(label=label, valign=Gtk.Align.CENTER, css_classes=[style] if style else [])
                bt.connect("clicked", lambda _b, i=item, f=fn: f(i))
                row.add_suffix(bt)
            qg.add(row)
        events = [e for e in antivirus.events() if e.get("file", "").startswith(str(Path.home()))
                  and not antivirus.dismissed(e)]
        for ev in events[:15]:
            when = GLib.DateTime.new_from_unix_local(ev["time"]).format("%b %-d, %H:%M")
            path = Path(ev["file"])
            still_there = path.exists()
            where = "Still in its folder" if still_there else "In quarantine"
            row = plain_row(title=path.name, subtitle=f"{ev['virus']} · {when} · {where}\n{ev['file']}")
            row.set_subtitle_lines(3)
            if still_there:
                # Found by a scan but not moved: act on the file where it is.
                qb = Gtk.Button(label="Quarantine", valign=Gtk.Align.CENTER)
                qb.connect("clicked", lambda _b, e=ev: self._av_event(e, "quarantine"))
                db = Gtk.Button(label="Delete", valign=Gtk.Align.CENTER, css_classes=["destructive-action"])
                db.connect("clicked", lambda _b, e=ev: self._av_event(e, "delete-file"))
                row.add_suffix(qb)
                row.add_suffix(db)
            else:
                rb = Gtk.Button(label="Restore", valign=Gtk.Align.CENTER)
                rb.connect("clicked", lambda _b, e=ev: self._av_event(e, "restore"))
                db = Gtk.Button(label="Delete", valign=Gtk.Align.CENTER, css_classes=["destructive-action"])
                db.connect("clicked", lambda _b, e=ev: self._av_event(e, "delete-quarantined"))
                row.add_suffix(rb)
                row.add_suffix(db)
            ib = Gtk.Button(icon_name="window-close-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                            tooltip_text="Ignore: it's safe (a false alarm)")
            ib.connect("clicked", lambda _b, e=ev: self._av_event(e, "ignore"))
            row.add_suffix(ib)
            qg.add(row)
        if not mine and not events:
            qg.add(plain_row(title="Nothing in quarantine"))
        page.add(qg)
        return page

    def _av_event(self, ev, what):
        """Acts on a detection from the threat log, then takes it off the list."""
        path, name = Path(ev["file"]), Path(ev["file"]).name

        def work():
            if what == "ignore":
                return True, f"Ignored {name}"
            if what == "quarantine":
                antivirus.quarantine(str(path), ev["virus"])
                return True, f"Moved {name} to quarantine"
            if what == "delete-file":
                try:
                    path.unlink()
                except PermissionError:
                    good, msg = antivirus.helper("delete-path", str(path))
                    if not good:
                        return False, msg or f"Couldn't delete {name}"
                return True, f"Deleted {name}"
            if what == "restore":
                good, msg = antivirus.helper("restore", name, str(path))
                return good, (f"Restored {name}" if good else msg or "Couldn't restore it")
            good, msg = antivirus.helper("delete", name)
            return good, (f"Deleted {name}" if good else msg or "Couldn't delete it")

        def done(res):
            good, msg = res if isinstance(res, tuple) else (False, str(res))
            if good:
                antivirus.dismiss(ev)
            self.toast(msg)
            if getattr(self, "current", "") == "security":
                self.reload()

        background(work, done)

    def _av_helper(self, args, success, reload=True):
        def done(res):
            good, msg = res if isinstance(res, tuple) else (False, str(res))
            self.toast(success if good else (msg.splitlines()[-1] if msg else "That didn't work"))
            if reload and getattr(self, "current", "") == "security":
                self.reload()
        background(lambda: antivirus.helper(*args), done)

    def _av_choose(self):
        dialog = Gtk.FileDialog(title="Choose what to scan")

        def done(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            self._av_scan([f.get_path()])

        dialog.select_folder(self, None, done)

    def _av_scan(self, paths):
        self.av_status.set_visible(True)
        self.av_status.set_label("Scanning " + ", ".join(Path(p).name or p for p in paths) + "…")
        self.av_results.remove_all()
        self.av_results.set_visible(False)

        def found(item):
            GLib.idle_add(lambda: (self._av_found_row(item), False)[1])

        def done(res):
            if getattr(self, "current", "") != "security":
                return
            if isinstance(res, Exception):
                self.av_status.set_label(str(res))
                return
            n = len(res["found"])
            files = f"{res['scanned']} files · " if res["scanned"] else ""
            self.av_status.set_label((f"Found {n} threat{'s' if n != 1 else ''}. Quarantine them below."
                                      if n else "No threats found.") + f"  ({files}{res['seconds']:.0f} s)")
            if n:
                self.toast(f"Found {n} threat{'s' if n != 1 else ''}")

        background(lambda: antivirus.scan(paths, on_found=found), done)

    def _av_found_row(self, item):
        self.av_results.set_visible(True)
        row = plain_row(title=Path(item["file"]).name, subtitle=f"{item['virus']} · {item['file']}")
        b = Gtk.Button(label="Quarantine", valign=Gtk.Align.CENTER, css_classes=["destructive-action"])

        def q(*_):
            try:
                antivirus.quarantine(item["file"], item["virus"])
                b.set_sensitive(False)
                b.set_label("Quarantined")
            except OSError as e:
                self.toast(f"Couldn't move it: {e.strerror}")
        b.connect("clicked", q)
        row.add_suffix(b)
        self.av_results.append(row)

    def _av_restore(self, item):
        try:
            self.toast(f"Restored to {antivirus.restore(item)}")
        except OSError as e:
            self.toast(f"Couldn't restore it: {e.strerror}")
        self.reload()

    def _av_delete(self, item):
        antivirus.delete(item)
        self.toast("Deleted for good")
        self.reload()

    # --- Network -----------------------------------------------------------------------------------

    def page_network(self):
        page = Adw.PreferencesPage()
        if not which("nmcli"):
            page.add(group("", None, plain_row(title="NetworkManager isn't installed")))
            return page
        wifi_on = run(["nmcli", "radio", "wifi"]).strip() == "enabled"
        g = Adw.PreferencesGroup(title="Wi-Fi")
        g.add(switch_row("Wi-Fi", None, wifi_on, lambda on: (
            subprocess.run(["nmcli", "radio", "wifi", "on" if on else "off"]), GLib.timeout_add(1500, self.reload))))
        page.add(g)
        nets = Adw.PreferencesGroup(title="Networks")
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", css_classes=["flat"], tooltip_text="Scan again")
        refresh.connect("clicked", lambda *_: self.reload())
        nets.set_header_suffix(refresh)
        self.net_list = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        nets.add(self.net_list)
        page.add(nets)
        if wifi_on:
            self.net_list.append(plain_row(title="Looking for networks…"))

            def scan():
                out = run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", "auto"],
                          timeout=30)
                seen, found = set(), []
                for line in out.splitlines():
                    parts = re.split(r"(?<!\\):", line)
                    if len(parts) < 4 or not parts[1] or parts[1] in seen:
                        continue
                    seen.add(parts[1])
                    found.append({"active": parts[0] == "*", "ssid": parts[1].replace("\\:", ":"),
                                  "signal": int(parts[2] or 0), "secure": parts[3] not in ("", "--")})
                return found

            background(scan, self._show_networks)
        else:
            self.net_list.append(plain_row(title="Wi-Fi is off"))

        other = Adw.PreferencesGroup(title="More")
        other.add(link_row("Network connections", "Wired, VPN, hotspot, proxies and IP settings",
                           ["nm-connection-editor"], "network-wired-symbolic"))
        dev = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
        for line in dev.splitlines():
            d = line.split(":")
            if len(d) >= 4 and d[1] in ("ethernet", "wifi") and d[2] == "connected":
                other.add(plain_row(title=f"{d[3]}", subtitle=f"Connected · {d[0]}"))
        page.add(other)
        return page

    def _show_networks(self, found):
        if getattr(self, "current", "") != "network" or isinstance(found, Exception):
            return
        self.net_list.remove_all()
        for n in sorted(found, key=lambda n: (not n["active"], -n["signal"])):
            row = plain_row(title=n["ssid"], subtitle=("Connected" if n["active"] else
                                                           f"Signal {n['signal']}%" + (" · Secured" if n["secure"] else "")))
            bars = "network-wireless-signal-" + ("excellent" if n["signal"] > 75 else "good" if n["signal"] > 50
                                                 else "ok" if n["signal"] > 25 else "weak") + "-symbolic"
            row.add_prefix(Gtk.Image(icon_name=bars))
            if n["secure"]:
                row.add_suffix(Gtk.Image(icon_name="channel-secure-symbolic"))
            if n["active"]:
                b = Gtk.Button(label="Disconnect", valign=Gtk.Align.CENTER)
                b.connect("clicked", lambda *_, s=n["ssid"]: (subprocess.run(["nmcli", "con", "down", "id", s]),
                                                               self.reload()))
            else:
                b = Gtk.Button(label="Connect", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
                b.connect("clicked", lambda *_, net=n: self._connect_wifi(net))
            row.add_suffix(b)
            self.net_list.append(row)
        if not found:
            self.net_list.append(plain_row(title="No networks found"))

    def _connect_wifi(self, net):
        def connect(password=None):
            cmd = ["nmcli", "dev", "wifi", "connect", net["ssid"]]
            if password:
                cmd += ["password", password]
            background(lambda: ok(cmd, timeout=45), lambda good: (
                self.toast(f"Connected to {net['ssid']}" if good is True else f"Couldn't connect to {net['ssid']}"),
                self.reload()))

        known = net["ssid"] in run(["nmcli", "-t", "-f", "NAME", "con", "show"]).splitlines()
        if not net["secure"] or known:
            connect()
            return
        entry = Adw.PasswordEntryRow(title="Password")
        box = Gtk.ListBox(css_classes=["boxed-list"])
        box.append(entry)
        dialog = Adw.AlertDialog(heading=f"Connect to {net['ssid']}", body="Enter the Wi-Fi password.")
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("connect", "Connect")
        dialog.set_response_appearance("connect", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("connect")
        dialog.connect("response", lambda _d, r: r == "connect" and connect(entry.get_text()))
        dialog.present(self)

    # --- Bluetooth ---------------------------------------------------------------------------------

    def page_bluetooth(self):
        page = Adw.PreferencesPage()
        if not which("bluetoothctl"):
            page.add(group("", None, plain_row(title="Bluetooth isn't installed")))
            return page
        show = run(["bluetoothctl", "show"])
        if "Controller" not in show:
            page.add(group("", None, plain_row(title="No Bluetooth adapter found",
                                                    subtitle="Plug one in, or check it's turned on in your firmware")))
            return page
        powered = "Powered: yes" in show
        page.add(group("", None, switch_row("Bluetooth", "Let this computer find and use Bluetooth devices", powered,
                                            lambda on: (subprocess.run(["bluetoothctl", "power", "on" if on else "off"],
                                                                       capture_output=True), self.reload()))))
        devs = Adw.PreferencesGroup(title="Your devices")
        add = Gtk.Button(label="Add a device", css_classes=["flat"])
        add.connect("clicked", lambda *_: spawn(["blueman-manager"]))
        devs.set_header_suffix(add)
        found = False
        for line in run(["bluetoothctl", "devices", "Paired"]).splitlines():
            parts = line.split(" ", 2)
            if len(parts) < 3:
                continue
            found = True
            mac, name = parts[1], parts[2]
            connected = "Connected: yes" in run(["bluetoothctl", "info", mac])
            row = plain_row(title=name, subtitle="Connected" if connected else "Not connected")
            b = Gtk.Button(label="Disconnect" if connected else "Connect", valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda _b, m=mac, c=connected: background(
                lambda: ok(["bluetoothctl", "disconnect" if c else "connect", m], timeout=30),
                lambda _r: self.reload()))
            row.add_suffix(b)
            rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                            tooltip_text="Forget this device")
            rm.connect("clicked", lambda _b, m=mac: (subprocess.run(["bluetoothctl", "remove", m],
                                                                    capture_output=True), self.reload()))
            row.add_suffix(rm)
            devs.add(row)
        if not found:
            devs.add(plain_row(title="No devices yet", subtitle="Click Add a device to pair one"))
        page.add(devs)
        return page

    # --- Display -----------------------------------------------------------------------------------

    def page_display(self):
        page = Adw.PreferencesPage()
        try:
            outputs = json.loads(run(["wlr-randr", "--json"]) or "[]")
        except ValueError:
            outputs = []
        if not outputs:
            page.add(group("", None, plain_row(title="Couldn't read your displays",
                                                    subtitle="This page works inside a Jace OS session")))
        for out in outputs:
            name = out.get("name", "")
            g = Adw.PreferencesGroup(title=out.get("description") or name, description=name)
            modes = out.get("modes", [])
            seen, opts, current = set(), [], None
            for m in sorted(modes, key=lambda m: (-m["width"] * m["height"], -m["refresh"])):
                key = f"{m['width']}x{m['height']}@{m['refresh']:.3f}"
                label = f"{m['width']} × {m['height']}  ({m['refresh']:.0f} Hz)"
                if label in seen:
                    continue
                seen.add(label)
                opts.append((key, label + ("  · recommended" if m.get("preferred") else "")))
                if m.get("current"):
                    current = key
            state = {"mode": current, "scale": f"{out.get('scale', 1):g}"}
            g.add(switch_row("Use this display", None, out.get("enabled", True),
                             lambda on, n=name: self._apply_output(n, {"enabled": on})))
            g.add(combo_row("Resolution", None, opts, current, lambda v, s=state: s.update(mode=v)))
            g.add(combo_row("Scale", "Makes text and apps bigger", [(s, f"{float(s) * 100:.0f}%") for s in
                                                                    ("1", "1.25", "1.5", "1.75", "2")],
                            state["scale"], lambda v, s=state: s.update(scale=v)))
            g.add(button_row("Apply", "Keeps these settings next time you sign in", "Apply",
                             lambda n=name, s=state: self._apply_output(n, s), suggested=True))
            page.add(g)
        nl = (config_dir() / "nightlight").exists()
        page.add(group("Night light", None,
                       switch_row("Night light", "Warmer colors in the evening, easier on your eyes", nl,
                                  self._night_light)))
        page.add(group("", None, link_row("Arrange displays", "Drag your screens into place", ["wdisplays"],
                                          "preferences-desktop-display-symbolic")))
        return page

    def _apply_output(self, name, state):
        args = ["--output", name]
        if "enabled" in state:
            args.append("--on" if state["enabled"] else "--off")
        if state.get("mode"):
            args += ["--mode", state["mode"].split("@")[0] + "@" + state["mode"].split("@")[1] + "Hz"]
        if state.get("scale"):
            args += ["--scale", state["scale"]]
        if not ok(["wlr-randr", *args]):
            self.toast("Couldn't change that display")
            return
        # Remember it: ~/.config/jace-os/displays has one wlr-randr line per display, run at sign-in.
        f = config_dir() / "displays"
        lines = [ln for ln in (f.read_text().splitlines() if f.exists() else []) if f"--output {name} " not in ln + " "]
        lines.append(" ".join(["wlr-randr", *[a if " " not in a else f"'{a}'" for a in args]]))
        f.write_text("\n".join(lines) + "\n")
        self.toast("Display settings applied")

    def _night_light(self, on):
        flag = config_dir() / "nightlight"
        subprocess.run(["pkill", "-x", "wlsunset"])
        if on:
            flag.touch()
            spawn(["wlsunset", "-t", "4000", "-T", "6500", "-S", "07:00", "-s", "20:00"])
        else:
            flag.unlink(missing_ok=True)

    # --- Sound -------------------------------------------------------------------------------------

    def page_sound(self):
        page = Adw.PreferencesPage()

        def devices(kind):
            try:
                items = json.loads(run(["pactl", "-f", "json", "list", kind]) or "[]")
            except ValueError:
                items = []
            return [d for d in items if not d.get("name", "").endswith(".monitor")]

        for kind, title, getdef, setdef, setvol, setmute in (
            ("sinks", "Output", "get-default-sink", "set-default-sink", "set-sink-volume", "set-sink-mute"),
            ("sources", "Input (microphone)", "get-default-source", "set-default-source", "set-source-volume",
             "set-source-mute"),
        ):
            devs = devices(kind)
            g = Adw.PreferencesGroup(title=title)
            if not devs:
                g.add(plain_row(title="No devices found"))
                page.add(g)
                continue
            default = run(["pactl", getdef]).strip()
            g.add(combo_row("Device", None, [(d["name"], d.get("description", d["name"])) for d in devs], default,
                            lambda v, c=setdef: (subprocess.run(["pactl", c, v]), GLib.timeout_add(300, self.reload))))
            cur = next((d for d in devs if d["name"] == default), devs[0])
            vols = cur.get("volume", {})
            pct = 100
            for ch in vols.values():
                m = re.match(r"(\d+)%", str(ch.get("value_percent", "100%")))
                pct = int(m.group(1)) if m else 100
                break
            row = plain_row(title="Volume")
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
            scale.set_hexpand(True)
            scale.set_value(min(pct, 100))
            scale.set_size_request(260, -1)
            scale.connect("value-changed", lambda s, c=setvol, n=cur["name"]:
                          subprocess.run(["pactl", c, n, f"{int(s.get_value())}%"]))
            row.add_suffix(scale)
            g.add(row)
            g.add(switch_row("Mute", None, bool(cur.get("mute")),
                             lambda on, c=setmute, n=cur["name"]: subprocess.run(["pactl", c, n, "1" if on else "0"])))
            page.add(g)
        page.add(group("", None, link_row("Volume for each app", "And more sound options",
                                          ["pavucontrol"], "multimedia-volume-control-symbolic")))
        return page

    # --- Appearance --------------------------------------------------------------------------------

    def page_appearance(self):
        page = Adw.PreferencesPage()
        scheme = gsettings_get("org.gnome.desktop.interface", "color-scheme")
        g = Adw.PreferencesGroup(title="Style")
        g.add(combo_row("Theme", "Light or dark apps and windows",
                        [("prefer-dark", "Dark"), ("default", "Light")],
                        "prefer-dark" if scheme == "prefer-dark" else "default", self._set_scheme))
        scaling = gsettings_get("org.gnome.desktop.interface", "text-scaling-factor") or "1.0"
        g.add(combo_row("Text size", None, [("0.9", "Small"), ("1.0", "Normal"), ("1.15", "Large"),
                                            ("1.3", "Larger")],
                        min(("0.9", "1.0", "1.15", "1.3"), key=lambda v: abs(float(v) - float(scaling))),
                        lambda v: gsettings_set("org.gnome.desktop.interface", "text-scaling-factor", v)))
        icons = gsettings_get("org.gnome.desktop.interface", "icon-theme")
        g.add(combo_row("Icons", None, [("Papirus-Dark", "Papirus (dark)"), ("Papirus", "Papirus (light)"),
                                        ("Adwaita", "Adwaita")], icons,
                        lambda v: gsettings_set("org.gnome.desktop.interface", "icon-theme", v)))
        page.add(g)

        wg = Adw.PreferencesGroup(title="Wallpaper")
        pick = Gtk.Button(label="Choose a picture…", css_classes=["flat"])
        pick.connect("clicked", lambda *_: self._choose_wallpaper())
        wg.set_header_suffix(pick)
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4, min_children_per_line=2,
                           row_spacing=10, column_spacing=10, homogeneous=True)
        current = run(["jace-wallpaper", "path"]).strip()
        for f in sorted(BACKGROUNDS.glob("*")) if BACKGROUNDS.exists() else []:
            if f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                continue
            pic = Gtk.Picture.new_for_filename(str(f))
            pic.set_content_fit(Gtk.ContentFit.COVER)
            pic.set_size_request(180, 110)
            b = Gtk.Button(child=pic, css_classes=["jw-thumb"] + (["jw-current"] if str(f) == current else []),
                           tooltip_text=f.stem.replace("-", " ").title())
            b.connect("clicked", lambda _b, p=str(f): self._set_wallpaper(p))
            flow.append(b)
        wg.add(flow)
        page.add(wg)
        return page

    def _set_scheme(self, v):
        gsettings_set("org.gnome.desktop.interface", "color-scheme", v)
        # adw-gtk3 makes older GTK 3 apps look like the libadwaita ones.
        gsettings_set("org.gnome.desktop.interface", "gtk-theme", "adw-gtk3-dark" if v == "prefer-dark" else "adw-gtk3")

    def _set_wallpaper(self, path):
        subprocess.run(["jace-wallpaper", "set", path])
        self.toast("Wallpaper changed")
        self.reload()

    def _choose_wallpaper(self):
        dialog = Gtk.FileDialog(title="Choose a wallpaper")
        filt = Gtk.FileFilter(name="Pictures")
        filt.add_mime_type("image/*")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(filt)
        dialog.set_filters(store)

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            self._set_wallpaper(f.get_path())

        dialog.open(self, None, done)

    # --- Start menu --------------------------------------------------------------------------------

    def page_menu(self):
        page = Adw.PreferencesPage()
        cfg = start_menu.load_config()

        def setv(key):
            def apply(v):
                cfg[key] = v
                start_menu.save_config(cfg)
            return apply

        look = Adw.PreferencesGroup(title="Layout", description="The start menu opens when you tap the Windows key "
                                                                 "or click J on the top bar.")
        look.add(combo_row("Where it opens", None, [("top-left", "Top left, under the J button"),
                                                    ("top-center", "Top middle"), ("center", "Middle of the screen")],
                           cfg["position"], setv("position")))
        look.add(combo_row("Size", None, [("compact", "Compact"), ("normal", "Normal"), ("large", "Large")],
                           cfg["size"], setv("size")))
        look.add(combo_row("Pinned apps", None, [("grid", "Grid of icons"), ("list", "List")], cfg["layout"],
                           setv("layout")))
        cols = Adw.SpinRow.new_with_range(3, 10, 1)
        cols.set_title("Icons per row")
        cols.set_value(cfg["columns"])
        cols.connect("notify::value", lambda r, _p: setv("columns")(int(r.get_value())))
        look.add(cols)
        look.add(switch_row("Show app names", "Under each pinned icon", cfg["show_labels"], setv("show_labels")))
        look.add(switch_row("Show your Jace account", "At the bottom of the menu", cfg["show_account"],
                            setv("show_account")))
        look.add(switch_row("Open on All apps", "Instead of your pinned apps", cfg["start_on_all_apps"],
                            setv("start_on_all_apps")))
        op = plain_row(title="See-through", subtitle="How much of the desktop shows through the menu")
        sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 40, 1)
        sc.set_value(round((1 - cfg["opacity"]) * 100))
        sc.set_size_request(220, -1)
        sc.connect("value-changed", lambda s: setv("opacity")(round(1 - s.get_value() / 100, 2)))
        op.add_suffix(sc)
        look.add(op)
        page.add(look)

        pg = Adw.PreferencesGroup(title="Pinned apps", description="Right-click any app in the start menu to pin it.")
        add = Gtk.Button(label="Pin an app…", css_classes=["flat"])
        add.connect("clicked", lambda *_: self._pick_app(cfg))
        pg.set_header_suffix(add)
        for i, app_id in enumerate(cfg["pinned"]):
            app = start_menu.app_by_id(app_id)
            row = plain_row(title=app.get_display_name() if app else app_id,
                                subtitle="" if app else "Not installed")
            if app and app.get_icon():
                img = Gtk.Image.new_from_gicon(app.get_icon())
                img.set_pixel_size(32)
                row.add_prefix(img)
            for icon, tip, delta in (("go-up-symbolic", "Move up", -1), ("go-down-symbolic", "Move down", 1)):
                b = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER, css_classes=["flat"],
                               sensitive=0 <= i + delta < len(cfg["pinned"]))
                b.connect("clicked", lambda _b, i=i, d=delta: self._move_pin(cfg, i, d))
                row.add_suffix(b)
            rm = Gtk.Button(icon_name="list-remove-symbolic", tooltip_text="Unpin", valign=Gtk.Align.CENTER,
                            css_classes=["flat"])
            rm.connect("clicked", lambda _b, a=app_id: (cfg["pinned"].remove(a), start_menu.save_config(cfg),
                                                        self.reload()))
            row.add_suffix(rm)
            pg.add(row)
        if not cfg["pinned"]:
            pg.add(plain_row(title="Nothing pinned"))
        page.add(pg)

        hg = Adw.PreferencesGroup(title="Hidden apps",
                                  description="Apps you hid, and helper tools Jace OS keeps out of the menu "
                                              "(Settings covers most of them). They still work.")
        hidden_now = sorted(start_menu.hidden_ids(cfg))
        shown_any = False
        for app_id in hidden_now:
            app = start_menu.app_by_id(app_id)
            if app is None:          # not installed
                continue
            shown_any = True
            mine = app_id in cfg["hidden"]
            row = plain_row(title=app.get_display_name(), subtitle="Hidden by you" if mine else "A helper tool")
            if app.get_icon():
                img = Gtk.Image.new_from_gicon(app.get_icon())
                img.set_pixel_size(28)
                row.add_prefix(img)
            b = Gtk.Button(label="Show", valign=Gtk.Align.CENTER)

            def show(_b, a=app_id, m=mine):
                if m:
                    cfg["hidden"].remove(a)
                if a in start_menu.SYSTEM_HIDDEN and a not in cfg.setdefault("shown", []):
                    cfg["shown"].append(a)
                start_menu.save_config(cfg)
                self.reload()
            b.connect("clicked", show)
            row.add_suffix(b)
            hg.add(row)
        if not shown_any:
            hg.add(plain_row(title="No hidden apps"))
        page.add(hg)

        eg = Adw.PreferencesGroup(title="Edit the menu",
                                  description="Add your own apps and websites, or rename any app, change its icon "
                                              "or what it runs. Right-click an app in the start menu for Edit too.")
        newb = Gtk.Button(label="Add an app or website", css_classes=["suggested-action", "pill"],
                          halign=Gtk.Align.START, margin_bottom=8)
        newb.connect("clicked", lambda *_: self._edit_app(None))
        eg.add(newb)
        eg.add(button_row("Edit an app", "Pick any app to rename it or change its icon", "Choose…",
                          lambda: self._pick_any_app(self._edit_app)))
        for f in sorted(menu_edits.user_entries()):
            info = menu_edits.read(f)
            row = plain_row(title=info.get("Name", f.stem), subtitle="Yours" if menu_edits.is_custom(f) else "Edited")
            b = Gtk.Button(label="Edit", valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda _b, i=f.name: self._edit_app(i))
            row.add_suffix(b)
            eg.add(row)
        page.add(eg)

        page.add(group("", None, button_row("Reset the start menu", "Back to the Jace OS layout and pinned apps",
                                            "Reset", lambda: (start_menu.save_config(dict(start_menu.DEFAULTS)),
                                                              self.reload()))))
        return page

    def _move_pin(self, cfg, i, delta):
        p = cfg["pinned"]
        p[i], p[i + delta] = p[i + delta], p[i]
        start_menu.save_config(cfg)
        self.reload()

    def _pick_app(self, cfg):
        dialog = Adw.Dialog(title="Pin an app", content_width=420, content_height=560)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=12, margin_end=12,
                      margin_bottom=12)
        search = Gtk.SearchEntry(placeholder_text="Search apps")
        lb = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        for app in start_menu.all_apps():
            if app.get_id() in cfg["pinned"]:
                continue
            row = plain_row(title=app.get_display_name(), activatable=True)
            if app.get_icon():
                img = Gtk.Image.new_from_gicon(app.get_icon())
                img.set_pixel_size(28)
                row.add_prefix(img)
            row.connect("activated", lambda _r, a=app.get_id(): (cfg["pinned"].append(a),
                                                                  start_menu.save_config(cfg),
                                                                  dialog.close(), self.reload()))
            lb.append(row)
        lb.set_filter_func(lambda r: search.get_text().lower() in r.get_title().lower())
        search.connect("search-changed", lambda *_: lb.invalidate_filter())
        box.append(search)
        box.append(Gtk.ScrolledWindow(child=lb, vexpand=True))
        view.set_content(box)
        dialog.set_child(view)
        dialog.present(self)

    def _pick_any_app(self, then):
        dialog = Adw.Dialog(title="Choose an app", content_width=420, content_height=560)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=12, margin_end=12,
                      margin_bottom=12)
        search = Gtk.SearchEntry(placeholder_text="Search apps")
        lb = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        for app in start_menu.all_apps():
            row = plain_row(title=app.get_display_name(), activatable=True)
            if app.get_icon():
                img = Gtk.Image.new_from_gicon(app.get_icon())
                img.set_pixel_size(28)
                row.add_prefix(img)
            row.connect("activated", lambda _r, a=app.get_id(): (dialog.close(), then(a)))
            lb.append(row)
        lb.set_filter_func(lambda r: search.get_text().lower() in r.get_title().lower())
        search.connect("search-changed", lambda *_: lb.invalidate_filter())
        box.append(search)
        box.append(Gtk.ScrolledWindow(child=lb, vexpand=True))
        view.set_content(box)
        dialog.set_child(view)
        dialog.present(self)

    def _edit_app(self, app_id):
        """Edit an app's menu entry (app_id), or add a new one (None)."""
        info = menu_edits.load(app_id) if app_id else {"Name": "", "Exec": "", "Icon": "", "NoDisplay": "false"}
        lb = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        name = Adw.EntryRow(title="Name", text=info.get("Name", ""))
        run_what = Adw.EntryRow(title="Command, or a website address (https://…)",
                                text=menu_edits.shown_exec(info.get("Exec", "")))
        icon = Adw.EntryRow(title="Icon (a name like web-browser, or a picture file)", text=info.get("Icon", ""))
        pick = Gtk.Button(icon_name="document-open-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                          tooltip_text="Choose a picture")

        def chosen(d, res):
            try:
                icon.set_text(d.open_finish(res).get_path())
            except GLib.Error:
                pass
        pick.connect("clicked", lambda *_: Gtk.FileDialog(title="Choose an icon").open(self, None, chosen))
        icon.add_suffix(pick)
        hidden = Adw.SwitchRow(title="Hide from the start menu", active=info.get("NoDisplay", "false") == "true")
        for r in (name, run_what, icon):
            lb.append(r)
        if app_id:
            lb.append(hidden)
        pin = Adw.SwitchRow(title="Pin to Start", active=app_id is None)
        if not app_id:
            lb.append(pin)

        d = Adw.AlertDialog(heading="Edit app" if app_id else "Add an app or website")
        d.set_extra_child(lb)
        d.add_response("cancel", "Cancel")
        if app_id and menu_edits.has_override(app_id):
            d.add_response("reset", "Remove my changes" if not menu_edits.is_custom_id(app_id) else "Delete")
            d.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        d.add_response("save", "Save")
        d.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        def respond(_d, r):
            if r == "save":
                if not name.get_text().strip() or not run_what.get_text().strip():
                    self.toast("An app needs a name and something to run")
                    return
                new_id = menu_edits.save(app_id, name.get_text().strip(), run_what.get_text().strip(),
                                         icon.get_text().strip(), hidden.get_active())
                if not app_id and pin.get_active():
                    cfg = start_menu.load_config()
                    if new_id not in cfg["pinned"]:
                        cfg["pinned"].append(new_id)
                    start_menu.save_config(cfg)
                self.toast("Saved")
            elif r == "reset":
                menu_edits.reset(app_id)
                self.toast("Back to how it was")
            else:
                return
            start_menu.save_config(start_menu.load_config())     # tells the start menu to refresh
            if getattr(self, "current", "") == "menu":
                self.reload()

        d.connect("response", respond)
        d.present(self)

    # --- Windows & desktops ------------------------------------------------------------------------

    def page_windows(self):
        page = Adw.PreferencesPage()
        lc = LabwcConfig()

        def yes(path):
            return lc.get(path, default_labwc(path, "yes")) == "yes"

        g = Adw.PreferencesGroup(title="Windows", description="Jace OS windows work like Windows: drag to the top "
                                                               "to maximize, to a side to snap.")
        g.add(switch_row("Drag to the top to maximize", None, yes("snapping/topMaximize"),
                         lambda on: lc.set("snapping/topMaximize", "yes" if on else "no")))
        mine = lc.root.find("snapping/range")
        snap_on = mine is None or mine.get("inner", "12") != "0"
        g.add(switch_row("Snap windows to the screen edges", "Drag a window to the left or right to fill half",
                         snap_on, lambda on: self._set_snap(lc, on)))
        g.add(combo_row("Window buttons", None,
                        [("icon:iconify,max,close", "Right, like Windows"), ("close,iconify,max:", "Left, like macOS")],
                        lc.get("theme/titlebar/layout", default_labwc("theme/titlebar/layout", "icon:iconify,max,close")),
                        lambda v: lc.set("theme/titlebar/layout", v)))
        g.add(switch_row("Focus follows the mouse", "Windows activate when you point at them",
                         lc.get("focus/followMouse", "no") == "yes",
                         lambda on: lc.set("focus/followMouse", "yes" if on else "no")))
        page.add(g)

        dg = Adw.PreferencesGroup(title="Desktops")
        spin = Adw.SpinRow.new_with_range(1, 9, 1)
        spin.set_title("Number of desktops")
        spin.set_subtitle("Switch with Win+Ctrl+Left/Right or Win+1…9")
        mine = lc.root.find("desktops")
        spin.set_value(int(mine.get("number", "4")) if mine is not None else 4)
        spin.connect("notify::value", lambda r, _p: self._set_desktops(lc, int(r.get_value())))
        dg.add(spin)
        page.add(dg)

        sg = Adw.PreferencesGroup(title="Scrolling", description="Off by default, so scrolling never moves you "
                                                                 "somewhere you didn't mean to go.")
        sg.add(switch_row("Scroll on the desktop to switch desktops", None, self._scroll_on(lc, "Root"),
                          lambda on: self._set_scroll(lc, "Root", on)))
        sg.add(switch_row("Scroll on a title bar to roll the window up", None, self._scroll_on(lc, "TitleBar"),
                          lambda on: self._set_scroll(lc, "TitleBar", on)))
        page.add(sg)

        page.add(group("", None, link_row("Keyboard shortcuts", "Change any shortcut, or add your own",
                                          ["jace-settings", "--page", "shortcuts"],
                                          "preferences-desktop-keyboard-shortcuts-symbolic")))
        return page

    @staticmethod
    def _set_snap(lc, on):
        rng = lc.node("snapping/range")
        rng.text = None
        rng.set("inner", "12" if on else "0")
        rng.set("outer", "12" if on else "0")
        lc.save()

    SCROLL_ACTIONS = {
        "Root": ('<action name="GoToDesktop" to="left" wrap="yes" />', '<action name="GoToDesktop" to="right" wrap="yes" />'),
        "TitleBar": ('<action name="Unshade" /><action name="Focus" />', '<action name="Unfocus" /><action name="Shade" />'),
    }

    @staticmethod
    def _scroll_context(lc, name):
        mouse = lc.node("mouse")
        ctx = next((c for c in mouse.findall("context") if c.get("name") == name), None)
        if ctx is None:
            ctx = ET.SubElement(mouse, "context", name=name)
        return ctx

    def _scroll_on(self, lc, name):
        ctx = next((c for c in lc.root.findall("mouse/context") if c.get("name") == name), None)
        if ctx is None:
            return False      # the Jace OS default: off
        up = next((m for m in ctx.findall("mousebind") if m.get("direction") == "Up"), None)
        return up is not None and up.find("action") is not None and up.find("action").get("name") != "None"

    def _set_scroll(self, lc, name, on):
        ctx = self._scroll_context(lc, name)
        for m in [m for m in ctx.findall("mousebind") if m.get("action") == "Scroll"]:
            ctx.remove(m)
        up, down = self.SCROLL_ACTIONS[name] if on else ('<action name="None" />', '<action name="None" />')
        for direction, actions in (("Up", up), ("Down", down)):
            mb = ET.SubElement(ctx, "mousebind", direction=direction, action="Scroll")
            for act in ET.fromstring(f"<x>{actions}</x>"):
                mb.append(act)
        lc.save()

    def _set_desktops(self, lc, n):
        d = lc.node("desktops")
        d.set("number", str(n))
        names = d.find("names")
        if names is not None:
            d.remove(names)
        names = ET.SubElement(d, "names")
        for i in range(1, n + 1):
            ET.SubElement(names, "name").text = str(i)
        lc.save()

    # --- Keyboard & mouse --------------------------------------------------------------------------

    def page_keyboard(self):
        page = Adw.PreferencesPage()
        env = LABWC_USER / "environment"
        values = {}
        if env.exists():
            for line in env.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    values[k.strip()] = v.strip()
        layouts = [("us", "English (US)"), ("gb", "English (UK)"), ("de", "German"), ("fr", "French"),
                   ("es", "Spanish"), ("latam", "Spanish (Latin America)"), ("it", "Italian"),
                   ("pt", "Portuguese"), ("br", "Portuguese (Brazil)"), ("nl", "Dutch"), ("be", "Belgian"),
                   ("ch", "Swiss"), ("se", "Swedish"), ("no", "Norwegian"), ("dk", "Danish"), ("fi", "Finnish"),
                   ("pl", "Polish"), ("cz", "Czech"), ("hu", "Hungarian"), ("ro", "Romanian"), ("tr", "Turkish"),
                   ("gr", "Greek"), ("ru", "Russian"), ("ua", "Ukrainian"), ("il", "Hebrew"), ("ara", "Arabic"),
                   ("in", "Indian"), ("jp", "Japanese"), ("kr", "Korean"), ("cn", "Chinese"), ("ca", "Canadian")]

        def set_env(key, value):
            values[key] = value
            LABWC_USER.mkdir(parents=True, exist_ok=True)
            env.write_text("# Written by Jace OS Settings (Keyboard & mouse).\n" +
                           "".join(f"{k}={v}\n" for k, v in values.items() if v))
            labwc_reload()
            self.toast("Keyboard changed")

        g = Adw.PreferencesGroup(title="Keyboard")
        g.add(combo_row("Layout", None, layouts, values.get("XKB_DEFAULT_LAYOUT", "us"),
                        lambda v: set_env("XKB_DEFAULT_LAYOUT", v)))
        g.add(combo_row("Switch layouts with", "If you use more than one layout",
                        [("", "Don't switch"), ("grp:win_space_toggle", "Win+Space"),
                         ("grp:alt_shift_toggle", "Alt+Shift")], values.get("XKB_DEFAULT_OPTIONS", ""),
                        lambda v: set_env("XKB_DEFAULT_OPTIONS", v)))
        page.add(g)

        lc = LabwcConfig()

        def dev_get(category, key, default):
            return lc.get(f"libinput/device[@category='{category}']/{key}", default)

        def dev_set(category, key, value):
            dev = next((d for d in lc.node("libinput").findall("device") if d.get("category") == category), None)
            if dev is None:
                dev = ET.SubElement(lc.node("libinput"), "device", category=category)
            node = dev.find(key)
            if node is None:
                node = ET.SubElement(dev, key)
            node.text = value
            lc.save()

        def touch(key, default):
            return dev_get("touchpad", key, default) == "yes"

        def set_touch(key, on):
            dev_set("touchpad", key, "yes" if on else "no")

        clip = Adw.PreferencesGroup(title="Clipboard", description="Win+V shows everything you copied.")
        clip.add(switch_row("Clipboard history", "Keep what you copy, so you can paste it again later",
                            clipboard.history_on(), clipboard.set_history))
        clip.add(switch_row("Paste right away", "Picking something in Win+V pastes it, not just copies it",
                            not (config_dir() / "clipboard-no-autopaste").exists(),
                            lambda on: (config_dir() / "clipboard-no-autopaste").unlink(missing_ok=True) if on
                            else (config_dir() / "clipboard-no-autopaste").touch()))
        clip.add(button_row("Clear clipboard history", "Pinned items stay", "Clear",
                            lambda: (clipboard.clear(), self.toast("Clipboard history cleared"))))
        page.add(clip)

        tg = Adw.PreferencesGroup(title="Touchpad")
        tg.add(switch_row("Tap to click", None, touch("tap", "yes"), lambda on: set_touch("tap", on)))
        tg.add(switch_row("Natural scrolling", "Content moves with your fingers", touch("naturalScroll", "yes"),
                          lambda on: set_touch("naturalScroll", on)))
        tg.add(switch_row("Turn off while typing", None, touch("disableWhileTyping", "yes"),
                          lambda on: set_touch("disableWhileTyping", on)))
        tg.add(self._slider_row("Touchpad speed", float(dev_get("touchpad", "pointerSpeed", "0")),
                                lambda v: dev_set("touchpad", "pointerSpeed", f"{v:.2f}")))
        page.add(tg)

        # Mice (labwc's "non-touch" devices): speed, acceleration, buttons, scrolling.
        mg = Adw.PreferencesGroup(title="Mouse")
        mg.add(self._slider_row("Pointer speed", float(dev_get("non-touch", "pointerSpeed", "0")),
                                lambda v: dev_set("non-touch", "pointerSpeed", f"{v:.2f}")))
        mg.add(combo_row("Acceleration", "Flat moves the pointer exactly as far as the mouse, good for games",
                         [("adaptive", "Adaptive (faster when you move quickly)"), ("flat", "Flat")],
                         dev_get("non-touch", "accelProfile", "adaptive"),
                         lambda v: dev_set("non-touch", "accelProfile", v)))
        mg.add(switch_row("Left-handed", "Swap the left and right buttons", dev_get("non-touch", "leftHanded", "no") == "yes",
                          lambda on: dev_set("non-touch", "leftHanded", "yes" if on else "no")))
        mg.add(switch_row("Natural scrolling", "Scroll the mouse wheel like a phone",
                          dev_get("non-touch", "naturalScroll", "no") == "yes",
                          lambda on: dev_set("non-touch", "naturalScroll", "yes" if on else "no")))
        mg.add(combo_row("Scroll speed", None, [("0.5", "Slow"), ("1.0", "Normal"), ("1.5", "Fast"), ("2.0", "Faster")],
                         dev_get("non-touch", "scrollFactor", "1.0"),
                         lambda v: dev_set("non-touch", "scrollFactor", v)))
        page.add(mg)

        # The pointer itself: theme and size, for apps (gsettings) and labwc (XCURSOR_*).
        cg = Adw.PreferencesGroup(title="Pointer", description="Changes show everywhere after you sign out and back in.")
        theme = gsettings_get("org.gnome.desktop.interface", "cursor-theme") or "Adwaita"
        size = gsettings_get("org.gnome.desktop.interface", "cursor-size") or "24"

        def set_cursor(new_theme=None, new_size=None):
            if new_theme:
                gsettings_set("org.gnome.desktop.interface", "cursor-theme", new_theme)
                values["XCURSOR_THEME"] = new_theme
            if new_size:
                gsettings_set("org.gnome.desktop.interface", "cursor-size", new_size)
                values["XCURSOR_SIZE"] = new_size
            LABWC_USER.mkdir(parents=True, exist_ok=True)
            env.write_text("# Written by Jace OS Settings (Keyboard & mouse).\n" +
                           "".join(f"{k}={v}\n" for k, v in values.items() if v))
            labwc_reload()
            self.toast("Pointer changed")

        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4, min_children_per_line=2,
                           column_spacing=10, row_spacing=10, homogeneous=True, margin_bottom=6)
        for name, folder in cursor_themes():
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=10, margin_bottom=10)
            preview = cursor_preview(folder)
            if preview:
                pic = Gtk.Picture.new_for_filename(preview)
                pic.set_size_request(40, 40)
                pic.set_content_fit(Gtk.ContentFit.CONTAIN)
                box.append(pic)
            else:
                box.append(Gtk.Image(icon_name="input-mouse-symbolic", pixel_size=32))
            box.append(Gtk.Label(label=name.replace("-", " "), ellipsize=Pango.EllipsizeMode.END, max_width_chars=14))
            b = Gtk.Button(child=box, css_classes=["jw-thumb"] + (["jw-current"] if name == theme else []),
                           tooltip_text=name)
            b.connect("clicked", lambda _b, n=name: (set_cursor(new_theme=n), self.reload()))
            flow.append(b)
        cg.add(flow)
        cg.add(combo_row("Pointer size", None, [("24", "Normal"), ("32", "Large"), ("40", "Larger"), ("48", "Largest")],
                         size, lambda v: set_cursor(new_size=v)))
        cg.add(link_row("Get more pointers", "Search Jace Store, Flathub and the AUR for cursor themes",
                        ["jace-settings", "--page", "apps"], "system-search-symbolic"))
        page.add(cg)
        return page

    @staticmethod
    def _slider_row(title, value, on_change):
        """A -1…1 speed slider (libinput's pointerSpeed); saves when you let go."""
        row = Adw.ActionRow(title=title)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -1, 1, 0.05)
        scale.set_value(max(-1.0, min(1.0, value)))
        scale.set_size_request(240, -1)
        scale.add_mark(0, Gtk.PositionType.BOTTOM, None)
        row.add_prefix(Gtk.Label(label="Slow", css_classes=["dim-label", "caption"]))
        row.add_suffix(scale)
        row.add_suffix(Gtk.Label(label="Fast", css_classes=["dim-label", "caption"]))
        pending = {"id": 0}

        def changed(s):
            if pending["id"]:
                GLib.source_remove(pending["id"])
            pending["id"] = GLib.timeout_add(400, lambda: (pending.update(id=0), on_change(s.get_value()), False)[2])
        scale.connect("value-changed", changed)
        return row

    # --- Keyboard shortcuts ---------------------------------------------------------------------------

    def page_shortcuts(self):
        page = Adw.PreferencesPage()
        data = shortcuts.load()
        top = Adw.PreferencesGroup(description="Click a shortcut to change it. Changes work right away.")
        add = Gtk.Button(label="Add a shortcut", css_classes=["suggested-action", "pill"], halign=Gtk.Align.START)
        add.connect("clicked", lambda *_: self._shortcut_dialog(data, None))
        top.add(add)
        page.add(top)
        for grp in shortcuts.GROUPS:
            g = Adw.PreferencesGroup(title=grp)
            for sid, g_name, title, _defaults, _actions in shortcuts.SHORTCUTS:
                if g_name == grp:
                    g.add(self._shortcut_row(data, sid, title))
            page.add(g)
        mine = Adw.PreferencesGroup(title="Your shortcuts", description="Shortcuts that run a command or open an app.")
        for c in data["custom"]:
            mine.add(self._shortcut_row(data, c["id"], c["title"], c["command"]))
        if not data["custom"]:
            mine.add(plain_row(title="None yet", subtitle="Use Add a shortcut to make one"))
        page.add(mine)
        page.add(group("", None, button_row("Reset all shortcuts", "Back to the Jace OS shortcuts", "Reset",
                                            lambda: self._shortcuts_reset_all(data))))
        return page

    def _shortcut_row(self, data, sid, title, command=None):
        keys = shortcuts.keys_for(data, sid)
        row = plain_row(title=title, subtitle=command or "", activatable=True)
        label = ", ".join(shortcuts.pretty(k) for k in keys) or "Off"
        row.add_suffix(Gtk.Label(label=label, css_classes=["dim-label"] + (["monospace"] if keys else [])))
        if sid in data["keys"]:
            row.add_suffix(Gtk.Image(icon_name="document-edit-symbolic", tooltip_text="Changed by you"))
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda *_: self._shortcut_dialog(data, sid))
        return row

    def _shortcuts_reset_all(self, data):
        data["keys"].clear()
        shortcuts.save(data)
        self.toast("Shortcuts reset")
        self.reload()

    def _shortcut_dialog(self, data, sid):
        """Change a shortcut (sid) or add one (sid None): press the keys, then Set."""
        custom = next((c for c in data["custom"] if c["id"] == sid), None)
        builtin = shortcuts.BY_ID.get(sid)
        title = builtin[2] if builtin else (custom["title"] if custom else "New shortcut")
        captured = {"key": None}

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        name = cmd = None
        if builtin is None:
            lb = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
            name = Adw.EntryRow(title="Name", text=custom["title"] if custom else "")
            cmd = Adw.EntryRow(title="Command to run (for example: jace-browser)", text=custom["command"] if custom else "")
            lb.append(name)
            lb.append(cmd)
            box.append(lb)
        current = shortcuts.keys_for(data, sid) if sid else []
        box.append(Gtk.Label(label="Now: " + (", ".join(shortcuts.pretty(k) for k in current) or "off"),
                             css_classes=["dim-label"]))
        press = Gtk.Label(label="Press the new shortcut…", css_classes=["title-2"], margin_top=6, margin_bottom=6)
        box.append(press)
        warn = Gtk.Label(css_classes=["caption", "warning"], wrap=True, visible=False)
        box.append(warn)

        d = Adw.AlertDialog(heading=title, body="Hold Win, Ctrl, Alt or Shift and press a key. "
                                                 "Tap Win alone for the Windows key.")
        d.set_extra_child(box)
        d.add_response("cancel", "Cancel")
        if sid and builtin:
            d.add_response("reset", "Reset")
        if sid:
            d.add_response("off", "Remove" if custom else "Turn off")
            d.set_response_appearance("off", Adw.ResponseAppearance.DESTRUCTIVE)
        d.add_response("set", "Set")
        d.set_response_appearance("set", Adw.ResponseAppearance.SUGGESTED)
        d.set_response_enabled("set", builtin is None and custom is not None)

        keyctl = Gtk.EventControllerKey()
        keyctl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def pressed(_c, keyval, _code, state):
            # Typing into the name and command boxes shouldn't count as a shortcut.
            if name is not None and (name.has_focus() or cmd.has_focus() or
                                     name.get_focus_child() or cmd.get_focus_child()):
                if not (state & (Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.CONTROL_MASK |
                                 Gdk.ModifierType.ALT_MASK)):
                    return False
            key = shortcuts.from_event(keyval, state)
            if key is None:
                return False
            if key == "Escape" and not captured["key"]:
                return False
            captured["key"] = key
            press.set_label(shortcuts.pretty(key))
            other = shortcuts.used_by(data, key, sid or "")
            warn.set_visible(bool(other))
            if other:
                warn.set_label(f"{shortcuts.pretty(key)} is used by “{other[1]}”. Set moves it here.")
            d.set_response_enabled("set", True)
            return True

        def released(_c, keyval, _code, state):
            # The Windows key on its own: set when it's let go without another key.
            from gi.repository import Gdk as _Gdk
            if _Gdk.keyval_name(keyval) in ("Super_L", "Super_R") and captured["key"] is None:
                captured["key"] = "Super_L"
                press.set_label("Win")
                d.set_response_enabled("set", True)

        keyctl.connect("key-pressed", pressed)
        keyctl.connect("key-released", released)
        d.add_controller(keyctl)

        def respond(_d, r):
            if r == "set":
                key = captured["key"]
                if builtin is None:
                    if not name.get_text().strip() or not cmd.get_text().strip():
                        self.toast("Give the shortcut a name and a command")
                        return
                    if custom:
                        custom["title"], custom["command"] = name.get_text().strip(), cmd.get_text().strip()
                        if key:
                            shortcuts.set_keys(data, sid, [key])
                    else:
                        shortcuts.add_custom(data, name.get_text().strip(), cmd.get_text().strip(),
                                             [key] if key else [])
                elif key:
                    shortcuts.set_keys(data, sid, [key])
            elif r == "off":
                if custom:
                    shortcuts.remove_custom(data, sid)
                else:
                    shortcuts.set_keys(data, sid, [])
            elif r == "reset":
                shortcuts.reset(data, sid)
            else:
                return
            shortcuts.save(data)
            self.toast("Shortcut saved")
            self.reload()

        d.connect("response", respond)
        d.present(self)

    # --- Top bar -------------------------------------------------------------------------------------

    def page_bar(self):
        page = Adw.PreferencesPage()
        cfg = bar.load()

        def change(fn):
            def apply(*args):
                fn(*args)
                bar.save(cfg)
            return apply

        look = Adw.PreferencesGroup(title="Bar")
        look.add(combo_row("Position", None, [("top", "Top of the screen"), ("bottom", "Bottom of the screen")],
                           cfg["position"], change(lambda v: cfg.__setitem__("position", v))))
        look.add(combo_row("Size", None, [("compact", "Compact"), ("normal", "Normal"), ("large", "Large")],
                           cfg["size"], change(lambda v: cfg.__setitem__("size", v))))
        page.add(look)

        cg = Adw.PreferencesGroup(title="Clock", description="Click the clock for the calendar (or press Win+N).")
        cg.add(switch_row("24-hour time", "Off shows 2:30 PM instead of 14:30", cfg["clock"]["24h"],
                          change(lambda on: self._set_24h(cfg, on))))
        cg.add(switch_row("Show seconds", None, cfg["clock"]["seconds"],
                          change(lambda on: cfg["clock"].__setitem__("seconds", on))))
        cg.add(switch_row("Show the date", None, cfg["clock"]["date"],
                          change(lambda on: cfg["clock"].__setitem__("date", on))))
        page.add(cg)

        for zone, title in (("left", "Left side"), ("center", "Middle"), ("right", "Right side")):
            g = Adw.PreferencesGroup(title=title)
            for mid, (name, about, z, _default) in bar.MODULES.items():
                if z != zone or mid == "clock":
                    continue
                g.add(switch_row(name, about, cfg["modules"].get(mid, False),
                                 change(lambda on, m=mid: cfg["modules"].__setitem__(m, on))))
            page.add(g)
        page.add(group("", None, button_row("Reset the top bar", "Back to the Jace OS layout", "Reset",
                                            lambda: (bar.save(json.loads(json.dumps(bar.DEFAULTS))), self.reload()))))
        return page

    @staticmethod
    def _set_24h(cfg, on):
        cfg["clock"]["24h"] = on
        # GNOME apps (Calendar, Files, …) use the same choice.
        gsettings_set("org.gnome.desktop.interface", "clock-format", "'24h'" if on else "'12h'")

    # --- Face unlock -----------------------------------------------------------------------------------

    def page_face(self):
        page = Adw.PreferencesPage()
        installed = which("howdy")
        cams = sorted(Path("/dev").glob("video*"))
        page.add(status_header("camera-web-symbolic", "Face unlock",
                               "Sign in and unlock by looking at your camera, like Windows Hello. Your password "
                               "always works too.", fallback="avatar-default-symbolic"))
        warn = Adw.PreferencesGroup()
        warn.add(plain_row(title="Best with an infrared (Windows Hello) camera",
                           subtitle="A normal webcam can be fooled by a photo of you. Use it for convenience, "
                                    "not for keeping a computer safe from people who know you."))
        page.add(warn)

        if not installed:
            g = Adw.PreferencesGroup(title="Set up")
            if not cams:
                g.add(plain_row(title="No camera found", subtitle="Plug one in, then come back here"))
            g.add(button_row("Install face unlock", "Installs Howdy from the AUR with yay. It builds face recognition "
                             "on this computer, which can take 20 minutes or more.", "Install",
                             lambda: spawn(["jace-terminal", "--", "/usr/lib/jace-os/then-wait",
                                            "yay", "-S", "--needed", "howdy-git"]),
                             suggested=True))
            page.add(g)
            return page

        cg = Adw.PreferencesGroup(title="Camera")
        current = ""
        for conf in ("/etc/howdy/config.ini", "/usr/lib/security/howdy/config.ini"):
            try:
                current = next((ln.split("=", 1)[1].strip() for ln in Path(conf).read_text().splitlines()
                                if ln.strip().startswith("device_path")), "")
                break
            except OSError:
                continue
        real = capture_cameras()
        set_camera = lambda v: background(  # noqa: E731
            lambda: subprocess.run(["pkexec", "/usr/bin/jace-face", "camera", v]).returncode,
            lambda r: (self.toast("Camera set" if r == 0 else "The camera wasn't changed"), self.reload()))
        if real and current not in [c for c, _l, _ir in real]:
            # e.g. /dev/video1 on many laptops: a metadata device with no picture ("Failed to read camera").
            best = next((c for c, _l, ir in real if ir), real[0][0])
            fix = Adw.Banner(title="Face unlock is using a camera that doesn't give a picture",
                             button_label="Use the right camera", revealed=True)
            fix.connect("button-clicked", lambda *_: set_camera(best))
            cg.add(fix)
        if real:
            cg.add(combo_row("Camera", "Pick the infrared one if your computer has one",
                             [(c, label + ("  · infrared" if ir else "")) for c, label, ir in real], current,
                             set_camera))
        else:
            cg.add(plain_row(title="No camera found"))
        cg.add(button_row("Add your face", "Look at the camera for a few seconds. Add it again with glasses, "
                          "in different light, and so on", "Add",
                          lambda: spawn(["jace-terminal", "--", "/usr/lib/jace-os/then-wait",
                                         "sudo", "howdy", "add"]),
                          suggested=True))
        cg.add(link_row("Test it", "Shows what the camera sees and whether it recognises you",
                        ["jace-terminal", "--", "/usr/lib/jace-os/then-wait", "sudo", "howdy", "test"],
                        "media-playback-start-symbolic"))
        page.add(cg)

        status = dict(ln.split() for ln in run(["jace-face", "status"]).splitlines() if len(ln.split()) == 2)
        ug = Adw.PreferencesGroup(title="Use face unlock for")
        for sid, title, about in (("lock", "The lock screen", "Press Enter on the lock screen, then look at the camera"),
                                  ("login", "Signing in", "On the login screen when the computer starts"),
                                  ("admin", "Admin prompts", "When an app asks for your password to change something"),
                                  ("sudo", "sudo in a terminal", None)):
            ug.add(switch_row(title, about, status.get(sid) == "on",
                              lambda on, s=sid: background(
                                  lambda: subprocess.run(["pkexec", "/usr/bin/jace-face",
                                                          "enable" if on else "disable", s]).returncode,
                                  lambda r: self.toast("Saved" if r == 0 else "That didn't work"))))
        page.add(ug)
        return page

    # --- Power -------------------------------------------------------------------------------------

    def page_power(self):
        page = Adw.PreferencesPage()
        bats = [p for p in Path("/sys/class/power_supply").glob("BAT*")]
        if bats:
            g = Adw.PreferencesGroup(title="Battery")
            for b in bats:
                cap = (b / "capacity").read_text().strip() if (b / "capacity").exists() else "?"
                status = (b / "status").read_text().strip() if (b / "status").exists() else ""
                row = plain_row(title=f"{cap}%", subtitle=status)
                lvl = Gtk.LevelBar(min_value=0, max_value=100, value=float(cap) if cap.isdigit() else 0,
                                   valign=Gtk.Align.CENTER, width_request=200)
                row.add_suffix(lvl)
                g.add(row)
            page.add(g)

        if which("powerprofilesctl"):
            cur = run(["powerprofilesctl", "get"]).strip()
            avail = [p for p in ("power-saver", "balanced", "performance") if p in run(["powerprofilesctl", "list"])]
            labels = {"power-saver": "Battery saver", "balanced": "Balanced", "performance": "Best performance"}
            page.add(group("Power mode", None,
                           combo_row("Power mode", None, [(p, labels[p]) for p in avail], cur,
                                     lambda v: subprocess.run(["powerprofilesctl", "set", v]))))

        idle = config_dir() / "idle"
        vals = {"LOCK": "600", "SCREEN": "900", "SUSPEND": "0"}
        if idle.exists():
            for line in idle.read_text().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    vals[k] = v

        def set_idle(key, v):
            vals[key] = v
            idle.write_text("".join(f"{k}={x}\n" for k, x in vals.items()))
            subprocess.run(["pkill", "-x", "swayidle"])
            spawn(["jace-idle"])

        times = [("0", "Never"), ("60", "1 minute"), ("300", "5 minutes"), ("600", "10 minutes"),
                 ("900", "15 minutes"), ("1800", "30 minutes"), ("3600", "1 hour")]
        g = Adw.PreferencesGroup(title="When you're away")
        g.add(combo_row("Lock the screen after", None, times, vals["LOCK"], lambda v: set_idle("LOCK", v)))
        g.add(combo_row("Turn off the screen after", None, times, vals["SCREEN"], lambda v: set_idle("SCREEN", v)))
        g.add(combo_row("Sleep after", None, times, vals["SUSPEND"], lambda v: set_idle("SUSPEND", v)))
        page.add(g)
        return page

    # --- Date & time -------------------------------------------------------------------------------

    def page_datetime(self):
        page = Adw.PreferencesPage()
        info = run(["timedatectl", "show"])
        props = dict(line.split("=", 1) for line in info.splitlines() if "=" in line)
        g = Adw.PreferencesGroup(title="Date &amp; time", description=GLib.DateTime.new_now_local().format("%A, %B %-d %Y  %H:%M"))
        g.add(switch_row("Set the time automatically", "From the internet", props.get("NTP") == "yes",
                         lambda on: subprocess.run(["timedatectl", "set-ntp", "true" if on else "false"])))
        zones = run(["timedatectl", "list-timezones"]).split()
        row = combo_row("Time zone", None, [(z, z.replace("_", " ")) for z in zones], props.get("Timezone", "UTC"),
                        lambda v: (subprocess.run(["timedatectl", "set-timezone", v]), self.toast(f"Time zone: {v}")))
        row.set_enable_search(True)
        g.add(row)
        cfg = bar.load()
        g.add(switch_row("24-hour time", "Off shows 2:30 PM instead of 14:30", cfg["clock"]["24h"],
                         lambda on: (self._set_24h(cfg, on), bar.save(cfg))))
        g.add(link_row("Clock and calendar on the top bar", None, ["jace-settings", "--page", "bar"],
                       "x-office-calendar-symbolic"))
        page.add(g)
        return page

    # --- Apps --------------------------------------------------------------------------------------

    def page_apps(self):
        page = Adw.PreferencesPage()

        # Jace Store, right here: search, install, open and remove apps and games from Jace Store,
        # Flathub and the Arch repositories. Installs show up in Jace Store's My apps too.
        sg = Adw.PreferencesGroup(title="Get apps", description="Search Jace Store, Flathub and Arch Linux. "
                                                                 "What you install here is in Jace Store's My apps too.")
        more = Gtk.Button(label="Open Jace Store", css_classes=["flat"])
        more.connect("clicked", lambda *_: store.open_in_store())
        sg.set_header_suffix(more)
        self.store_search = Gtk.SearchEntry(placeholder_text="Search for apps and games", margin_bottom=8)
        self.store_search.connect("search-changed", lambda *_: self._store_search())
        sg.add(self.store_search)
        self.store_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        sg.add(self.store_box)
        page.add(sg)
        self._store_search()

        jace = __import__("jaceos").JACE_APPS
        jg = Adw.PreferencesGroup(title="Jace apps", description="They come with Jace OS; updates are in Updates.")
        for _pkg, cmd, name, about in jace:
            jg.add(link_row(name, about, [cmd], cmd))
        page.add(jg)

        mine = [r for r in store.installed().values() if r.get("source") in store.SOURCE_NAMES]
        if mine:
            ig = Adw.PreferencesGroup(title="Installed from Jace Store",
                                      description="Also in Jace Store → My apps, which keeps them up to date.")
            for rec in sorted(mine, key=lambda r: (r.get("title") or "").lower()):
                app = store.from_record(rec)
                row = plain_row(title=app.title, subtitle=f"{store.SOURCE_NAMES[app.source]}"
                                + (f" · {app.version}" if app.version else ""))
                cmd = store.launch_command(app)
                if cmd:
                    b = Gtk.Button(label="Open", valign=Gtk.Align.CENTER)
                    b.connect("clicked", lambda *_, c=cmd: spawn(c))
                    row.add_suffix(b)
                rm = Gtk.Button(label="Remove", valign=Gtk.Align.CENTER, css_classes=["destructive-action"])
                rm.connect("clicked", lambda _b, a=app: self._store_remove(a))
                row.add_suffix(rm)
                ig.add(row)
            page.add(ig)

        g = Adw.PreferencesGroup(title="Default apps", description="Which app opens links and files.")
        terms = [(t, n) for t, n in (("ptyxis", "Ptyxis"), ("kgx", "Console"), ("foot", "foot"),
                                     ("alacritty", "Alacritty"), ("kitty", "kitty"), ("wezterm", "WezTerm"),
                                     ("ghostty", "Ghostty"), ("konsole", "Konsole")) if which(t)]
        if terms:
            conf = config_dir() / "terminal"
            current = conf.read_text().strip() if conf.exists() else "ptyxis"
            g.add(combo_row("Terminal", "Opened by Win+Enter, Ctrl+Alt+T and Open in Terminal", terms, current,
                            lambda v: (conf.write_text(v + "\n"), self.toast(f"Terminal: {v}"))))
        for title, types in (
            ("Web browser", ["x-scheme-handler/http", "x-scheme-handler/https", "text/html"]),
            ("Email", ["x-scheme-handler/mailto"]),
            ("Files", ["inode/directory"]),
            ("Text", ["text/plain"]),
            ("Pictures", ["image/png", "image/jpeg", "image/webp", "image/gif"]),
            ("Videos", ["video/mp4", "video/x-matroska", "video/webm"]),
            ("Music", ["audio/mpeg", "audio/flac", "audio/ogg"]),
            ("PDFs", ["application/pdf"]),
        ):
            apps = Gio.AppInfo.get_all_for_type(types[0])
            if not apps:
                continue
            default = Gio.AppInfo.get_default_for_type(types[0], False)
            opts = [(a.get_id(), a.get_display_name()) for a in apps]
            g.add(combo_row(title, None, opts, default.get_id() if default else None,
                            lambda v, t=types: self._set_default(v, t)))
        page.add(g)
        return page

    def _store_search(self):
        query = self.store_search.get_text().strip()
        self._store_query = query
        while (c := self.store_box.get_first_child()) is not None:
            self.store_box.remove(c)
        self.store_box.append(Gtk.Label(label="Searching…", css_classes=["dim-label"], xalign=0))
        # Wait for typing to pause before searching.
        if getattr(self, "_store_timer", None):
            GLib.source_remove(self._store_timer)

        def start():
            self._store_timer = None
            background(lambda: store.search(query),
                       lambda res: query == self._store_query and self._store_results(res))
            return False

        self._store_timer = GLib.timeout_add(350 if query else 0, start)

    def _store_results(self, results):
        if getattr(self, "current", "") != "apps":
            return
        while (c := self.store_box.get_first_child()) is not None:
            self.store_box.remove(c)
        if isinstance(results, Exception):
            self.store_box.append(Gtk.Label(label=str(results), css_classes=["dim-label"], xalign=0))
            return
        shown = False
        for source in ("jacestore", "flathub", "distro"):
            hits = results.get(source)
            if not hits:
                continue
            shown = True
            title = store.SOURCE_NAMES[source] + ("" if self._store_query or source != "flathub" else " · popular")
            self.store_box.append(Gtk.Label(label=title, xalign=0, css_classes=["heading"]))
            lb = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
            if isinstance(hits, Exception):
                lb.append(plain_row(title=f"Couldn't search {store.SOURCE_NAMES[source]}", subtitle=str(hits)))
            else:
                for app in hits[:8]:
                    lb.append(self._store_row(app))
            self.store_box.append(lb)
        if not shown:
            self.store_box.append(Gtk.Label(label="Nothing found. Try another search, or open Jace Store for "
                                                  "the AUR, AppImageHub and more.", wrap=True, xalign=0,
                                            css_classes=["dim-label"]))

    def _store_row(self, app):
        by = f"  ·  by {app.author}" if app.author else ""
        row = plain_row(title=app.title, subtitle=(app.summary or "")[:140] + by,
                        activatable=app.source == "jacestore")
        row.set_subtitle_lines(2)
        icon = Gtk.Image(icon_name="package-x-generic" if app.source == "distro" else "application-x-executable",
                         pixel_size=40)
        row.add_prefix(icon)
        if app.icon_url:
            def fetch(url=app.icon_url):
                import urllib.request
                req = urllib.request.Request(url, headers={"User-Agent": store.USER_AGENT})
                with urllib.request.urlopen(req, timeout=15) as r:
                    return GLib.Bytes.new(r.read(4 << 20))

            def show(data):
                if not isinstance(data, Exception):
                    try:
                        icon.set_from_paintable(Gdk.Texture.new_from_bytes(data))
                    except GLib.Error:
                        pass
            background(fetch, show)
        if app.source == "jacestore":
            row.connect("activated", lambda *_: store.open_in_store(app))

        state = store.status(app)
        if state == "bundled":
            row.add_suffix(Gtk.Label(label="Comes with Jace OS", css_classes=["dim-label", "caption"]))
        if state in ("bundled", "installed"):
            cmd = store.launch_command(app)
            if cmd:
                b = Gtk.Button(label="Open", valign=Gtk.Align.CENTER)
                b.connect("clicked", lambda *_: spawn(cmd))
                row.add_suffix(b)
            elif state == "installed":
                row.add_suffix(Gtk.Image(icon_name="emblem-ok-symbolic", css_classes=["success"],
                                         tooltip_text="Installed"))
        else:
            b = Gtk.Button(label="Install", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
            b.connect("clicked", lambda btn: self._store_install(btn, app))
            row.add_suffix(b)
        return row

    def _store_install(self, button, app):
        button.set_sensitive(False)
        button.set_label("Installing…")

        def progress(fraction):
            GLib.idle_add(lambda: (button.set_label(f"{fraction * 100:.0f}%"), False)[1])

        def done(result):
            if isinstance(result, store.StoreError) and str(result) == "open-in-store":
                self.toast(f"{app.title} installs from the Jace Store app")
                store.open_in_store(app)
            elif isinstance(result, Exception):
                self.toast(str(result))
            else:
                self.toast(result)
            if getattr(self, "current", "") == "apps":
                self.reload()

        background(lambda: store.install(app, progress), done)

    def _store_remove(self, app):
        background(lambda: store.uninstall(app), lambda r: (self.toast(str(r)), self.reload()))

    def _set_default(self, app_id, types):
        app = Gio.DesktopAppInfo.new(app_id)
        for t in types:
            try:
                app.set_as_default_for_type(t)
            except GLib.Error:
                pass
        if "x-scheme-handler/http" in types:
            subprocess.run(["xdg-settings", "set", "default-web-browser", app_id], capture_output=True)
        self.toast(f"{app.get_display_name()} is now the default")

    # --- Users -------------------------------------------------------------------------------------

    def page_users(self):
        page = Adw.PreferencesPage()
        me = profile.local_user()
        signed_in = account.load() is not None

        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_bottom=6)
        av = Adw.Avatar(size=96, text=me["name"], show_initials=True, halign=Gtk.Align.CENTER)
        if me["picture"]:
            try:
                av.set_custom_image(Gdk.Texture.new_from_filename(me["picture"]))
            except GLib.Error:
                pass
        top.append(av)
        top.append(Gtk.Label(label=me["name"], css_classes=["title-1"]))
        top.append(Gtk.Label(label=f"{me['username']} · {'Administrator' if me['admin'] else 'Standard user'}",
                             css_classes=["dim-label"]))
        g = Adw.PreferencesGroup()
        g.add(top)
        page.add(g)

        yg = Adw.PreferencesGroup(title="You")
        synced = signed_in and profile.sync_on()
        if synced:
            yg.set_description("Your name and picture come from your Jace account. Change them on Jace, "
                               "or turn off syncing below.")
        yg.add(button_row("Picture", "Pick one, make your own, or use a photo", "Change…",
                          self._picture_dialog))
        yg.add(button_row("Name", "Shown on the login screen and the top bar", "Change…", self._change_name))
        if signed_in:
            yg.add(switch_row("Use my Jace name and picture", "Keeps them the same as your Jace display name "
                              "and photo", profile.sync_on(), self._set_profile_sync))
        yg.add(link_row("Change your password", None, ["jace-terminal", "--", "/usr/lib/jace-os/then-wait", "passwd"],
                        "dialog-password-symbolic"))
        page.add(yg)

        og = Adw.PreferencesGroup(title="Other people")
        add = Gtk.Button(label="Add a person", css_classes=["flat"])
        add.connect("clicked", lambda *_: self._add_user())
        og.set_header_suffix(add)
        others = profile.other_users()
        for u in others:
            row = plain_row(title=u["name"], subtitle=f"{u['username']} · "
                                                      f"{'Administrator' if u['admin'] else 'Standard user'}")
            oav = Adw.Avatar(size=36, text=u["name"], show_initials=True)
            if u["picture"]:
                try:
                    oav.set_custom_image(Gdk.Texture.new_from_filename(u["picture"]))
                except GLib.Error:
                    pass
            row.add_prefix(oav)
            pwb = Gtk.Button(label="Set password", valign=Gtk.Align.CENTER)
            pwb.connect("clicked", lambda _b, user=u: self._set_user_password(user))
            row.add_suffix(pwb)
            rm = Gtk.Button(label="Delete", valign=Gtk.Align.CENTER, css_classes=["destructive-action"])
            rm.connect("clicked", lambda _b, user=u: self._delete_user(user))
            row.add_suffix(rm)
            og.add(row)
        if not others:
            og.add(plain_row(title="Nobody else uses this computer yet"))
        page.add(og)
        return page

    def _picture_dialog(self):
        """Pick a ready-made picture, build one (colours + emoji or letters), or use a file."""
        dialog = Adw.Dialog(title="Your picture", content_width=520, content_height=600)
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher(stack=stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)
        view.add_top_bar(header)
        cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "jace-os" / "avatars"
        cache.mkdir(parents=True, exist_ok=True)

        def use(design):
            out = cache / "chosen.png"
            avatars.render(design, str(out))
            self._apply_picture(str(out))
            dialog.close()

        # Ready-made
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4, min_children_per_line=4,
                           column_spacing=12, row_spacing=12, margin_top=18, margin_start=18, margin_end=18,
                           homogeneous=True, valign=Gtk.Align.START)
        for i, design in enumerate(avatars.PRESETS):
            f = cache / f"preset-{i}.png"
            if not f.exists():
                avatars.render(design, str(f), 160)
            av = Adw.Avatar(size=88)
            av.set_custom_image(Gdk.Texture.new_from_filename(str(f)))
            b = Gtk.Button(child=av, css_classes=["flat", "circular"])
            b.connect("clicked", lambda _b, d=design: use(d))
            flow.append(b)
        stack.add_titled_with_icon(Gtk.ScrolledWindow(child=flow, vexpand=True), "pick", "Pick one",
                                   "view-grid-symbolic")

        # Builder
        design = {"bg": ["#3ddc84", "#1f9d5a"], "emoji": "😀"}
        b_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=18, margin_start=18,
                        margin_end=18, margin_bottom=18)
        preview = Adw.Avatar(size=140, halign=Gtk.Align.CENTER)
        b_box.append(preview)

        def redraw():
            f = cache / "builder.png"
            avatars.render(design, str(f), 280)
            # From the bytes, so the preview can't keep showing an earlier version of the file.
            preview.set_custom_image(Gdk.Texture.new_from_bytes(GLib.Bytes.new(f.read_bytes())))

        def color_row(label, index):
            row = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
            row.append(Gtk.Label(label=label, css_classes=["dim-label"], width_chars=9, xalign=0))
            for c in avatars.COLORS:
                sw = Gtk.Button(css_classes=["circular", "jw-swatch"], width_request=26, height_request=26,
                                tooltip_text=c)
                prov = Gtk.CssProvider()
                prov.load_from_string(f"button {{ background: {c}; min-width: 26px; min-height: 26px; }}")
                sw.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

                def pick(_b, color=c):
                    if index == 0:
                        design["bg"][0] = color
                    elif len(design["bg"]) > 1:
                        design["bg"][1] = color
                    else:
                        design["bg"].append(color)
                    redraw()
                sw.connect("clicked", pick)
                row.append(sw)
            return row

        b_box.append(color_row("Colour", 0))
        gradient = Adw.SwitchRow(title="Fade into a second colour", active=True)
        second = color_row("Second", 1)

        def toggle_gradient(r, _p):
            second.set_sensitive(r.get_active())
            if r.get_active() and len(design["bg"]) == 1:
                design["bg"].append("#1f9d5a")
            elif not r.get_active():
                design["bg"] = design["bg"][:1]
            redraw()
        gradient.connect("notify::active", toggle_gradient)
        lb = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        lb.append(gradient)
        b_box.append(lb)
        b_box.append(second)

        what = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        emoji_btn = Gtk.Button(label="Choose an emoji…", css_classes=["pill"])
        chooser = Gtk.EmojiChooser()
        chooser.set_parent(emoji_btn)

        def emoji_picked(_c, text):
            design.pop("text", None)
            design["emoji"] = text
            redraw()
        chooser.connect("emoji-picked", emoji_picked)
        emoji_btn.connect("clicked", lambda *_: chooser.popup())
        letters = Gtk.Entry(placeholder_text="Or letters, like JC", max_length=2, width_chars=12)

        def letters_changed(e):
            if e.get_text().strip():
                design.pop("emoji", None)
                design["text"] = e.get_text()
                redraw()
        letters.connect("changed", letters_changed)
        what.append(emoji_btn)
        what.append(letters)
        b_box.append(what)
        save = Gtk.Button(label="Use this picture", css_classes=["pill", "suggested-action"], halign=Gtk.Align.CENTER)
        save.connect("clicked", lambda *_: use(dict(design, bg=list(design["bg"]))))
        b_box.append(save)
        redraw()
        stack.add_titled_with_icon(Gtk.ScrolledWindow(child=b_box, vexpand=True), "build", "Make your own",
                                   "applications-graphics-symbolic")

        # From a file
        f_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER)
        f_box.append(Gtk.Label(label="Use any photo. It's cropped to a square.", css_classes=["dim-label"]))
        fb = Gtk.Button(label="Choose a photo…", css_classes=["pill", "suggested-action"], halign=Gtk.Align.CENTER)
        fb.connect("clicked", lambda *_: (dialog.close(), self._choose_picture()))
        f_box.append(fb)
        stack.add_titled_with_icon(f_box, "file", "From a file", "image-x-generic-symbolic")

        view.set_content(stack)
        dialog.set_child(view)
        dialog.present(self)

    def _apply_picture(self, path):
        if account.load() and profile.sync_on():
            profile.set_sync(False)     # your own picture wins; turn syncing back on to use Jace's
        good = profile.set_picture(path)
        account._refresh_bar()
        self.toast("Picture changed" if good else "That picture couldn't be used")
        self.reload()

    def _choose_picture(self):
        dialog = Gtk.FileDialog(title="Choose a picture")
        filt = Gtk.FileFilter(name="Pictures")
        filt.add_mime_type("image/*")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(filt)
        dialog.set_filters(store)

        def done(d, res):
            try:
                path = d.open_finish(res).get_path()
            except GLib.Error:
                return
            self._apply_picture(path)

        dialog.open(self, None, done)

    def _change_name(self):
        me = profile.local_user()
        lb = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        entry = Adw.EntryRow(title="Your name", text=me["name"])
        lb.append(entry)
        d = Adw.AlertDialog(heading="Change your name")
        d.set_extra_child(lb)
        d.add_response("cancel", "Cancel")
        d.add_response("save", "Save")
        d.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        def respond(_d, r):
            if r != "save":
                return
            if account.load() and profile.sync_on():
                profile.set_sync(False)
            ok_ = profile.set_name(entry.get_text())
            account._refresh_bar()
            self.toast("Name changed" if ok_ else "Couldn't change your name")
            self.reload()

        d.connect("response", respond)
        d.present(self)

    def _set_profile_sync(self, on):
        profile.set_sync(on)
        if on:
            background(account.refresh_profile, lambda _r: (self.toast("Synced with Jace"), self.reload()))

    def _delete_user(self, user):
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        files = Adw.SwitchRow(title="Also delete their files", subtitle=f"Everything in /home/{user['username']}",
                              active=False)
        box.append(files)
        d = Adw.AlertDialog(heading=f"Delete {user['name']}?",
                            body="They won't be able to sign in any more. This can't be undone.")
        d.set_extra_child(box)
        d.add_response("cancel", "Cancel")
        d.add_response("delete", "Delete")
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_default_response("cancel")

        def respond(_d, r):
            if r != "delete":
                return
            background(lambda: profile.delete_user(user["uid"], files.get_active()),
                       lambda res: (self.toast(f"Deleted {user['name']}" if res[0] else f"Couldn't delete: {res[1]}"),
                                    self.reload()))

        d.connect("response", respond)
        d.present(self)

    def _add_user(self):
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        full = Adw.EntryRow(title="Full name")
        login = Adw.EntryRow(title="Username (lowercase, no spaces)")
        pw = Adw.PasswordEntryRow(title="Password")
        pw2 = Adw.PasswordEntryRow(title="Type it again")
        admin = Adw.SwitchRow(title="Administrator", subtitle="Can install apps and change settings for everyone")
        for r in (full, login, pw, pw2, admin):
            box.append(r)

        def fill_login(*_):
            guess = re.sub(r"[^a-z0-9_-]", "", (full.get_text().strip().lower().split() or [""])[0])[:32]
            if not login.get_text() or login.get_text() == getattr(login, "_auto", ""):
                login.set_text(guess)
                login._auto = guess
        full.connect("changed", fill_login)

        d = Adw.AlertDialog(heading="Add a person", body="They sign in with this username and password.")
        d.set_extra_child(box)
        d.add_response("cancel", "Cancel")
        d.add_response("add", "Add")
        d.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)

        def respond(_d, r):
            if r != "add":
                return
            u = login.get_text().strip()
            if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", u):
                self.toast("That username isn't valid")
                return
            if not pw.get_text() or pw.get_text() != pw2.get_text():
                self.toast("The passwords are empty or don't match")
                return
            cmd = ["pkexec", "/usr/bin/jace-users", "add", u, full.get_text().strip() or u,
                   "admin" if admin.get_active() else "standard"]
            password = pw.get_text()
            background(lambda: self._run_with_password(cmd, password),
                       lambda res: (self.toast(f"Added {u}" if res[0] else res[1]), self.reload()))

        d.connect("response", respond)
        d.present(self)

    @staticmethod
    def _run_with_password(cmd, password):
        r = subprocess.run(cmd, input=password + "\n", capture_output=True, text=True, timeout=120)
        return r.returncode == 0, (r.stderr.strip().splitlines() or ["That didn't work"])[-1]

    def _set_user_password(self, user):
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        pw = Adw.PasswordEntryRow(title="New password")
        pw2 = Adw.PasswordEntryRow(title="Type it again")
        box.append(pw)
        box.append(pw2)
        d = Adw.AlertDialog(heading=f"Set a password for {user['name']}")
        d.set_extra_child(box)
        d.add_response("cancel", "Cancel")
        d.add_response("set", "Set password")
        d.set_response_appearance("set", Adw.ResponseAppearance.SUGGESTED)

        def respond(_d, r):
            if r != "set":
                return
            if not pw.get_text() or pw.get_text() != pw2.get_text():
                self.toast("The passwords are empty or don't match")
                return
            cmd = ["pkexec", "/usr/bin/jace-users", "password", user["username"]]
            password = pw.get_text()
            background(lambda: self._run_with_password(cmd, password),
                       lambda res: self.toast(f"{user['name']} can sign in with the new password" if res[0]
                                              else res[1]))

        d.connect("response", respond)
        d.present(self)

    # --- About -------------------------------------------------------------------------------------

    def page_about(self):
        page = Adw.PreferencesPage()
        rel = os_release()
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_bottom=12)
        logo = Gtk.Picture.new_for_filename(str(LOGO))
        logo.set_size_request(112, 112)
        logo.set_can_shrink(True)
        logo.set_halign(Gtk.Align.CENTER)
        top.append(logo)
        top.append(Gtk.Label(label=rel.get("NAME", "Jace OS"), css_classes=["title-1"]))
        top.append(Gtk.Label(label=f"Version {rel.get('VERSION_ID', VERSION)} · based on Arch Linux",
                             css_classes=["dim-label"]))
        g = Adw.PreferencesGroup()
        g.add(top)
        page.add(g)

        cpu = next((ln.split(":", 1)[1].strip() for ln in run(["cat", "/proc/cpuinfo"]).splitlines()
                    if ln.startswith("model name")), "Unknown")
        mem_kb = next((int(ln.split()[1]) for ln in run(["cat", "/proc/meminfo"]).splitlines()
                       if ln.startswith("MemTotal")), 0)
        gpu = next((ln.split(": ", 1)[1] for ln in run(["lspci"]).splitlines()
                    if " VGA " in ln or "3D controller" in ln), "Unknown")
        st = os.statvfs("/")
        disk = f"{(st.f_blocks - st.f_bfree) * st.f_frsize / 1e9:.0f} GB used of {st.f_blocks * st.f_frsize / 1e9:.0f} GB"
        hw = Adw.PreferencesGroup(title="This computer")
        for title, value in (("Computer name", os.uname().nodename), ("Processor", cpu),
                             ("Memory", f"{mem_kb / 1048576:.1f} GB"), ("Graphics", gpu), ("Storage", disk),
                             ("Linux", os.uname().release), ("Desktop", "labwc (Wayland)")):
            row = plain_row(title=title, subtitle=value, subtitle_selectable=True)
            hw.add(row)
        page.add(hw)
        page.add(group("Jace OS", None,
                       link_row("Website", WEBSITE, WEBSITE, "web-browser-symbolic"),
                       link_row("Source code", "Jace OS is open source (MIT)", GITHUB, "text-x-script-symbolic"),
                       link_row("Report a problem", "On GitHub", f"{GITHUB}/issues", "dialog-warning-symbolic"),
                       link_row("Check for updates", None, ["jace-settings", "--page", "updates"],
                                "software-update-available-symbolic")))
        return page


class SettingsApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.jaceos.Settings", flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)

    def do_startup(self):
        Adw.Application.do_startup(self)
        load_css()

    def do_command_line(self, cmdline):
        args = cmdline.get_arguments()[1:]
        page = args[args.index("--page") + 1] if "--page" in args and args.index("--page") + 1 < len(args) else None
        win = self.get_active_window()
        if win is None:
            win = SettingsWindow(self, page or "account")
        elif page:
            win.open_page(page)
        win.present()
        if "--edit" in args and args.index("--edit") + 1 < len(args):
            win.open_page("menu")
            win._edit_app(args[args.index("--edit") + 1])
        return 0


def main():
    sys.exit(SettingsApp().run(sys.argv))
