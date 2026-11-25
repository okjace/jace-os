"""Jace OS's own "Sign in with Jace" window, and the welcome animation after it.

    jace-signin                 sign in (the window shows Jace's sign-in page)
    jace-signin --add           add another Jace account (Jace's account picker)
    jace-signin --reauth SUB    sign that account in again (its sign-in ran out)
    jace-signin --switch SUB    switch to another account on this computer (then the animation)
    jace-signin --silent        at login: sign in by itself if this window is signed in to Jace
                                already (prompt=none). Shows nothing unless it worked.
    jace-signin --welcome-demo  just the animation

The window keeps its own Jace sign-in (cookies in ~/.local/share/jace-os/web), so next time,
or on a new login, Jace OS signs in without a click. It's started through run-popup, so the
animation can cover the screen.
"""
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from jaceos import account, data_dir  # noqa: E402
from jaceos.popup import strip_preload  # noqa: E402

strip_preload()


def _web_session():
    gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit
    base = data_dir() / "web"
    cache = GLib.get_user_cache_dir() + "/jace-os/web"
    (base / "data").mkdir(parents=True, exist_ok=True)
    session = WebKit.NetworkSession.new(str(base / "data"), cache)
    session.get_cookie_manager().set_persistent_storage(str(base / "cookies.sqlite"),
                                                        WebKit.CookiePersistentStorage.SQLITE)
    return WebKit, session


def _in_thread(work, done):
    def run():
        try:
            result = work()
        except Exception as e:  # noqa: BLE001 - shown in the window
            result = e
        GLib.idle_add(lambda: (done(result), False)[1])
    threading.Thread(target=run, daemon=True).start()


def _sign_in_retrying(**kw):
    """The silent sign-in at login may still be holding the sign-in port for a few seconds."""
    import time
    for _ in range(20):
        try:
            return account.sign_in(**kw)
        except account.AccountError as e:
            if "already open" not in str(e) or (kw.get("cancelled") and kw["cancelled"]()):
                raise
            time.sleep(1.5)
    return account.sign_in(**kw)


class SignInWindow(Adw.ApplicationWindow):
    def __init__(self, app, prompt=None, title="Sign in with Jace", expect=None):
        super().__init__(application=app, title=title, default_width=480, default_height=700)
        self.app, self.prompt, self.expect = app, prompt, expect
        self.cancel = False
        self.finished = False
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        view.add_top_bar(header)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        view.set_content(self.stack)
        self.set_content(view)

        spinner = Adw.Spinner(width_request=36, height_request=36) if hasattr(Adw, "Spinner") \
            else Gtk.Spinner(spinning=True, width_request=36, height_request=36)
        busy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, valign=Gtk.Align.CENTER)
        busy.append(spinner)
        self.busy_label = Gtk.Label(label="Opening Jace…", css_classes=["dim-label"])
        busy.append(self.busy_label)
        self.stack.add_named(busy, "busy")

        self.error = Adw.StatusPage(icon_name="dialog-warning-symbolic", title="Couldn't sign in")
        again = Gtk.Button(label="Try again", css_classes=["suggested-action", "pill"], halign=Gtk.Align.CENTER)
        again.connect("clicked", lambda _b: self.start())
        self.error.set_child(again)
        self.stack.add_named(self.error, "error")

        try:
            WebKit, session = _web_session()
            self.web = WebKit.WebView(network_session=session, vexpand=True)
            self.web.connect("load-changed", self.loaded)
            self.stack.add_named(self.web, "web")
        except (ValueError, ImportError):
            self.web = None
        self.connect("close-request", self.closing)
        self.start()

    def loaded(self, _web, event):
        gi.require_version("WebKit", "6.0")
        from gi.repository import WebKit
        if event == WebKit.LoadEvent.FINISHED and not self.finished:
            self.stack.set_visible_child_name("web")

    def open_url(self, url):
        if self.web is not None:
            GLib.idle_add(lambda: (self.web.load_uri(url), False)[1])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", url])
            GLib.idle_add(lambda: (self.busy_label.set_label("Finish signing in in your browser…"), False)[1])

    def start(self):
        self.cancel = False
        self.stack.set_visible_child_name("busy")
        self.busy_label.set_label("Opening Jace…")
        _in_thread(lambda: _sign_in_retrying(open_url=self.open_url, prompt=self.prompt,
                                             cancelled=lambda: self.cancel), self.done)

    def done(self, result):
        if self.cancel:
            return
        if isinstance(result, Exception):
            self.error.set_description(str(result) or "Something went wrong.")
            self.stack.set_visible_child_name("error")
            return
        self.finished = True
        self.set_visible(False)
        welcome(self.app, result, first_time=False)
        self.close()

    def closing(self, _w):
        self.cancel = True
        return False


def welcome(app, acct, first_time=False):
    from jaceos import welcome as w
    app.hold()
    pic = account.avatar_path()
    w.show(app, account.display_name(acct), str(pic) if pic else None, first_time, then=app.release)


def silent(app):
    """Sign in by itself, in a web view nobody sees. Only the animation shows, and only if it worked."""
    if not account.should_sign_in_silently() or not account.settings()["client_id"]:
        return
    try:
        WebKit, session = _web_session()
    except (ValueError, ImportError):
        return
    holder = Gtk.Window()                  # never shown: just somewhere for the web view to live
    web = WebKit.WebView(network_session=session)
    holder.set_child(web)
    app.hold()

    def done(result):
        web.stop_loading()                 # (destroying a web view that never showed crashes WebKit)
        if isinstance(result, dict) and result:
            welcome(app, result)
        app.release()
    _in_thread(lambda: account.sign_in_silently(lambda url: GLib.idle_add(
        lambda: (web.load_uri(url), False)[1])), done)


def switch(app, sub):
    app.hold()

    def done(result):
        if isinstance(result, Exception):
            if getattr(result, "code", "") == "reauth":
                SignInWindow(app, prompt="select_account", title="Sign in again", expect=sub).present()
            else:
                _message(app, "Couldn't switch accounts", str(result))
        else:
            welcome(app, result)
        app.release()
    _in_thread(lambda: account.switch(sub), done)


def _message(app, heading, body):
    win = Adw.ApplicationWindow(application=app, default_width=1, default_height=1)
    d = Adw.AlertDialog(heading=heading, body=body)
    d.add_response("ok", "OK")
    d.connect("response", lambda *_: win.close())
    win.present()
    d.present(win)


def main():
    args = sys.argv[1:]
    app = Adw.Application(application_id=None)

    def activate(a):
        if "--welcome-demo" in args:
            acct = account.load() or {"user": {"name": "Jace"}}
            welcome(a, acct)
        elif "--silent" in args:
            silent(a)
        elif "--switch" in args and len(args) > args.index("--switch") + 1:
            switch(a, args[args.index("--switch") + 1])
        elif "--add" in args:
            SignInWindow(a, prompt="select_account", title="Add a Jace account").present()
        elif "--reauth" in args:
            SignInWindow(a, prompt="select_account", title="Sign in again").present()
        else:
            SignInWindow(a).present()
    app.connect("activate", activate)
    app.run([])
