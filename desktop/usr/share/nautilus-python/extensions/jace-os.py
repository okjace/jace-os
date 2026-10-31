"""Jace OS right-click actions in Files (Nautilus):
  - Install, on an AppImage: puts it in ~/Applications and the start menu (jace-appimage)
  - Open in Terminal, on a folder or the folder you're in (jace-terminal)
"""
import subprocess

import gi

try:
    gi.require_version("Nautilus", "4.1")
except ValueError:
    gi.require_version("Nautilus", "4.0")
from gi.repository import GObject, Nautilus  # noqa: E402


def _run(cmd):
    subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _is_appimage(f) -> bool:
    return f.get_uri_scheme() == "file" and (
        f.get_name().lower().endswith(".appimage")
        or f.get_mime_type() in ("application/vnd.appimage", "application/x-iso9660-appimage"))


class JaceOSMenu(GObject.GObject, Nautilus.MenuProvider):
    def get_file_items(self, files):
        items = []
        if len(files) == 1 and _is_appimage(files[0]):
            path = files[0].get_location().get_path()
            install = Nautilus.MenuItem(name="JaceOS::InstallAppImage", label="Install",
                                        tip="Add this app to the start menu and Applications")
            install.connect("activate", lambda *_: _run(["jace-appimage", "install", path]))
            items.append(install)
        if len(files) == 1 and files[0].is_directory() and files[0].get_uri_scheme() == "file":
            folder = files[0].get_location().get_path()
            term = Nautilus.MenuItem(name="JaceOS::TerminalHere", label="Open in Terminal")
            term.connect("activate", lambda *_: _run(["jace-terminal", "--dir", folder]))
            items.append(term)
        return items

    def get_background_items(self, folder):
        if folder.get_uri_scheme() != "file":
            return []
        path = folder.get_location().get_path()
        term = Nautilus.MenuItem(name="JaceOS::TerminalHereBackground", label="Open in Terminal")
        term.connect("activate", lambda *_: _run(["jace-terminal", "--dir", path]))
        return [term]
