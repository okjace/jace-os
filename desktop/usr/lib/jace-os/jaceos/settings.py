"""Jace OS Settings: every setting in one place.

  jace-settings [--page <name>]

Pages: account, updates, security, network, bluetooth, display, sound, appearance, menu,
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
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from jaceos import (BACKGROUNDS, GITHUB, JACE_URL, LOGO, VERSION, WEBSITE, account, background,  # noqa: E402
                    config_dir, load_css, ok, os_release, run, spawn, updates, which)
from jaceos import menu as start_menu  # noqa: E402
from jaceos import antivirus, store  # noqa: E402

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
    ("windows", "Windows & desktops", "preferences-desktop-apps-symbolic"),
    ("keyboard", "Keyboard & mouse", "input-keyboard-symbolic"),
    ("power", "Power & battery", "battery-good-symbolic"),
    ("datetime", "Date & time", "preferences-system-time-symbolic"),
    ("apps", "Apps", "application-x-addon-symbolic"),
    ("users", "Users", "system-users-symbolic"),
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
        self.content.set_content(builder())
        self.split.set_show_content(True)

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
                               "Jace OS, the Jace apps, Zen Browser and Flatpaks", spinner))
        background(updates.check, self._show_updates)
        return page

    def _show_updates(self, result):
        if getattr(self, "current", "") != "updates":
            return
        page = Adw.PreferencesPage()
        if isinstance(result, Exception):
            page.add(group("", None, plain_row(title="Couldn't check for updates", subtitle=str(result))))
            self.content.set_content(page)
            return

        n = updates.count(result)
        if result["reboot_required"]:
            banner = Adw.Banner(title="Restart to finish installing updates", button_label="Restart now",
                                revealed=True)
            banner.connect("button-clicked", lambda *_: spawn(["systemctl", "reboot"]))
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
                                                                   "Zen Browser and yay update through Jace OS.")
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
            for u in result["flatpak"]:
                fg.add(plain_row(title=u["title"], subtitle=u["new"] or u["name"]))
            page.add(fg)

        auto = Adw.PreferencesGroup(title="Automatic updates")
        auto.add(switch_row("Install updates automatically",
                            "Every few hours when plugged in: Linux, security fixes, Jace apps and everything else",
                            result["auto"], self._set_auto))
        auto.add(link_row("Update history", "/var/log/jace-update.log",
                          ["foot", "-e", "less", "+G", "/var/log/jace-update.log"], "document-open-recent-symbolic"))
        if result["last_error"]:
            auto.add(plain_row(title="The last update didn't finish",
                                   subtitle=result["last_error"].splitlines()[-1][:200]))
        page.add(auto)
        self.content.set_content(page)

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
        events = [e for e in antivirus.events() if e.get("file", "").startswith(str(Path.home()))]
        for ev in events[:15]:
            when = GLib.DateTime.new_from_unix_local(ev["time"]).format("%b %-d, %H:%M")
            row = plain_row(title=Path(ev["file"]).name, subtitle=f"{ev['virus']} · {when} · {ev['file']}")
            name = Path(ev["file"]).name
            rb = Gtk.Button(label="Restore", valign=Gtk.Align.CENTER)
            rb.connect("clicked", lambda _b, n=name, f=ev["file"]: self._av_helper(
                ["restore", n, f], f"Restored {n}", reload=True))
            db = Gtk.Button(label="Delete", valign=Gtk.Align.CENTER, css_classes=["destructive-action"])
            db.connect("clicked", lambda _b, n=name: self._av_helper(["delete", n], f"Deleted {n}", reload=True))
            row.add_suffix(rb)
            row.add_suffix(db)
            qg.add(row)
        if not mine and not events:
            qg.add(plain_row(title="Nothing in quarantine"))
        page.add(qg)
        return page

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
        gsettings_set("org.gnome.desktop.interface", "gtk-theme", "Adwaita-dark" if v == "prefer-dark" else "Adwaita")

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

        hg = Adw.PreferencesGroup(title="Hidden apps", description="Apps you hid from the start menu.")
        for app_id in cfg["hidden"]:
            app = start_menu.app_by_id(app_id)
            row = plain_row(title=app.get_display_name() if app else app_id)
            b = Gtk.Button(label="Show again", valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda _b, a=app_id: (cfg["hidden"].remove(a), start_menu.save_config(cfg),
                                                       self.reload()))
            row.add_suffix(b)
            hg.add(row)
        if not cfg["hidden"]:
            hg.add(plain_row(title="No hidden apps"))
        page.add(hg)

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
        snap_on = lc.get("snapping/range", default_labwc("snapping/range", "12")) != "0"
        g.add(switch_row("Snap windows to the screen edges", "Drag a window to the left or right to fill half",
                         snap_on, lambda on: lc.set("snapping/range", "12" if on else "0")))
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

        kg = Adw.PreferencesGroup(title="Keyboard shortcuts")
        for keys, what in (
            ("Windows key", "Open the start menu"),
            ("Alt+Tab", "Switch windows"),
            ("Win+← / Win+→", "Snap to the left or right half"),
            ("Win+↑ / Win+↓", "Maximize / restore or minimize"),
            ("Win+D", "Show the desktop"),
            ("Win+E", "Files"),
            ("Win+I", "Settings"),
            ("Win+L", "Lock"),
            ("Win+Shift+S", "Screenshot of an area"),
            ("Print Screen", "Screenshot of the whole screen"),
            ("Ctrl+Shift+Esc", "System Monitor (Task Manager)"),
            ("Ctrl+Alt+T", "Terminal"),
            ("Alt+F4", "Close the window"),
        ):
            row = plain_row(title=what)
            row.add_suffix(Gtk.Label(label=keys, css_classes=["dim-label", "monospace"]))
            kg.add(row)
        page.add(kg)
        return page

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

        def touch(key, default):
            return lc.get(f"libinput/device[@category='touchpad']/{key}", default) == "yes"

        def set_touch(key, on):
            dev = next((d for d in lc.node("libinput").findall("device") if d.get("category") == "touchpad"), None)
            if dev is None:
                dev = ET.SubElement(lc.node("libinput"), "device", category="touchpad")
            node = dev.find(key)
            if node is None:
                node = ET.SubElement(dev, key)
            node.text = "yes" if on else "no"
            lc.save()

        tg = Adw.PreferencesGroup(title="Touchpad")
        tg.add(switch_row("Tap to click", None, touch("tap", "yes"), lambda on: set_touch("tap", on)))
        tg.add(switch_row("Natural scrolling", "Content moves with your fingers", touch("naturalScroll", "yes"),
                          lambda on: set_touch("naturalScroll", on)))
        tg.add(switch_row("Turn off while typing", None, touch("disableWhileTyping", "yes"),
                          lambda on: set_touch("disableWhileTyping", on)))
        page.add(tg)
        cursor = gsettings_get("org.gnome.desktop.interface", "cursor-size") or "24"
        page.add(group("Mouse", None,
                       combo_row("Pointer size", None, [("24", "Normal"), ("32", "Large"), ("48", "Larger")], cursor,
                                 lambda v: gsettings_set("org.gnome.desktop.interface", "cursor-size", v))))
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
        user = os.environ.get("USER", "")
        full = run(["getent", "passwd", user]).split(":")
        name = full[4].split(",")[0] if len(full) > 4 and full[4] else user
        groups = run(["id", "-nG"]).split()
        g = Adw.PreferencesGroup(title="You")
        row = plain_row(title=name, subtitle=f"{user} · {'Administrator' if 'wheel' in groups else 'Standard user'}")
        row.add_prefix(Adw.Avatar(size=40, text=name, show_initials=True))
        g.add(row)
        g.add(link_row("Change your password", None, ["foot", "--title", "Change password", "-e", "passwd"],
                       "dialog-password-symbolic"))
        page.add(g)
        others = [ln.split(":") for ln in run(["getent", "passwd"]).splitlines()]
        others = [u for u in others if len(u) > 6 and u[2].isdigit() and 1000 <= int(u[2]) < 60000 and u[0] != user]
        og = Adw.PreferencesGroup(title="Other people")
        add = Gtk.Button(label="Add a person", css_classes=["flat"])
        add.connect("clicked", lambda *_: self._add_user())
        og.set_header_suffix(add)
        for u in others:
            og.add(plain_row(title=u[4].split(",")[0] or u[0], subtitle=u[0]))
        if not others:
            og.add(plain_row(title="Nobody else uses this computer yet"))
        page.add(og)
        return page

    def _add_user(self):
        box = Gtk.ListBox(css_classes=["boxed-list"])
        full = Adw.EntryRow(title="Full name")
        login = Adw.EntryRow(title="Username (lowercase, no spaces)")
        admin = Adw.SwitchRow(title="Administrator")
        for r in (full, login, admin):
            box.append(r)
        d = Adw.AlertDialog(heading="Add a person", body="They'll set their password the first time they sign in.")
        d.set_extra_child(box)
        d.add_response("cancel", "Cancel")
        d.add_response("add", "Add")
        d.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)

        def respond(_d, r):
            u = login.get_text().strip()
            if r != "add" or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", u):
                if r == "add":
                    self.toast("That username isn't valid")
                return
            cmd = ["pkexec", "sh", "-c",
                   'useradd -m -c "$1" -G "$2" "$3" && passwd -d "$3" && passwd -e "$3"', "sh",
                   full.get_text().strip() or u, "wheel" if admin.get_active() else "users", u]
            background(lambda: ok(cmd, timeout=120),
                       lambda good: (self.toast(f"Added {u}" if good is True else "Couldn't add them"), self.reload()))

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
        return 0


def main():
    sys.exit(SettingsApp().run(sys.argv))
