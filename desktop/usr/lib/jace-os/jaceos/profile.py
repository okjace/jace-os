"""Your account on this computer: name, picture, and syncing them with Jace.

The name and picture live in AccountsService (what the login screen and other apps read),
plus ~/.face. You can change your own without a password. When you're signed in to Jace
(and "Use my Jace name and picture" is on), they follow your Jace display name and photo.

Also: the round picture the top bar shows (jace-account --avatar), and the other accounts
on this computer for Settings → Users (deleting one asks for an administrator password).
"""
import os
import pwd
import shutil
from pathlib import Path

from gi.repository import Gio, GLib

from jaceos import config_dir, data_dir

BUS = "org.freedesktop.Accounts"


def _accounts():
    return Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None, BUS,
                                          "/org/freedesktop/Accounts", "org.freedesktop.Accounts", None)


def _user_proxy(path: str):
    return Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None, BUS, path,
                                          "org.freedesktop.Accounts.User", None)


def _me():
    try:
        path = _accounts().call_sync("FindUserByName", GLib.Variant("(s)", (pwd.getpwuid(os.getuid()).pw_name,)),
                                     Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
        return _user_proxy(path)
    except GLib.Error:
        return None


def _prop(proxy, name, default=""):
    v = proxy.get_cached_property(name) if proxy else None
    return v.unpack() if v is not None else default


def local_user() -> dict:
    """{"username", "name", "picture" (a file or None), "admin"}"""
    pw = pwd.getpwuid(os.getuid())
    me = _me()
    name = _prop(me, "RealName") or pw.pw_gecos.split(",")[0] or pw.pw_name
    pic = _prop(me, "IconFile")
    if not pic or not Path(pic).is_file():
        face = Path.home() / ".face"
        pic = str(face) if face.is_file() else None
    return {"username": pw.pw_name, "name": name, "picture": pic, "admin": _prop(me, "AccountType", 0) == 1}


def set_name(name: str) -> bool:
    me = _me()
    if not me or not name.strip():
        return False
    try:
        me.call_sync("SetRealName", GLib.Variant("(s)", (name.strip(),)), Gio.DBusCallFlags.NONE, 5000, None)
        return True
    except GLib.Error:
        return False


def set_picture(image: str) -> bool:
    """Uses a picture (any size or shape) as your account picture: cropped square, 512 px."""
    out = data_dir() / "account-picture.png"
    if not _square(image, out, 512):
        return False
    shutil.copyfile(out, Path.home() / ".face")
    me = _me()
    if me:
        try:
            me.call_sync("SetIconFile", GLib.Variant("(s)", (str(out),)), Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error:
            pass
    _forget_round()
    return True


# --- Jace sync ------------------------------------------------------------------------------------

def sync_on() -> bool:
    return not (config_dir() / "no-jace-profile-sync").exists()


def set_sync(on: bool):
    flag = config_dir() / "no-jace-profile-sync"
    flag.unlink(missing_ok=True) if on else flag.touch()


def sync_from_jace(user: dict, avatar: Path | None):
    """Copies your Jace display name and photo onto this computer's account."""
    if not sync_on():
        return
    name = user.get("display_name") or user.get("name")
    if name and name != local_user()["name"]:
        set_name(name)
    if avatar and avatar.is_file():
        set_picture(str(avatar))


# --- pictures -------------------------------------------------------------------------------------

def _pixbuf(path: str):
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    try:
        return GdkPixbuf.Pixbuf.new_from_file(path)
    except GLib.Error:
        return None


def _square(src: str, out: Path, size: int) -> bool:
    pb = _pixbuf(src)
    if pb is None:
        return False
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    side = min(pb.get_width(), pb.get_height())
    pb = pb.new_subpixbuf((pb.get_width() - side) // 2, (pb.get_height() - side) // 2, side, side)
    pb = pb.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
    out.parent.mkdir(parents=True, exist_ok=True)
    pb.savev(str(out), "png", [], [])
    return True


def _round_dir() -> Path:
    d = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "jace-os"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _forget_round():
    for f in _round_dir().glob("corner-*.png"):
        f.unlink(missing_ok=True)


def round_picture(src: str | None, name: str, size: int = 64) -> str:
    """A round PNG of src (or a green circle with your initials) for the top bar."""
    import cairo
    import hashlib
    stamp = Path(src).stat().st_mtime_ns if src and Path(src).exists() else 0
    key = hashlib.sha1(f"{src}|{name}|{stamp}|{size}".encode()).hexdigest()[:12]
    out = _round_dir() / f"corner-{key}.png"
    if out.exists():
        return str(out)
    _forget_round()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.arc(size / 2, size / 2, size / 2, 0, 6.2832)
    cr.clip()
    square = _round_dir() / ".corner-square.png"
    if src and _square(src, square, size):
        cr.set_source_surface(cairo.ImageSurface.create_from_png(str(square)), 0, 0)
        cr.paint()
        square.unlink(missing_ok=True)
    else:
        cr.set_source_rgb(0x3d / 255, 0xdc / 255, 0x84 / 255)
        cr.paint()
        letters = "".join(w[0] for w in name.split()[:2]).upper() or "?"
        cr.set_source_rgb(0x0c / 255, 0x14 / 255, 0x10 / 255)
        cr.select_font_face("Inter", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(size * (0.42 if len(letters) > 1 else 0.5))
        ext = cr.text_extents(letters)
        cr.move_to(size / 2 - ext.width / 2 - ext.x_bearing, size / 2 - ext.height / 2 - ext.y_bearing)
        cr.show_text(letters)
    surface.write_to_png(str(out))
    return str(out)


# --- other accounts -------------------------------------------------------------------------------

def other_users() -> list[dict]:
    """[{"uid", "username", "name", "picture", "admin"}] for the other people on this computer."""
    out = []
    try:
        paths = _accounts().call_sync("ListCachedUsers", None, Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    except GLib.Error:
        paths = []
    for path in paths:
        p = _user_proxy(path)
        uid = _prop(p, "Uid", 0)
        if uid == os.getuid() or uid < 1000:
            continue
        pic = _prop(p, "IconFile")
        out.append({"uid": uid, "username": _prop(p, "UserName"), "name": _prop(p, "RealName") or _prop(p, "UserName"),
                    "picture": pic if pic and os.access(pic, os.R_OK) else None,
                    "admin": _prop(p, "AccountType", 0) == 1})
    return sorted(out, key=lambda u: u["name"].lower())


def delete_user(uid: int, remove_files: bool) -> tuple[bool, str]:
    """Deletes another account (asks for an administrator password)."""
    try:
        _accounts().call_sync("DeleteUser", GLib.Variant("(xb)", (uid, remove_files)),
                              Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, 120000, None)
        return True, ""
    except GLib.Error as e:
        return False, e.message.split(":")[-1].strip() if e.message else "It didn't work"
