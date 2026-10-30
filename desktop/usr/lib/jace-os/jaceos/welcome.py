"""Welcome to Jace OS: the first thing you see after signing in for the first time
(and every time on the live USB, where it offers to install Jace OS).

  jace-welcome              show it
  jace-welcome --autostart  show it only if it hasn't been finished yet (or on the live USB)
"""
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from jaceos import JACE_APPS, LOGO, account, background, config_dir, is_live, load_css, spawn, updates  # noqa: E402

DONE = "welcome-done"


def install_mode() -> bool:
    try:
        return "jace.install=1" in open("/proc/cmdline").read().split()
    except OSError:
        return False


class WelcomeWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Welcome to Jace OS", default_width=760, default_height=560)
        self.set_icon_name("jace-os")
        self.live = is_live()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar(show_title=False)
        view.add_top_bar(header)

        self.carousel = Adw.Carousel(vexpand=True, allow_scroll_wheel=False)
        for page in (self.page_hello(), self.page_account(), self.page_apps(), self.page_tips(),
                     self.page_updates(), self.page_finish()):
            self.carousel.append(page)

        dots = Adw.CarouselIndicatorDots(carousel=self.carousel)
        self.back = Gtk.Button(label="Back", css_classes=["pill"])
        self.back.connect("clicked", lambda *_: self.go(-1))
        self.next = Gtk.Button(label="Next", css_classes=["pill", "suggested-action"])
        self.next.connect("clicked", lambda *_: self.go(1))
        bar = Gtk.CenterBox(margin_start=24, margin_end=24, margin_bottom=20, margin_top=8)
        bar.set_start_widget(self.back)
        bar.set_center_widget(dots)
        bar.set_end_widget(self.next)
        self.carousel.connect("page-changed", lambda *_: self.update_buttons())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.carousel)
        box.append(bar)
        view.set_content(box)
        self.set_content(view)
        self.update_buttons()

    # --- pages -------------------------------------------------------------------------------------

    @staticmethod
    def status(title, description, child=None, icon=None, paintable_logo=False):
        page = Adw.StatusPage(title=title, description=description, hexpand=True, vexpand=True)
        if paintable_logo:
            logo = Gtk.Picture.new_for_filename(str(LOGO))
            logo.set_size_request(128, 128)
            logo.set_can_shrink(True)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
            box.append(logo)
            if child:
                box.append(child)
            page.set_child(box)
        else:
            if icon:
                page.set_icon_name(icon)
            if child:
                page.set_child(child)
        return page

    def page_hello(self):
        return self.status("Welcome to Jace OS",
                           "Your apps, your Jace account and a desktop that works the way you expect. "
                           "Let's get you set up: it takes a minute.", paintable_logo=True)

    def page_account(self):
        self.acct_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)
        self.fill_account()
        return self.status("Sign in with Jace",
                           "One account for Jace OS, Jace Social, Jace Store and Jace Launcher. "
                           "You can skip this and sign in later in Settings.",
                           self.acct_box, icon="avatar-default-symbolic")

    def fill_account(self):
        while (c := self.acct_box.get_first_child()) is not None:
            self.acct_box.remove(c)
        acct = account.load()
        if acct:
            self.acct_box.append(Gtk.Label(label=f"Signed in as {account.display_name(acct)}",
                                           css_classes=["title-3", "success"]))
            return
        b = Gtk.Button(label="Sign in with Jace", css_classes=["pill", "suggested-action"])
        self.acct_msg = Gtk.Label(css_classes=["dim-label"], wrap=True)
        b.connect("clicked", self.sign_in)
        self.acct_box.append(b)
        self.acct_box.append(self.acct_msg)

    def sign_in(self, button):
        button.set_sensitive(False)
        self.acct_msg.set_label("Finish signing in in your browser…")

        def done(result):
            if isinstance(result, Exception):
                button.set_sensitive(True)
                self.acct_msg.set_label(f"Couldn't sign in: {result}")
            else:
                self.present()
                self.fill_account()

        background(account.sign_in, done)

    def page_apps(self):
        grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4, min_children_per_line=2,
                           column_spacing=12, row_spacing=12, halign=Gtk.Align.CENTER, homogeneous=True)
        for _pkg, cmd, name, _about in JACE_APPS + [("zen-browser-bin", "zen-browser", "Zen Browser", "")]:
            icon = "zen" if cmd == "zen-browser" else cmd
            b = Gtk.Button(css_classes=["flat", "jw-app"])
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            img = Gtk.Image(icon_name=icon, pixel_size=64)
            box.append(img)
            box.append(Gtk.Label(label=name))
            b.set_child(box)
            b.connect("clicked", lambda _b, c=cmd: spawn([c]))
            grid.append(b)
        return self.status("Your apps are ready",
                           "Jace Launcher, Jace Social, Jace Store and Zen Browser come with Jace OS, "
                           "and stay up to date on their own.", grid, icon="view-app-grid-symbolic")

    def page_tips(self):
        lb = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE, width_request=460)
        for keys, what in (
            ("Windows key", "Opens the start menu. Just start typing to search."),
            ("Drag to the top", "Maximizes a window. Drag it to a side to snap it to half the screen."),
            ("Alt+Tab", "Switches between windows."),
            ("Win+← / Win+→", "Snaps the window you're in."),
            ("Win+Shift+S", "Takes a screenshot of part of the screen."),
            ("Win+I", "Opens Settings."),
        ):
            row = Adw.ActionRow(title=what)
            row.add_prefix(Gtk.Label(label=keys, css_classes=["caption-heading", "accent"], width_chars=14, xalign=0))
            lb.append(row)
        return self.status("Getting around", "If you've used Windows, you already know how this works.", lb,
                           icon="input-keyboard-symbolic")

    def page_updates(self):
        row = Adw.SwitchRow(title="Install updates automatically",
                            subtitle="Linux, security fixes and your Jace apps, in the background",
                            active=updates.auto_enabled() or self.live)
        row.connect("notify::active", lambda r, _p: not self.live and background(lambda: updates.set_auto(r.get_active())))
        lb = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE, width_request=460)
        lb.append(row)
        return self.status("Always up to date",
                           "Jace OS keeps itself secure without you doing anything. You can see every update in "
                           "Settings → Updates.", lb, icon="software-update-available-symbolic")

    def page_finish(self):
        if self.live:
            b = Gtk.Button(label="Install Jace OS", css_classes=["pill", "suggested-action"], halign=Gtk.Align.CENTER)
            b.connect("clicked", lambda *_: spawn(["jace-installer"]))
            return self.status("Try it, then install it",
                               "You're running Jace OS from a USB stick. Nothing is saved until you install it.",
                               b, paintable_logo=True)
        b = Gtk.Button(label="Open Settings", css_classes=["pill"], halign=Gtk.Align.CENTER)
        b.connect("clicked", lambda *_: spawn(["jace-settings"]))
        return self.status("You're all set", "Enjoy Jace OS.", b, paintable_logo=True)

    # --- navigation --------------------------------------------------------------------------------

    def go(self, delta):
        i = round(self.carousel.get_position()) + delta
        if i >= self.carousel.get_n_pages():
            self.finish()
            return
        if 0 <= i:
            self.carousel.scroll_to(self.carousel.get_nth_page(i), True)

    def update_buttons(self):
        i = round(self.carousel.get_position())
        last = i == self.carousel.get_n_pages() - 1
        self.back.set_visible(i > 0)
        self.next.set_label("Start using Jace OS" if last else "Next")

    def finish(self):
        (config_dir() / DONE).touch()
        self.close()


class WelcomeApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.jaceos.Welcome", flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)

    def do_startup(self):
        Adw.Application.do_startup(self)
        load_css()

    def do_command_line(self, cmdline):
        args = cmdline.get_arguments()[1:]
        if "--autostart" in args and is_live() and install_mode():
            # Booted with "Install Jace OS" from the USB's boot menu: go straight to the installer.
            spawn(["jace-installer"])
            return 0
        if "--autostart" in args and (config_dir() / DONE).exists() and not is_live():
            return 0
        win = self.get_active_window() or WelcomeWindow(self)
        win.present()
        return 0


def main():
    sys.exit(WelcomeApp().run(sys.argv))
