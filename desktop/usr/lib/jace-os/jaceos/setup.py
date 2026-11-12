"""Setup: the first thing you see when Jace OS starts for the first time (and after a
factory reset). Full screen, like setting up a phone:

  Hello → Language → Keyboard → Wi-Fi → Jace account → Sync (if you signed in) → Time zone →
  Your account → Look → Setting up → Welcome

Picking a language switches Setup to it right away (jaceos/i18n.py). It runs as the
jace-setup user in cage (see /usr/share/jace-os/greetd/setup.toml) and applies your choices
with /usr/lib/jace-os/setup-backend, then signs you in.

  jace-setup          run it
  jace-setup --demo   try it without changing anything (no account is made)

On a computer that's already set up, it always runs as the demo: Setup only does anything
for real the first time Jace OS starts (or after a factory reset).
"""
import base64
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from jaceos import LOGO, ROOT, SHARE, account, cloudsync, filesync, i18n, run, spawn  # noqa: E402
from jaceos.i18n import _  # noqa: E402
from jaceos.installer import KEYBOARDS, LOCALES, current_timezone  # noqa: E402

BACKEND = str(ROOT / "usr/lib/jace-os/setup-backend")
# The real thing only while the computer is waiting to be set up; any other time it's the demo,
# so opening it by hand (even as "jace-setup demo") never asks for your password.
DEMO = bool({"--demo", "demo"} & set(sys.argv[1:])) or not Path("/var/lib/jace-os/setup-pending").exists()

HELLOS = ["Hello", "Hola", "Bonjour", "Hallo", "Ciao", "Olá", "Hej", "Merhaba", "Привет", "こんにちは",
          "안녕하세요", "你好", "नमस्ते", "مرحبا", "Γεια σας", "Cześć"]

# Good first guesses for the next pages from the language you picked.
ZONE_GUESS = {"en_US": "America/New_York", "en_GB": "Europe/London", "en_CA": "America/Toronto",
              "en_AU": "Australia/Sydney", "de_DE": "Europe/Berlin", "fr_FR": "Europe/Paris",
              "fr_CA": "America/Toronto", "es_ES": "Europe/Madrid", "es_MX": "America/Mexico_City",
              "it_IT": "Europe/Rome", "pt_BR": "America/Sao_Paulo", "pt_PT": "Europe/Lisbon",
              "nl_NL": "Europe/Amsterdam", "sv_SE": "Europe/Stockholm", "nb_NO": "Europe/Oslo",
              "da_DK": "Europe/Copenhagen", "fi_FI": "Europe/Helsinki", "pl_PL": "Europe/Warsaw",
              "cs_CZ": "Europe/Prague", "hu_HU": "Europe/Budapest", "ro_RO": "Europe/Bucharest",
              "tr_TR": "Europe/Istanbul", "el_GR": "Europe/Athens", "ru_RU": "Europe/Moscow",
              "uk_UA": "Europe/Kyiv", "ar_EG": "Africa/Cairo", "he_IL": "Asia/Jerusalem",
              "hi_IN": "Asia/Kolkata", "ja_JP": "Asia/Tokyo", "ko_KR": "Asia/Seoul",
              "zh_CN": "Asia/Shanghai", "zh_TW": "Asia/Taipei"}
KEYBOARD_GUESS = {"en_US": "us", "en_GB": "gb", "en_CA": "us", "en_AU": "us", "de_DE": "de", "fr_FR": "fr",
                  "fr_CA": "ca", "es_ES": "es", "es_MX": "latam", "it_IT": "it", "pt_BR": "br", "pt_PT": "pt",
                  "nl_NL": "us", "sv_SE": "se", "nb_NO": "no", "da_DK": "dk", "fi_FI": "fi", "pl_PL": "pl",
                  "cs_CZ": "cz", "hu_HU": "hu", "ro_RO": "ro", "tr_TR": "tr", "el_GR": "gr", "ru_RU": "ru",
                  "uk_UA": "ua", "ar_EG": "ara", "he_IL": "il", "hi_IN": "in", "ja_JP": "jp", "ko_KR": "kr",
                  "zh_CN": "cn", "zh_TW": "cn"}

ORDER = ["hello", "language", "keyboard", "wifi", "jace", "sync", "timezone", "account", "look", "working",
         "welcome"]
DOTS = ORDER[1:ORDER.index("working")]          # the pages that get a progress dot


def backend(command: str, data: dict, on_line=None) -> tuple[bool, str]:
    """Runs setup-backend <command> with data as JSON. Returns (worked, error message)."""
    if DEMO:
        if command == "finish" and on_line:
            for pct, text in ((10, "Setting your language"), (35, "Setting your keyboard and time zone"),
                              (60, "Making your account"), (80, "Choosing your look"), (100, "Done")):
                GLib.usleep(700_000)
                on_line(f"@progress {pct} {text}")
        else:
            GLib.usleep(1_200_000)
        return True, ""
    try:
        proc = subprocess.Popen(["pkexec", BACKEND, command], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
    except OSError as e:
        return False, str(e)
    proc.stdin.write(json.dumps(data))
    proc.stdin.close()
    error = ""
    for line in proc.stdout:
        line = line.rstrip("\n")
        if line.startswith("@error "):
            error = line[7:]
        elif on_line:
            on_line(line)
    code = proc.wait()
    return code == 0 and not error, error or (_("Setup stopped.") if code else "")


def in_thread(work, done):
    """Runs work() away from the screen, then done(result) on it."""
    def go():
        result = work()
        GLib.idle_add(lambda: (done(result), False)[1])
    threading.Thread(target=go, daemon=True).start()


def online() -> bool:
    if DEMO:
        return False
    return run(["nmcli", "-t", "networking", "connectivity"]).strip() == "full"


def wifi_networks() -> list[dict]:
    if DEMO:
        return [{"ssid": "Home Wi-Fi", "signal": 92, "secure": True, "in_use": False},
                {"ssid": "Jace's Phone", "signal": 70, "secure": True, "in_use": False},
                {"ssid": "Coffee Shop", "signal": 41, "secure": False, "in_use": False}]
    out = run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "auto"],
              timeout=30)
    seen, nets = set(), []
    for line in out.splitlines():
        parts = re.split(r"(?<!\\):", line)
        if len(parts) < 4:
            continue
        ssid = parts[1].replace("\\:", ":")
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        nets.append({"ssid": ssid, "signal": int(parts[2] or 0), "secure": parts[3] not in ("", "--"),
                     "in_use": parts[0] == "*"})
    return sorted(nets, key=lambda n: -n["signal"])


def signal_icon(strength: int) -> str:
    level = "excellent" if strength > 75 else "good" if strength > 50 else "ok" if strength > 25 else "weak"
    return f"network-wireless-signal-{level}-symbolic"


def spinner(size):
    if hasattr(Adw, "Spinner"):
        return Adw.Spinner(width_request=size, height_request=size, halign=Gtk.Align.CENTER)
    return Gtk.Spinner(spinning=True, width_request=size, height_request=size, halign=Gtk.Align.CENTER)


class SetupWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Jace OS", default_width=1100, default_height=760,
                         css_classes=["js-window"])
        self.set_icon_name("jace-os")
        self.choice = {"locale": "en_US.UTF-8", "keyboard": "us", "timezone": current_timezone() or "UTC",
                       "dark": True}
        i18n.set_language(self.choice["locale"])
        self.current = "hello"

        overlay = Gtk.Overlay()
        overlay.set_child(Gtk.Box(css_classes=["js-bg"], hexpand=True, vexpand=True))
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        overlay.add_overlay(layout)

        # Top: back, and a dot for each step (the current one grows into a pill).
        top = Gtk.CenterBox(margin_top=22, margin_start=26, margin_end=26)
        self.back = Gtk.Button(icon_name="go-previous-symbolic", css_classes=["circular", "js-back"])
        self.back.connect("clicked", lambda *_a: self.go(-1))
        top.set_start_widget(self.back)
        self.dots = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        self.dot = {}
        for name in DOTS:
            d = Gtk.Box(css_classes=["js-dot"], valign=Gtk.Align.CENTER)
            self.dot[name] = d
            self.dots.append(d)
        top.set_center_widget(self.dots)
        layout.append(top)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, transition_duration=550,
                               vexpand=True, hhomogeneous=False, vhomogeneous=False)
        self.pages = {}
        for name in ORDER:
            self.build(name)
        layout.append(self.stack)

        # Bottom: the big Continue button.
        self.cont = Gtk.Button(css_classes=["pill", "suggested-action", "js-big"],
                               halign=Gtk.Align.CENTER, margin_bottom=40, margin_top=12)
        self.cont.connect("clicked", lambda *_a: self.go(1))
        layout.append(self.cont)
        self.retranslate_chrome()

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        self.toasts = Adw.ToastOverlay(child=overlay)
        self.set_content(self.toasts)
        self.show("hello")

        # For screenshots and tests: step through the pages by itself (--demo only).
        tour = os.environ.get("JACE_SETUP_TOUR")
        if DEMO and tour:
            GLib.timeout_add(int(float(tour) * 1000), self.tour_step)

    def build(self, name):
        """(Re)builds one page, in the current language."""
        old = self.pages.get(name)
        page = getattr(self, f"page_{name}")()
        self.pages[name] = page
        if old is not None:
            self.stack.remove(old)
        self.stack.add_named(page, name)

    def retranslate_chrome(self):
        self.back.set_tooltip_text(_("Back"))
        self.cont.set_label(_("Continue"))

    def change_language(self, locale):
        """Picking a language switches Setup to it at once (the greetings stay as they are)."""
        i18n.set_language(locale)
        Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL if i18n.is_rtl() else Gtk.TextDirection.LTR)
        self.retranslate_chrome()
        for name in ORDER:
            if name not in ("hello", "language"):
                self.build(name)
        self.language_title.set_label(_("Language"))
        self.language_sub.set_label(_("Choose the language for Jace OS."))

    def tour_step(self):
        if os.environ.get("JACE_SETUP_TOUR_LANGUAGE") and self.current == "language":
            loc = os.environ["JACE_SETUP_TOUR_LANGUAGE"]
            self.choice["locale"] = loc
            self.language_list.pick(loc)
        if self.current == "account":
            self.fullname.set_text("Jace Cannady")
            self.pw.set_text("demo-password")
            self.pw2.set_text("demo-password")
        if self.current in ("working", "welcome"):
            return self.current != "welcome"
        self.go(1)
        return True

    # --- moving between pages ---------------------------------------------------------------------

    def show(self, name, backwards=False):
        if name != "jace" and getattr(self, "current", "") == "jace":
            attempt = getattr(self, "jace_attempt", None)
            if attempt and attempt["running"]:
                attempt["cancel"] = True
        self.current = name
        rtl = Gtk.Widget.get_default_direction() == Gtk.TextDirection.RTL
        forward = Gtk.StackTransitionType.SLIDE_RIGHT if rtl else Gtk.StackTransitionType.SLIDE_LEFT
        backward = Gtk.StackTransitionType.SLIDE_LEFT if rtl else Gtk.StackTransitionType.SLIDE_RIGHT
        self.stack.set_transition_type(backward if backwards else forward)
        self.stack.set_visible_child_name(name)
        page = self.pages[name]
        # Replay the page's entrance: its parts rise and fade in one after another (setup.css).
        page.remove_css_class("js-play")
        GLib.timeout_add(30, lambda: (page.add_css_class("js-play"), False)[1])
        in_flow = name in DOTS
        self.back.set_opacity(1 if in_flow and name != "language" else 0)
        self.back.set_sensitive(in_flow and name != "language")
        self.dots.set_opacity(1 if in_flow else 0)
        for n, d in self.dot.items():
            (d.add_css_class if n == name else d.remove_css_class)("js-dot-on")
            (d.add_css_class if in_flow and DOTS.index(n) < DOTS.index(name) else d.remove_css_class)("js-dot-done")
        self.cont.set_visible(in_flow)
        self.validate()
        enter = getattr(self, f"enter_{name}", None)
        if enter:
            enter()

    def go(self, delta):
        i = ORDER.index(self.current)
        if delta > 0 and not self.validate():
            return
        if delta > 0 and self.current == "look":
            self.show("working")
            self.finish()
            return
        if delta > 0 and self.current == "sync" and self.sync_wanted() and not getattr(self, "sync_key", None):
            self.sync_check()                     # checks the passphrase, then moves on by itself
            return
        target = ORDER[max(1, min(len(ORDER) - 1, i + delta))]
        if target == "sync" and not getattr(self, "jace", None):
            target = ORDER[ORDER.index("sync") + (1 if delta > 0 else -1)]   # sync needs a Jace sign-in
        self.show(target, backwards=delta < 0)

    def validate(self) -> bool:
        ok = True
        if self.current == "account":
            ok = not self.account_problem()
        self.cont.set_sensitive(ok)
        return ok

    def on_key(self, _c, keyval, _code, _state):
        modifiers = (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R, Gdk.KEY_Control_L, Gdk.KEY_Control_R, Gdk.KEY_Alt_L,
                     Gdk.KEY_Alt_R, Gdk.KEY_Super_L, Gdk.KEY_Super_R)
        if self.current == "hello" and keyval not in modifiers:
            self.go(1)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.current in DOTS:
            # Enter in a text box, or on Jace's sign-in page, is theirs: it doesn't move on.
            if self.current == "jace" and self.jace_state.get_visible_child_name() in ("web", "wait"):
                return False
            if not isinstance(self.get_focus(), Gtk.Text):
                self.go(1)
                return True
        return False

    # --- page parts -------------------------------------------------------------------------------

    @staticmethod
    def page_box(*children, width=560):
        """A page: its parts centred, each rising in a moment after the one before."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, valign=Gtk.Align.CENTER)
        for i, child in enumerate(children):
            child.add_css_class("js-in")
            child.add_css_class(f"js-d{min(i + 1, 6)}")
            box.append(child)
        clamp = Adw.Clamp(maximum_size=width, child=box, margin_start=24, margin_end=24, margin_top=8,
                          margin_bottom=8, valign=Gtk.Align.CENTER)
        # On small screens a tall page (your account, filled in) scrolls instead of being cut off.
        page = Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                  css_classes=["js-page"])
        return page

    @staticmethod
    def title(text, sub=None, icon=None):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_bottom=10)
        if icon:
            box.append(Gtk.Image(icon_name=icon, pixel_size=64, css_classes=["js-icon"]))
        box.title = Gtk.Label(label=text, css_classes=["js-title"], wrap=True, justify=Gtk.Justification.CENTER)
        box.append(box.title)
        box.sub = None
        if sub:
            box.sub = Gtk.Label(label=sub, css_classes=["js-sub"], wrap=True, justify=Gtk.Justification.CENTER)
            box.append(box.sub)
        return box

    @staticmethod
    def logo(size):
        img = Gtk.Image.new_from_file(str(LOGO))
        img.set_pixel_size(size)
        img.set_halign(Gtk.Align.CENTER)
        return img

    def choice_list(self, options, key, on_pick=None, height=330):
        """A list you pick one thing from; the picked one gets a check that pops in."""
        lb = Gtk.ListBox(css_classes=["boxed-list", "js-list"], selection_mode=Gtk.SelectionMode.NONE)
        checks = {}
        for value, label in options:
            row = Adw.ActionRow(title=GLib.markup_escape_text(label), activatable=True)
            row.value = value
            check = Gtk.Image(icon_name="object-select-symbolic", css_classes=["js-check"])
            check.set_opacity(1 if self.choice.get(key) == value else 0)
            checks[value] = check
            row.add_suffix(check)
            row.connect("activated", lambda _r, v=value: pick(v))
            lb.append(row)

        def pick(v):
            self.choice[key] = v
            for val, c in checks.items():
                c.set_opacity(1 if val == v else 0)
                (c.add_css_class if val == v else c.remove_css_class)("js-pop")
            if on_pick:
                on_pick(v)
        lb.pick = pick
        sw = Gtk.ScrolledWindow(child=lb, hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=height,
                                max_content_height=height, propagate_natural_height=True, css_classes=["js-scroll"])
        return sw, lb

    # --- pages ------------------------------------------------------------------------------------

    def page_hello(self):
        self.hello = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=900)
        for i, word in enumerate(HELLOS):
            self.hello.add_named(Gtk.Label(label=word, css_classes=["js-hello"]), str(i))
        self.hello_i = 0
        GLib.timeout_add(2400, self.next_hello)
        start = Gtk.Button(label="Get started", css_classes=["pill", "suggested-action", "js-big"],
                           halign=Gtk.Align.CENTER, margin_top=30)
        start.connect("clicked", lambda *_a: self.go(1))
        hint = Gtk.Label(label="Press any key to begin", css_classes=["js-hint"])
        logo = self.logo(120)
        logo.add_css_class("js-float")
        return self.page_box(logo, self.hello, start, hint)

    def next_hello(self):
        if self.current != "hello":
            return True
        self.hello_i = (self.hello_i + 1) % len(HELLOS)
        self.hello.set_visible_child_name(str(self.hello_i))
        return True

    def page_language(self):
        def picked(v):
            short = v.split(".")[0]
            # Follow along with good guesses for the next pages.
            self.choice["keyboard"] = KEYBOARD_GUESS.get(short, self.choice["keyboard"])
            self.choice["timezone"] = ZONE_GUESS.get(short, self.choice["timezone"])
            self.change_language(v)
        sw, self.language_list = self.choice_list(LOCALES, "locale", picked)
        head = self.title(_("Language"), _("Choose the language for Jace OS."), "preferences-desktop-locale-symbolic")
        self.language_title, self.language_sub = head.title, head.sub
        return self.page_box(head, sw)

    def page_keyboard(self):
        sw, self.keyboard_list = self.choice_list(KEYBOARDS, "keyboard")
        return self.page_box(self.title(_("Keyboard"), _("Choose the layout that matches your keyboard."),
                                        "input-keyboard-symbolic"), sw)

    def page_wifi(self):
        self.wifi_state = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=400,
                                    vhomogeneous=False)
        done = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        done.append(Gtk.Image(icon_name="emblem-ok-symbolic", pixel_size=72, css_classes=["js-ok"]))
        self.wifi_done = Gtk.Label(label=_("You're online"), css_classes=["title-3"])
        done.append(self.wifi_done)
        self.wifi_state.add_named(done, "online")
        self.wifi_list = Gtk.ListBox(css_classes=["boxed-list", "js-list"], selection_mode=Gtk.SelectionMode.NONE)
        sw = Gtk.ScrolledWindow(child=self.wifi_list, hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=260,
                                max_content_height=300, propagate_natural_height=True)
        self.wifi_state.add_named(sw, "list")
        wait = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        wait.append(spinner(48))
        self.wifi_wait = Gtk.Label(label=_("Looking for networks…"), css_classes=["dim-label"])
        wait.append(self.wifi_wait)
        self.wifi_state.add_named(wait, "wait")
        later = Gtk.Button(label=_("Set up later"), css_classes=["flat", "pill"], halign=Gtk.Align.CENTER)
        later.connect("clicked", lambda *_a: self.go(1))
        return self.page_box(self.title(_("Connect to Wi-Fi"), _("Get the newest updates and sign in to Jace apps."),
                                        "network-wireless-symbolic"), self.wifi_state, later)

    def enter_wifi(self):
        self.wifi_state.set_visible_child_name("wait")
        self.wifi_wait.set_label(_("Looking for networks…"))
        in_thread(lambda: (online(), wifi_networks()), self.show_networks)

    def show_networks(self, result):
        is_online, nets = result
        if is_online:
            self.wifi_state.set_visible_child_name("online")
            return
        self.wifi_list.remove_all()
        for n in nets:
            row = Adw.ActionRow(title=GLib.markup_escape_text(n["ssid"]), activatable=True)
            row.add_prefix(Gtk.Image(icon_name=signal_icon(n["signal"])))
            if n["secure"]:
                row.add_suffix(Gtk.Image(icon_name="channel-secure-symbolic", css_classes=["dim-label"]))
            row.connect("activated", lambda _r, net=n: self.join(net))
            self.wifi_list.append(row)
        if not nets:
            self.wifi_list.append(Adw.ActionRow(title=_("No Wi-Fi networks found"),
                                                subtitle=_("Plug in a network cable, or set up Wi-Fi later")))
        self.wifi_state.set_visible_child_name("list")

    def join(self, net):
        if not net["secure"]:
            self.connect_wifi(net["ssid"], "")
            return
        entry = Adw.PasswordEntryRow(title=_("Password"))
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        box.append(entry)
        d = Adw.AlertDialog(heading=_("Join “{}”").format(net["ssid"]), body=_("Type the Wi-Fi password."))
        d.set_extra_child(box)
        d.add_response("cancel", _("Cancel"))
        d.add_response("join", _("Join"))
        d.set_response_appearance("join", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("join")
        entry.connect("entry-activated", lambda *_a: (d.close(), self.connect_wifi(net["ssid"], entry.get_text())))
        d.connect("response", lambda _d, r: r == "join" and self.connect_wifi(net["ssid"], entry.get_text()))
        d.present(self)

    def connect_wifi(self, ssid, password):
        self.wifi_state.set_visible_child_name("wait")
        self.wifi_wait.set_label(_("Joining {}…").format(ssid))

        def done(result):
            ok, error = result
            if ok:
                self.wifi_done.set_label(_("Connected to {}").format(ssid))
                self.wifi_state.set_visible_child_name("online")
                GLib.timeout_add(1400, lambda: (self.current == "wifi" and self.go(1), False)[1])
            else:
                self.toasts.add_toast(Adw.Toast(title=error or _("Couldn't join {}").format(ssid), timeout=4))
                self.wifi_state.set_visible_child_name("list")
        in_thread(lambda: backend("wifi", {"ssid": ssid, "password": password}), done)

    def page_jace(self):
        """Sign in with Jace: Jace's sign-in page opens right here (a private web view that keeps
        nothing). Your Jace name and picture become your account's, and the Jace apps sign in
        by themselves once you're on the desktop."""
        self.jace_state = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=400,
                                    vhomogeneous=False, interpolate_size=True)
        intro = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        sign = Gtk.Button(label=_("Sign in with Jace"), css_classes=["pill", "suggested-action", "js-big"],
                          halign=Gtk.Align.CENTER)
        sign.connect("clicked", lambda *_a: self.jace_sign_in())
        intro.append(sign)
        later = Gtk.Button(label=_("Not now"), css_classes=["flat", "pill"], halign=Gtk.Align.CENTER)
        later.connect("clicked", lambda *_a: self.go(1))
        intro.append(later)
        self.jace_state.add_named(intro, "intro")

        offline = Gtk.Label(label=_("Connect to the internet to sign in, or sign in later in Settings."),
                            css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER)
        self.jace_state.add_named(offline, "offline")

        self.jace_web_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        cancel = Gtk.Button(label=_("Cancel"), css_classes=["flat", "pill"], halign=Gtk.Align.CENTER)
        cancel.connect("clicked", lambda *_a: self.jace_cancel())
        self.jace_web_box.append(cancel)
        self.jace_state.add_named(self.jace_web_box, "web")

        wait = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        wait.append(spinner(48))
        wait.append(Gtk.Label(label=_("Signing in…"), css_classes=["dim-label"]))
        self.jace_state.add_named(wait, "wait")

        done = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.jace_avatar = Adw.Avatar(size=96, show_initials=True, halign=Gtk.Align.CENTER, css_classes=["js-ok"])
        self.jace_who = Gtk.Label(css_classes=["title-3"], wrap=True, justify=Gtk.Justification.CENTER)
        self.jace_handle = Gtk.Label(css_classes=["dim-label"])
        switch = Gtk.Button(label=_("Use a different account"), css_classes=["flat", "pill"],
                            halign=Gtk.Align.CENTER, margin_top=6)
        switch.connect("clicked", lambda *_a: (setattr(self, "jace", None), self.jace_sign_in()))
        for w in (self.jace_avatar, self.jace_who, self.jace_handle, switch):
            done.append(w)
        self.jace_state.add_named(done, "done")
        self.title_jace = self.title(_("Sign in with Jace"),
                                     _("Your name and picture come from your Jace account, and Jace Launcher, "
                                       "Jace Social and Jace Store sign in by themselves."),
                                     "avatar-default-symbolic")
        if getattr(self, "jace", None):
            self.show_jace(self.jace)
        return self.page_box(self.title_jace, self.jace_state, width=640)

    def enter_jace(self):
        if getattr(self, "jace", None):
            return
        self.jace_state.set_visible_child_name("wait")
        in_thread(lambda: DEMO or online(),
                  lambda up: self.jace_state.set_visible_child_name("intro" if up else "offline"))

    def jace_sign_in(self):
        old = getattr(self, "jace_attempt", None)
        if old and old["running"]:
            # Stop the sign-in that's still waiting (it lets go within a second), then start again.
            old["cancel"] = True
            self.jace_state.set_visible_child_name("wait")

            def retry():
                if old["running"]:
                    return True                        # still letting go: check again shortly
                self.jace_sign_in()
                return False
            GLib.timeout_add(250, retry)
            return
        attempt = self.jace_attempt = {"cancel": False, "running": True}
        self.title_jace.set_visible(False)
        web = None
        try:
            gi.require_version("WebKit", "6.0")
            from gi.repository import WebKit
            # Private: nothing (cookies, history) is kept after Setup.
            web = WebKit.WebView(network_session=WebKit.NetworkSession.new_ephemeral(), vexpand=True,
                                 width_request=620, height_request=480, css_classes=["js-web"])
            self.jace_web_box.prepend(web)
            self.jace_view = web
            self.jace_state.set_visible_child_name("web")
        except (ValueError, ImportError):
            self.jace_state.set_visible_child_name("wait")

        def open_url(url):
            if web is not None:
                GLib.idle_add(lambda: (web.load_uri(url), False)[1])
            else:
                spawn(["xdg-open", url])           # no web view: a browser window instead

        def done(result):
            attempt["running"] = False
            if attempt is not self.jace_attempt:
                return                                 # an older attempt, replaced by a new one
            if getattr(self, "jace_view", None) is not None:
                self.jace_web_box.remove(self.jace_view)
                self.jace_view = None
            self.title_jace.set_visible(True)
            if isinstance(result, Exception):
                if not attempt["cancel"]:
                    self.toasts.add_toast(Adw.Toast(title=str(result) or _("Couldn't sign in."), timeout=4))
                self.jace_state.set_visible_child_name("intro")
                return
            self.jace = result
            self.show_jace(result)

        def work():
            try:
                return account.sign_in(open_url=open_url, save=False, cancelled=lambda: attempt["cancel"])
            except Exception as e:  # noqa: BLE001 - shown to you, and you can try again
                return e
        in_thread(work, done)

    def jace_cancel(self):
        attempt = getattr(self, "jace_attempt", None)
        if attempt:
            attempt["cancel"] = True
        self.jace_state.set_visible_child_name("wait")

    def show_jace(self, acct):
        user = acct.get("user", {})
        name = account.display_name(acct)
        self.jace_avatar.set_text(name)
        texture = self.jace_texture(acct)
        self.jace_avatar.set_custom_image(texture)
        self.jace_who.set_label(_("Signed in as {}").format(name))
        self.jace_handle.set_label("@" + user["username"] if user.get("username") else "")
        self.jace_state.set_visible_child_name("done")
        # Your Jace name becomes your name here (until you type your own).
        if not self.fullname.get_text().strip():
            self.fullname.set_text(name)
        if texture:
            self.avatar.set_custom_image(texture)

    @staticmethod
    def jace_texture(acct):
        if not acct or not acct.get("avatar"):
            return None
        try:
            return Gdk.Texture.new_from_bytes(GLib.Bytes.new(acct["avatar"]))
        except GLib.Error:
            return None

    def page_sync(self):
        """Sync with Jace: what comes from (and keeps in step with) your other computers, and your
        sync passphrase. Your new account gets it, and its first sign-in brings everything in."""
        self.sync_switches = {}
        g = Adw.PreferencesGroup()
        for cid, (_col, title, about) in cloudsync.CATEGORIES.items():
            row = Adw.SwitchRow(title=_(title), subtitle=_(about), active=True)
            self.sync_switches[("cat", cid)] = row
            g.add(row)
        for gid, (title, about, _paths, _rule) in filesync.GROUPS.items():
            row = Adw.SwitchRow(title=_(title), subtitle=_(about), active=gid != "projects")
            self.sync_switches[("files", gid)] = row
            g.add(row)
        for row in self.sync_switches.values():
            row.connect("notify::active", lambda *_a: self.sync_reset())
        sw = Gtk.ScrolledWindow(child=g, hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=230,
                                max_content_height=260, propagate_natural_height=True)
        pw_group = Adw.PreferencesGroup()
        self.sync_pw = Adw.PasswordEntryRow(title=_("Sync passphrase"))
        self.sync_pw2 = Adw.PasswordEntryRow(title=_("Type it again"), visible=False)
        for r in (self.sync_pw, self.sync_pw2):
            r.connect("changed", lambda *_a: self.sync_reset())
            pw_group.add(r)
        self.sync_pw2.connect("entry-activated", lambda *_a: self.go(1))
        self.sync_pw.connect("entry-activated", lambda *_a: self.sync_pw2.grab_focus()
                             if self.sync_pw2.get_visible() else self.go(1))
        self.sync_hint = Gtk.Label(css_classes=["js-sub", "caption"], wrap=True, justify=Gtk.Justification.CENTER)
        later = Gtk.Button(label=_("Not now"), css_classes=["flat", "pill"], halign=Gtk.Align.CENTER)
        later.connect("clicked", lambda *_a: self.sync_skip())
        return self.page_box(self.title(_("Sync with Jace"), _("Bring your look, wallpaper, desktop, apps and "
                                                               "files from your other computers, and keep them "
                                                               "in step."), "emblem-synchronizing-symbolic"),
                             sw, pw_group, self.sync_hint, later, width=620)

    def enter_sync(self):
        self.sync_skipped = False
        cloudsync.TOKEN = self.jace["access_token"]
        self.sync_hint.set_label(_("Checking your Jace account…"))

        def shown(exists):
            if isinstance(exists, Exception):
                self.sync_hint.set_label(str(exists))
                return
            self.sync_exists = exists
            self.sync_pw2.set_visible(not exists)
            self.sync_pw.set_title(_("Your sync passphrase") if exists else _("Choose a sync passphrase"))
            self.sync_hint.set_label(
                _("Type the sync passphrase you chose on your other Jace OS computer.") if exists else
                _("At least 8 characters. Your other computers will need it too. Jace never sees it, so it "
                  "can't be recovered."))
            GLib.idle_add(lambda: (self.sync_pw.grab_focus(), False)[1])
        in_thread(lambda: self.sync_safe(cloudsync.passphrase_exists), shown)

    @staticmethod
    def sync_safe(work):
        try:
            return work()
        except Exception as e:  # noqa: BLE001 - shown on the page
            return e

    def sync_wanted(self) -> bool:
        return not getattr(self, "sync_skipped", False) and any(r.get_active() for r in self.sync_switches.values())

    def sync_reset(self):
        self.sync_key = None

    def sync_skip(self):
        self.sync_skipped = True
        self.sync_key = None
        self.go(1)

    def sync_check(self):
        pw = self.sync_pw.get_text()
        if self.sync_pw2.get_visible() and pw != self.sync_pw2.get_text():
            self.sync_hint.set_label(_("The passphrases don't match."))
            return
        self.sync_hint.set_label(_("Checking your passphrase…"))
        self.cont.set_sensitive(False)

        def done(result):
            self.cont.set_sensitive(True)
            if isinstance(result, Exception):
                self.sync_hint.set_label(str(result))
                return
            self.sync_key = result
            self.go(1)
        # The demo checks a passphrase you already have, but never sets up a new one.
        in_thread(lambda: self.sync_safe(lambda: cloudsync.unlock(pw, create=not DEMO)), done)

    def sync_choice(self) -> dict | None:
        """What goes to the new account: the sync key and what's turned on."""
        if not getattr(self, "jace", None) or not getattr(self, "sync_key", None) or not self.sync_wanted():
            return None
        cats = {cid: self.sync_switches[("cat", cid)].get_active() for cid in cloudsync.CATEGORIES}
        files = {gid: self.sync_switches[("files", gid)].get_active() for gid in filesync.GROUPS}
        return {"key": base64.b64encode(self.sync_key).decode(),
                "state": {"enabled": True, "categories": cats, "files": files}}

    def page_timezone(self):
        zones = run(["timedatectl", "list-timezones"]).split() or ["UTC"]
        self.zone_search = Gtk.SearchEntry(placeholder_text=_("Search for a city or region"),
                                           css_classes=["js-search"])
        sw, self.zone_list = self.choice_list([(z, z.replace("_", " ").replace("/", " · ")) for z in zones],
                                              "timezone", lambda _v: self.update_clock(), height=280)
        self.zone_rows = {z: self.zone_list.get_row_at_index(i) for i, z in enumerate(zones)}
        self.zone_list.set_filter_func(
            lambda row: (q := self.zone_search.get_text().lower().replace(" ", "_")) == "" or q in row.value.lower())
        self.zone_search.connect("search-changed", lambda *_a: self.zone_list.invalidate_filter())
        self.clock = Gtk.Label(css_classes=["js-clock"])
        if not getattr(self, "_clock_timer", None):
            self._clock_timer = GLib.timeout_add_seconds(1, lambda: (self.update_clock(), True)[1])
        return self.page_box(self.title(_("Time zone"), None, "preferences-system-time-symbolic"), self.clock,
                             self.zone_search, sw)

    def enter_timezone(self):
        self.zone_list.pick(self.choice["timezone"])
        row = self.zone_rows.get(self.choice["timezone"])
        if row:
            GLib.idle_add(lambda: (row.grab_focus(), False)[1])
        self.update_clock()

    def update_clock(self):
        try:
            tz = GLib.TimeZone.new_identifier(self.choice["timezone"]) or GLib.TimeZone.new_utc()
        except (TypeError, GLib.Error):
            tz = GLib.TimeZone.new_utc()
        self.clock.set_label(GLib.DateTime.new_now(tz).format("%H:%M"))

    def page_account(self):
        # Rebuilt when you change the language: keep what you've typed.
        typed = {r: getattr(self, r).get_text() for r in ("fullname", "username", "hostname")
                 if getattr(self, r, None) is not None}
        self.avatar = Adw.Avatar(size=88, show_initials=True, text="", halign=Gtk.Align.CENTER,
                                 css_classes=["js-avatar"])
        g = Adw.PreferencesGroup()
        self.fullname = Adw.EntryRow(title=_("Your name"))
        self.username = Adw.EntryRow(title=_("Username"))
        self.pw = Adw.PasswordEntryRow(title=_("Password"))
        self.pw2 = Adw.PasswordEntryRow(title=_("Type it again"))
        self.hostname = Adw.EntryRow(title=_("Computer name"))
        self._auto = {"user": "", "host": ""}
        rows = [self.fullname, self.username, self.pw, self.pw2, self.hostname]
        for i, r in enumerate(rows):
            g.add(r)
            r.connect("changed", lambda *_a: self.validate())
            nxt = rows[i + 1] if i + 1 < len(rows) else None
            r.connect("entry-activated", lambda _r, n=nxt: n.grab_focus() if n else self.go(1))
        self.fullname.connect("changed", self.name_changed)
        self.account_hint = Gtk.Label(css_classes=["js-sub", "caption"], wrap=True)
        for r, text in typed.items():
            getattr(self, r).set_text(text)
        texture = self.jace_texture(getattr(self, "jace", None))
        if texture:
            self.avatar.set_custom_image(texture)
        return self.page_box(self.title(_("Make your account"), _("You'll use it to sign in to this computer.")),
                             self.avatar, g, self.account_hint)

    def enter_account(self):
        GLib.idle_add(lambda: (self.fullname.grab_focus(), False)[1])

    def name_changed(self, *_a):
        name = self.fullname.get_text().strip()
        self.avatar.set_text(name)
        user = re.sub(r"[^a-z0-9_-]", "", (name.lower().split() or [""])[0])[:32]
        if user and not re.match(r"[a-z_]", user):
            user = "u" + user
        if self.username.get_text() in ("", self._auto["user"]):
            self.username.set_text(user)
            self._auto["user"] = user
        host = f"{user or 'jace'}-pc"
        if self.hostname.get_text() in ("", self._auto["host"]):
            self.hostname.set_text(host)
            self._auto["host"] = host

    def account_problem(self) -> str:
        problem = ""
        if not self.fullname.get_text().strip():
            problem = " "                                  # nothing typed yet: no message
        elif not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", self.username.get_text()):
            problem = _("Usernames are lowercase letters, numbers, - and _.")
        elif self.username.get_text() in ("root", "jace-setup", "jace-live", "greeter", "clamav", "nobody"):
            problem = _("Choose a different username.")
        elif not self.pw.get_text():
            problem = " "
        elif self.pw2.get_text() and self.pw.get_text() != self.pw2.get_text():
            problem = _("The passwords don't match.")
        elif self.pw.get_text() != self.pw2.get_text():
            problem = " "
        elif not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,62}", self.hostname.get_text()):
            problem = _("Computer names are letters, numbers and -.")
        self.account_hint.set_label(problem.strip())
        return problem

    def page_look(self):
        tiles = Gtk.Box(spacing=22, halign=Gtk.Align.CENTER, homogeneous=True)
        self.tiles = {}
        for dark, label in ((False, _("Light")), (True, _("Dark"))):
            preview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                              css_classes=["js-preview", "js-preview-dark" if dark else "js-preview-light"])
            preview.append(Gtk.Box(css_classes=["js-pv-bar"]))
            win = Gtk.Box(css_classes=["js-pv-window"], vexpand=True)
            win.append(Gtk.Box(css_classes=["js-pv-side"]))
            preview.append(win)
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            content.append(preview)
            content.append(Gtk.Label(label=label, css_classes=["heading"]))
            b = Gtk.Button(child=content, css_classes=["flat", "js-tile"])
            b.connect("clicked", lambda _b, d=dark: self.set_look(d))
            self.tiles[dark] = b
            tiles.append(b)
        self.set_look(self.choice["dark"])
        return self.page_box(self.title(_("Choose your look"), _("Change it any time in Settings.")), tiles,
                             width=640)

    def set_look(self, dark):
        self.choice["dark"] = dark
        for d, b in self.tiles.items():
            (b.add_css_class if d == dark else b.remove_css_class)("js-tile-on")
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK if dark
                                                       else Adw.ColorScheme.FORCE_LIGHT)
        (self.remove_css_class if dark else self.add_css_class)("js-light")

    def page_working(self):
        self.work_text = Gtk.Label(label=_("Setting things up…"), css_classes=["js-sub"])
        self.work_bar = Gtk.ProgressBar(css_classes=["js-progress"], halign=Gtk.Align.CENTER, width_request=320)
        self.work_retry = Gtk.Button(label=_("Go back"), css_classes=["pill"], halign=Gtk.Align.CENTER,
                                     visible=False)
        self.work_retry.connect("clicked", lambda *_a: self.show("account", backwards=True))
        return self.page_box(spinner(72), self.title(_("Setting up your computer")), self.work_text, self.work_bar,
                             self.work_retry)

    def finish(self):
        data = dict(self.choice, fullname=self.fullname.get_text().strip(), username=self.username.get_text(),
                    password=self.pw.get_text(), hostname=self.hostname.get_text())
        sync = self.sync_choice()
        if sync:
            data["sync"] = sync
        if getattr(self, "jace", None):
            acct = {k: v for k, v in self.jace.items() if k != "avatar"}
            data["jace"] = {"account": acct,
                            "avatar": base64.b64encode(self.jace.get("avatar") or b"").decode()}
        self.work_retry.set_visible(False)
        self.work_bar.set_fraction(0.02)

        def line(text):
            if text.startswith("@progress "):
                _p, pct, what = (text.split(" ", 2) + [""])[:3]
                GLib.idle_add(lambda: (self.work_bar.set_fraction(int(pct) / 100), self.work_text.set_label(_(what)),
                                       False)[2])

        def done(result):
            ok, error = result
            if ok:
                self.pw.set_text("")
                self.pw2.set_text("")
                first = (self.fullname.get_text().strip().split() or [""])[0]
                self.welcome_title.set_label(_("Welcome, {}").format(first) if first else _("Welcome"))
                GLib.timeout_add(600, lambda: (self.show("welcome"), False)[1])
            else:
                self.work_text.set_label(error or _("Something went wrong."))
                self.work_retry.set_visible(True)
        in_thread(lambda: backend("finish", data, line), done)

    def page_welcome(self):
        logo = self.logo(150)
        logo.add_css_class("js-popin")
        self.welcome_title = Gtk.Label(label=_("Welcome"), css_classes=["js-title", "js-big-title"])
        sub = Gtk.Label(label=_("Jace OS is ready. Jace Launcher, Jace Social, Jace Store and Jace Browser are "
                                "already installed."), css_classes=["js-sub"], wrap=True,
                        justify=Gtk.Justification.CENTER)
        self.start_button = Gtk.Button(label=_("Get started"), css_classes=["pill", "suggested-action", "js-big"],
                                       halign=Gtk.Align.CENTER, margin_top=24)
        self.start_button.connect("clicked", self.sign_in)
        return self.page_box(logo, self.welcome_title, sub, self.start_button)

    def sign_in(self, button):
        button.set_sensitive(False)
        button.set_label(_("Signing you in…"))
        if DEMO:
            GLib.timeout_add(1500, lambda: (self.get_application().quit(), False)[1])
            return
        in_thread(lambda: subprocess.run(["pkexec", BACKEND, "signin"], input="{}", text=True,
                                         capture_output=True).returncode, lambda _r: None)


class SetupApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.jaceos.Setup", flags=Gio.ApplicationFlags.NON_UNIQUE)

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        provider = Gtk.CssProvider()
        provider.load_from_path(str(SHARE / "setup.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        win = self.get_active_window() or SetupWindow(self)
        if not DEMO or os.environ.get("JACE_SETUP_FULLSCREEN"):
            win.fullscreen()
        win.present()


def main():
    sys.exit(SetupApp().run([sys.argv[0]]))
