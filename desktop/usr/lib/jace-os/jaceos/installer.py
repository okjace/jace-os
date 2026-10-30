"""Install Jace OS: the graphical installer on the live USB.

  jace-installer

Steps: Welcome → Language & keyboard → Disk → Your account → Review → Installing → Done.
It erases the whole disk you pick (with optional encryption) and runs
/usr/lib/jace-os/install-backend as root, showing its progress. For dual-booting or
your own partitions, "Advanced install" opens archinstall with the Jace OS preset.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from jaceos import LOGO, ROOT, is_live, load_css, run, spawn  # noqa: E402

BACKEND = str(ROOT / "usr/lib/jace-os/install-backend")

LOCALES = [("en_US.UTF-8", "English (United States)"), ("en_GB.UTF-8", "English (United Kingdom)"),
           ("en_CA.UTF-8", "English (Canada)"), ("en_AU.UTF-8", "English (Australia)"),
           ("de_DE.UTF-8", "Deutsch"), ("fr_FR.UTF-8", "Français"), ("fr_CA.UTF-8", "Français (Canada)"),
           ("es_ES.UTF-8", "Español (España)"), ("es_MX.UTF-8", "Español (México)"), ("it_IT.UTF-8", "Italiano"),
           ("pt_BR.UTF-8", "Português (Brasil)"), ("pt_PT.UTF-8", "Português (Portugal)"),
           ("nl_NL.UTF-8", "Nederlands"), ("sv_SE.UTF-8", "Svenska"), ("nb_NO.UTF-8", "Norsk"),
           ("da_DK.UTF-8", "Dansk"), ("fi_FI.UTF-8", "Suomi"), ("pl_PL.UTF-8", "Polski"),
           ("cs_CZ.UTF-8", "Čeština"), ("hu_HU.UTF-8", "Magyar"), ("ro_RO.UTF-8", "Română"),
           ("tr_TR.UTF-8", "Türkçe"), ("el_GR.UTF-8", "Ελληνικά"), ("ru_RU.UTF-8", "Русский"),
           ("uk_UA.UTF-8", "Українська"), ("ar_EG.UTF-8", "العربية"), ("he_IL.UTF-8", "עברית"),
           ("hi_IN.UTF-8", "हिन्दी"), ("ja_JP.UTF-8", "日本語"), ("ko_KR.UTF-8", "한국어"),
           ("zh_CN.UTF-8", "中文 (简体)"), ("zh_TW.UTF-8", "中文 (繁體)")]
KEYBOARDS = [("us", "English (US)"), ("gb", "English (UK)"), ("de", "German"), ("fr", "French"),
             ("es", "Spanish"), ("latam", "Spanish (Latin America)"), ("it", "Italian"), ("pt", "Portuguese"),
             ("br", "Portuguese (Brazil)"), ("nl", "Dutch"), ("be", "Belgian"), ("ch", "Swiss"),
             ("se", "Swedish"), ("no", "Norwegian"), ("dk", "Danish"), ("fi", "Finnish"), ("pl", "Polish"),
             ("cz", "Czech"), ("hu", "Hungarian"), ("ro", "Romanian"), ("tr", "Turkish"), ("gr", "Greek"),
             ("ru", "Russian"), ("ua", "Ukrainian"), ("il", "Hebrew"), ("ara", "Arabic"), ("in", "Indian"),
             ("jp", "Japanese"), ("kr", "Korean"), ("cn", "Chinese"), ("ca", "Canadian")]


def disks() -> list[dict]:
    """Disks you can install to (not the USB stick Jace OS is running from)."""
    try:
        data = json.loads(run(["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,MODEL,TYPE,TRAN,RM,RO,FSTYPE,LABEL"]))
    except ValueError:
        return []
    live = run(["findmnt", "-no", "SOURCE", "/run/archiso/bootmnt"]).strip()
    live_disk = run(["lsblk", "-no", "PKNAME", live]).strip().splitlines()[0] if live else ""
    out = []
    for d in data.get("blockdevices", []):
        if d.get("type") != "disk" or d.get("ro") or d["name"] == live_disk or d["name"].startswith(("zram", "loop")):
            continue
        if int(d.get("size") or 0) < 20 * 1024**3:
            continue
        has = [c.get("label") or c.get("fstype") for c in d.get("children", []) if c.get("fstype")]
        out.append({"path": d["path"], "size": int(d["size"]), "model": (d.get("model") or "").strip() or "Disk",
                    "removable": bool(d.get("rm")) or d.get("tran") == "usb", "contents": [h for h in has if h]})
    return out


def human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{size:.0f} {unit}" if unit != "TB" else f"{size:.1f} TB"
        size /= 1000
    return ""


def current_timezone() -> str:
    try:
        return os.path.realpath("/etc/localtime").split("zoneinfo/", 1)[1]
    except (IndexError, OSError):
        return "UTC"


def online() -> bool:
    return subprocess.run(["curl", "-fsI", "--max-time", "6", "https://github.com"], capture_output=True).returncode == 0


class InstallerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Install Jace OS", default_width=820, default_height=640)
        self.set_icon_name("jace-os")
        self.choice = {"encrypt": False, "autologin": False, "locale": "en_US.UTF-8", "keyboard": "us",
                       "timezone": current_timezone()}
        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title="Install Jace OS")
        header.set_title_widget(self.title)
        view.add_top_bar(header)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vexpand=True)
        self.order = ["welcome", "language", "disk", "account", "review"]
        builders = {"welcome": self.page_welcome, "language": self.page_language, "disk": self.page_disk,
                    "account": self.page_account, "review": self.page_review}
        for name in self.order:
            self.stack.add_named(builders[name](), name)
        self.stack.add_named(self.page_progress(), "progress")
        self.stack.add_named(self.page_done(), "done")

        self.back = Gtk.Button(label="Back", css_classes=["pill"])
        self.back.connect("clicked", lambda *_: self.go(-1))
        self.next = Gtk.Button(label="Next", css_classes=["pill", "suggested-action"])
        self.next.connect("clicked", lambda *_: self.go(1))
        self.nav = Gtk.CenterBox(margin_start=24, margin_end=24, margin_bottom=18, margin_top=6)
        self.nav.set_start_widget(self.back)
        self.nav.set_end_widget(self.next)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.stack)
        box.append(self.nav)
        view.set_content(box)
        self.toasts.set_child(view)
        self.set_content(self.toasts)
        self.show_page("welcome")

    # --- helpers -----------------------------------------------------------------------------------

    @staticmethod
    def scrolled(child):
        clamp = Adw.Clamp(maximum_size=560, child=child, margin_top=12, margin_bottom=12,
                          margin_start=16, margin_end=16)
        return Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)

    @staticmethod
    def heading(title, text, icon=None):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_bottom=18)
        if icon == "logo":
            pic = Gtk.Picture.new_for_filename(str(LOGO))
            pic.set_size_request(96, 96)
            pic.set_can_shrink(True)
            pic.set_halign(Gtk.Align.CENTER)
            box.append(pic)
        elif icon:
            box.append(Gtk.Image(icon_name=icon, pixel_size=56, css_classes=["dim-label"]))
        box.append(Gtk.Label(label=title, css_classes=["title-1"], wrap=True, justify=Gtk.Justification.CENTER))
        box.append(Gtk.Label(label=text, css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER))
        return box

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=4))

    def show_page(self, name):
        self.stack.set_visible_child_name(name)
        self.current = name
        in_flow = name in self.order
        self.nav.set_visible(in_flow)
        if in_flow:
            i = self.order.index(name)
            self.back.set_visible(i > 0)
            self.next.set_label("Erase and install" if name == "review" else "Next")
            self.next.set_css_classes(["pill", "destructive-action" if name == "review" else "suggested-action"])
        if name == "review":
            self.fill_review()

    def go(self, delta):
        i = self.order.index(self.current)
        if delta > 0:
            problem = self.check(self.current)
            if problem:
                self.toast(problem)
                return
            if self.current == "review":
                self.confirm()
                return
        self.show_page(self.order[max(0, min(len(self.order) - 1, i + delta))])

    # --- pages -------------------------------------------------------------------------------------

    def page_welcome(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(self.heading("Install Jace OS", "This puts Jace OS on this computer's disk. It takes about "
                                "10 to 20 minutes, depending on your internet connection.", "logo"))
        self.net_banner = Adw.Banner(title="You're not connected to the internet. Jace OS needs it to install.",
                                     button_label="Connect to Wi-Fi")
        self.net_banner.connect("button-clicked", lambda *_: spawn(["jace-settings", "--page", "network"]))
        box.append(self.net_banner)
        GLib.timeout_add_seconds(1, self._check_net)

        lb = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        for icon, title, sub in (
            ("drive-harddisk-symbolic", "Erases the disk you choose", "Back up anything you want to keep first."),
            ("security-high-symbolic", "Optional disk encryption", "Keeps your files safe if the computer is lost."),
            ("view-app-grid-symbolic", "Everything's included", "Jace Launcher, Jace Social, Jace Store, Zen Browser, "
                                                               "virus protection and automatic updates."),
        ):
            row = Adw.ActionRow(title=title, subtitle=sub)
            row.add_prefix(Gtk.Image(icon_name=icon))
            lb.append(row)
        box.append(lb)

        adv = Gtk.Button(label="Advanced install (keep other systems, choose partitions)…",
                         css_classes=["flat"], halign=Gtk.Align.CENTER, margin_top=6)
        adv.connect("clicked", lambda *_: spawn(["jace-install", "--advanced"]))
        box.append(adv)
        return self.scrolled(box)

    def _check_net(self):
        from jaceos import background
        def done(ok):
            self._online = ok is True
            self.net_banner.set_revealed(not self._online)
        background(online, done)
        return self.current == "welcome"

    def page_language(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.heading("Language and keyboard", "You can change these later in Settings.",
                                "preferences-desktop-locale-symbolic"))
        g = Adw.PreferencesGroup()
        g.add(self.combo("Language", LOCALES, "locale"))
        g.add(self.combo("Keyboard", KEYBOARDS, "keyboard"))
        zones = run(["timedatectl", "list-timezones"]).split() or ["UTC"]
        tz = self.combo("Time zone", [(z, z.replace("_", " ")) for z in zones], "timezone")
        tz.set_enable_search(True)
        g.add(tz)
        test = Adw.EntryRow(title="Try your keyboard here")
        g.add(test)
        box.append(g)
        return self.scrolled(box)

    def combo(self, title, options, key):
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new([label for _v, label in options]))
        values = [v for v, _l in options]
        if self.choice.get(key) in values:
            row.set_selected(values.index(self.choice[key]))
        row.connect("notify::selected", lambda r, _p: self.choice.__setitem__(key, values[r.get_selected()]))
        return row

    def page_disk(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.heading("Where should Jace OS go?", "Everything on the disk you pick will be erased.",
                                "drive-harddisk-symbolic"))
        self.disk_group = Adw.PreferencesGroup(title="Disks")
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", css_classes=["flat"], tooltip_text="Look again")
        refresh.connect("clicked", lambda *_: self.fill_disks())
        self.disk_group.set_header_suffix(refresh)
        self.disk_list = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        self.disk_group.add(self.disk_list)
        box.append(self.disk_group)
        self.fill_disks()

        enc = Adw.PreferencesGroup(title="Encryption", margin_top=18)
        sw = Adw.SwitchRow(title="Encrypt the disk", subtitle="You'll type this password every time the computer starts")
        self.disk_pw = Adw.PasswordEntryRow(title="Encryption password", visible=False)
        self.disk_pw2 = Adw.PasswordEntryRow(title="Type it again", visible=False)

        def toggled(r, _p):
            self.choice["encrypt"] = r.get_active()
            self.disk_pw.set_visible(r.get_active())
            self.disk_pw2.set_visible(r.get_active())
        sw.connect("notify::active", toggled)
        for r in (sw, self.disk_pw, self.disk_pw2):
            enc.add(r)
        box.append(enc)
        return self.scrolled(box)

    def fill_disks(self):
        self.disk_list.remove_all()
        found = disks()
        first = None
        for d in found:
            sub = human(d["size"])
            if d["removable"]:
                sub += " · USB / removable"
            sub += " · has " + ", ".join(d["contents"][:3]) if d["contents"] else " · empty"
            row = Adw.ActionRow(title=GLib.markup_escape_text(f"{d['model']}  ({d['path']})"),
                                subtitle=GLib.markup_escape_text(sub), activatable=True)
            radio = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            if first is None:
                first = radio
                radio.set_active(True)
                self.choice["disk"] = d["path"]
            else:
                radio.set_group(first)
            radio.connect("toggled", lambda b, p=d["path"]: b.get_active() and self.choice.__setitem__("disk", p))
            row.add_prefix(radio)
            row.set_activatable_widget(radio)
            row.add_prefix(Gtk.Image(icon_name="drive-removable-media-symbolic" if d["removable"]
                                     else "drive-harddisk-symbolic"))
            self.disk_list.append(row)
        if not found:
            self.choice.pop("disk", None)
            self.disk_list.append(Adw.ActionRow(title="No disks found",
                                                subtitle="Jace OS needs a disk of at least 20 GB."))

    def page_account(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.heading("Your account", "You'll use this to sign in to Jace OS.", "avatar-default-symbolic"))
        g = Adw.PreferencesGroup()
        self.fullname = Adw.EntryRow(title="Your name")
        self.username = Adw.EntryRow(title="Username")
        self.hostname = Adw.EntryRow(title="Computer name")
        self.pw = Adw.PasswordEntryRow(title="Password")
        self.pw2 = Adw.PasswordEntryRow(title="Type it again")
        self._auto = {"user": "", "host": ""}

        def name_changed(*_):
            # Fill in the username and computer name until you type your own.
            user = re.sub(r"[^a-z0-9_-]", "", (self.fullname.get_text().strip().lower().split() or [""])[0])[:32]
            if self.username.get_text() in ("", self._auto["user"]):
                self.username.set_text(user)
                self._auto["user"] = user
            host = f"{user or 'jace'}-pc"
            if self.hostname.get_text() in ("", self._auto["host"]):
                self.hostname.set_text(host)
                self._auto["host"] = host
        self.fullname.connect("changed", name_changed)
        for r in (self.fullname, self.username, self.hostname, self.pw, self.pw2):
            g.add(r)
        auto = Adw.SwitchRow(title="Sign in automatically", subtitle="Skip the login screen when the computer starts")
        auto.connect("notify::active", lambda r, _p: self.choice.__setitem__("autologin", r.get_active()))
        g.add(auto)
        box.append(g)
        return self.scrolled(box)

    def page_review(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.heading("Ready to install", "Check everything, then click Erase and install.",
                                "emblem-default-symbolic"))
        self.review = Adw.PreferencesGroup()
        box.append(self.review)
        return self.scrolled(box)

    def fill_review(self):
        page = self.review.get_parent()
        new = Adw.PreferencesGroup()
        loc = dict(LOCALES).get(self.choice["locale"], self.choice["locale"])
        kb = dict(KEYBOARDS).get(self.choice["keyboard"], self.choice["keyboard"])
        disk = next((d for d in disks() if d["path"] == self.choice.get("disk")), None)
        for title, value in (
            ("Disk (will be erased)", f"{disk['model']} · {disk['path']} · {human(disk['size'])}" if disk else "?"),
            ("Encryption", "On" if self.choice["encrypt"] else "Off"),
            ("Language", loc), ("Keyboard", kb), ("Time zone", self.choice["timezone"]),
            ("Name", self.fullname.get_text()), ("Username", self.username.get_text()),
            ("Computer name", self.hostname.get_text()),
            ("Sign in automatically", "Yes" if self.choice["autologin"] else "No"),
            ("Boot", "UEFI" if Path("/sys/firmware/efi").exists() else "BIOS (legacy)"),
        ):
            new.add(Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(value or "—")))
        page.remove(self.review)
        page.append(new)
        self.review = new

    def check(self, page) -> str:
        if page == "welcome" and not getattr(self, "_online", True):
            return "Connect to the internet first (Connect to Wi-Fi)."
        if page == "disk":
            if not self.choice.get("disk"):
                return "Choose a disk."
            if self.choice["encrypt"]:
                if len(self.disk_pw.get_text()) < 6:
                    return "The encryption password needs at least 6 characters."
                if self.disk_pw.get_text() != self.disk_pw2.get_text():
                    return "The encryption passwords don't match."
        if page == "account":
            if not self.fullname.get_text().strip():
                return "Type your name."
            if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", self.username.get_text()):
                return "Usernames are lowercase letters, numbers, - and _."
            if self.username.get_text() in ("root", "jace-live", "greeter", "clamav", "nobody"):
                return "Choose a different username."
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,62}", self.hostname.get_text()):
                return "Computer names are letters, numbers and -."
            if not self.pw.get_text():
                return "Choose a password."
            if self.pw.get_text() != self.pw2.get_text():
                return "The passwords don't match."
        return ""

    def confirm(self):
        d = Adw.AlertDialog(heading="Erase the disk and install Jace OS?",
                            body=f"Everything on {self.choice['disk']} will be deleted. This can't be undone.")
        d.add_response("cancel", "Cancel")
        d.add_response("install", "Erase and install")
        d.set_response_appearance("install", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_default_response("cancel")
        d.connect("response", lambda _d, r: r == "install" and self.start())
        d.present(self)

    # --- installing --------------------------------------------------------------------------------

    def page_progress(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(self.heading("Installing Jace OS", "You can keep using the computer while this runs.", "logo"))
        self.bar = Gtk.ProgressBar(show_text=True)
        self.status = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"])
        box.append(self.bar)
        box.append(self.status)
        self.log = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
        exp = Gtk.Expander(label="Details", child=Gtk.ScrolledWindow(child=self.log, min_content_height=200))
        box.append(exp)
        self.retry = Gtk.Button(label="Try again", css_classes=["pill"], halign=Gtk.Align.CENTER, visible=False)
        self.retry.connect("clicked", lambda *_: self.show_page("review"))
        box.append(self.retry)
        return self.scrolled(box)

    def page_done(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(self.heading("Jace OS is installed", "Restart the computer and take out the USB stick. "
                                "Then sign in with the account you just made.", "logo"))
        restart = Gtk.Button(label="Restart now", css_classes=["pill", "suggested-action"], halign=Gtk.Align.CENTER)
        restart.connect("clicked", lambda *_: spawn(["systemctl", "reboot"]))
        later = Gtk.Button(label="Keep trying Jace OS", css_classes=["pill"], halign=Gtk.Align.CENTER)
        later.connect("clicked", lambda *_: self.close())
        box.append(restart)
        box.append(later)
        return self.scrolled(box)

    def start(self):
        self.choice.update(fullname=self.fullname.get_text().strip(), username=self.username.get_text(),
                           hostname=self.hostname.get_text(), password=self.pw.get_text(),
                           disk_password=self.disk_pw.get_text() if self.choice["encrypt"] else "")
        self.show_page("progress")
        self.title.set_subtitle(self.choice["disk"])
        self.retry.set_visible(False)
        self.bar.set_fraction(0)
        self.status.set_label("Starting…")
        self.log.get_buffer().set_text("")
        cmd = (["sudo", "-n", BACKEND] if is_live() else ["pkexec", BACKEND])
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, bufsize=1)
        except OSError as e:
            self.failed(str(e))
            return
        self.proc.stdin.write(json.dumps(self.choice))
        self.proc.stdin.close()
        # The passwords aren't needed here any more.
        self.choice["password"] = self.choice["disk_password"] = ""
        self.finished = False
        GLib.io_add_watch(self.proc.stdout, GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP, self.read)
        self.inhibit = self.get_application().inhibit(self, Gtk.ApplicationInhibitFlags.LOGOUT |
                                                      Gtk.ApplicationInhibitFlags.SUSPEND, "Installing Jace OS")

    def read(self, stream, _cond):
        line = stream.readline()
        if not line:
            code = self.proc.wait()
            if not self.finished:
                self.failed(f"The installer stopped (code {code}).")
            return False
        line = line.rstrip("\n")
        if line.startswith("@progress "):
            _, pct, text = (line.split(" ", 2) + [""])[:3]
            self.bar.set_fraction(min(1, int(pct) / 100))
            self.status.set_label(text)
        elif line.startswith("@error "):
            self.finished = True
            self.failed(line[7:])
        elif line == "@done":
            self.finished = True
            self.get_application().uninhibit(self.inhibit)
            self.show_page("done")
        else:
            buf = self.log.get_buffer()
            buf.insert(buf.get_end_iter(), line + "\n")
        return True

    def failed(self, message):
        self.status.set_label(f"Jace OS couldn't be installed: {message}")
        self.retry.set_visible(True)
        try:
            self.get_application().uninhibit(self.inhibit)
        except (AttributeError, TypeError):
            pass


class InstallerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.jaceos.Installer", flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Adw.Application.do_startup(self)
        load_css()

    def do_activate(self):
        (self.get_active_window() or InstallerWindow(self)).present()


def main():
    sys.exit(InstallerApp().run(sys.argv))
