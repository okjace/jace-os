"""Keyboard shortcuts you can change (Settings → Keyboard shortcuts).

The Jace OS defaults live in /usr/share/jace-os/xdg/labwc/rc.xml and are listed in
SHORTCUTS below. What you change is kept in ~/.config/jace-os/shortcuts.json and written
into your labwc config (~/.config/labwc/rc.xml, merged over the defaults): a key you
moved away from is cleared with <action name="None"/>, and the new key gets the action.
You can also add your own shortcuts that run any command.

Keys use labwc's names: W- (Windows/Super), C- (Ctrl), A- (Alt), S- (Shift), then an
XKB key name like "e", "Return", "Print" or "F4".
"""
import json
import uuid
import xml.etree.ElementTree as ET

from jaceos import config_dir

GROUPS = ["General", "Windows", "Desktops", "Apps", "Screenshots", "Sound, brightness and media"]


def _exec(cmd: str) -> str:
    return f'<action name="Execute" command="{cmd}" />'


# id, group, title, default keys, labwc actions (the inside of <keybind>)
SHORTCUTS = [
    ("start-menu", "General", "Open the start menu", ["Super_L", "W-s"], _exec("jace-menu")),
    ("settings", "General", "Settings", ["W-i"], _exec("jace-settings")),
    ("account", "General", "Your Jace account", ["W-a"], _exec("jace-account")),
    ("lock", "General", "Lock the screen", ["W-l"], _exec("jace-lock")),
    ("power", "General", "Power menu (sign out, restart, shut down)", ["W-x", "C-A-Delete"], _exec("jace-power")),
    ("emoji", "General", "Emoji and symbols", ["W-period"], _exec("gnome-characters")),

    ("next-window", "Windows", "Switch windows", ["A-Tab", "W-Tab"], '<action name="NextWindow" />'),
    ("previous-window", "Windows", "Switch windows backwards", ["A-S-Tab"], '<action name="PreviousWindow" />'),
    ("close", "Windows", "Close the window", ["A-F4"], '<action name="Close" />'),
    ("snap-left", "Windows", "Snap to the left half", ["W-Left"], '<action name="SnapToEdge" direction="left" />'),
    ("snap-right", "Windows", "Snap to the right half", ["W-Right"], '<action name="SnapToEdge" direction="right" />'),
    ("maximize", "Windows", "Maximize", ["W-Up"], '<action name="Maximize" />'),
    ("restore", "Windows", "Restore, or minimize if not maximized", ["W-Down"],
     '<action name="If"><query maximized="yes" /><then><action name="UnMaximize" /></then>'
     '<else><action name="Iconify" /></else></action>'),
    ("minimize", "Windows", "Minimize", ["W-m"], '<action name="Iconify" />'),
    ("show-desktop", "Windows", "Show the desktop", ["W-d"], '<action name="ToggleShowDesktop" />'),

    ("desktop-left", "Desktops", "Previous desktop", ["W-C-Left"], '<action name="GoToDesktop" to="left" wrap="no" />'),
    ("desktop-right", "Desktops", "Next desktop", ["W-C-Right"], '<action name="GoToDesktop" to="right" wrap="no" />'),
    ("send-left", "Desktops", "Move the window to the previous desktop", ["W-S-Left"],
     '<action name="SendToDesktop" to="left" wrap="no" />'),
    ("send-right", "Desktops", "Move the window to the next desktop", ["W-S-Right"],
     '<action name="SendToDesktop" to="right" wrap="no" />'),
    ("desktop-1", "Desktops", "Desktop 1", ["W-1"], '<action name="GoToDesktop" to="1" />'),
    ("desktop-2", "Desktops", "Desktop 2", ["W-2"], '<action name="GoToDesktop" to="2" />'),
    ("desktop-3", "Desktops", "Desktop 3", ["W-3"], '<action name="GoToDesktop" to="3" />'),
    ("desktop-4", "Desktops", "Desktop 4", ["W-4"], '<action name="GoToDesktop" to="4" />'),

    ("terminal", "Apps", "Terminal", ["W-Return", "C-A-t"], _exec("jace-terminal")),
    ("files", "Apps", "Files", ["W-e"], _exec("nautilus --new-window")),
    ("task-manager", "Apps", "System Monitor (Task Manager)", ["C-S-Escape"], _exec("gnome-system-monitor")),

    ("screenshot", "Screenshots", "Screenshot of the whole screen", ["Print", "W-Print"], _exec("jace-screenshot screen")),
    ("screenshot-area", "Screenshots", "Screenshot of an area", ["W-S-s"], _exec("jace-screenshot area")),

    ("volume-up", "Sound, brightness and media", "Volume up", ["XF86AudioRaiseVolume"],
     _exec("wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ 5%+")),
    ("volume-down", "Sound, brightness and media", "Volume down", ["XF86AudioLowerVolume"],
     _exec("wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-")),
    ("mute", "Sound, brightness and media", "Mute", ["XF86AudioMute"], _exec("wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle")),
    ("mic-mute", "Sound, brightness and media", "Mute the microphone", ["XF86AudioMicMute"],
     _exec("wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle")),
    ("brightness-up", "Sound, brightness and media", "Brighter", ["XF86MonBrightnessUp"], _exec("brightnessctl set +5%")),
    ("brightness-down", "Sound, brightness and media", "Dimmer", ["XF86MonBrightnessDown"], _exec("brightnessctl set 5%-")),
    ("play-pause", "Sound, brightness and media", "Play / pause", ["XF86AudioPlay"], _exec("playerctl play-pause")),
    ("next-track", "Sound, brightness and media", "Next track", ["XF86AudioNext"], _exec("playerctl next")),
    ("previous-track", "Sound, brightness and media", "Previous track", ["XF86AudioPrev"], _exec("playerctl previous")),
]
BY_ID = {s[0]: s for s in SHORTCUTS}

MODIFIERS = {"W": "Win", "C": "Ctrl", "A": "Alt", "S": "Shift"}
MOD_ORDER = "WCAS"


def _file():
    return config_dir() / "shortcuts.json"


def load() -> dict:
    """{"keys": {id: [keys]}, "custom": [{"id", "title", "command", "keys"}]}"""
    try:
        data = json.loads(_file().read_text())
    except (OSError, ValueError):
        data = {}
    return {"keys": data.get("keys", {}), "custom": data.get("custom", [])}


def save(data: dict):
    _file().write_text(json.dumps(data, indent=2))
    apply(data)


def keys_for(data: dict, sid: str) -> list[str]:
    if sid in data["keys"]:
        return data["keys"][sid]
    if sid in BY_ID:
        return BY_ID[sid][3]
    return next((c["keys"] for c in data["custom"] if c["id"] == sid), [])


def all_current(data: dict) -> list[tuple[str, str, list[str]]]:
    """(id, title, keys) for every shortcut, built-in and your own."""
    out = [(sid, title, keys_for(data, sid)) for sid, _g, title, _k, _a in SHORTCUTS]
    out += [(c["id"], c["title"], c["keys"]) for c in data["custom"]]
    return out


def used_by(data: dict, key: str, except_id: str = "") -> tuple[str, str] | None:
    for sid, title, keys in all_current(data):
        if sid != except_id and key in keys:
            return sid, title
    return None


def set_keys(data: dict, sid: str, keys: list[str]):
    """Gives a shortcut these keys, taking them away from any other shortcut that had them."""
    for key in keys:
        while (other := used_by(data, key, sid)):
            remaining = [k for k in keys_for(data, other[0]) if k != key]
            _assign(data, other[0], remaining)
    _assign(data, sid, keys)


def _assign(data, sid, keys):
    if sid in BY_ID:
        if keys == BY_ID[sid][3]:
            data["keys"].pop(sid, None)
        else:
            data["keys"][sid] = keys
    else:
        for c in data["custom"]:
            if c["id"] == sid:
                c["keys"] = keys


def reset(data: dict, sid: str):
    data["keys"].pop(sid, None)


def add_custom(data: dict, title: str, command: str, keys: list[str]) -> str:
    sid = "custom-" + uuid.uuid4().hex[:8]
    data["custom"].append({"id": sid, "title": title, "command": command, "keys": []})
    set_keys(data, sid, keys)
    return sid


def remove_custom(data: dict, sid: str):
    data["custom"] = [c for c in data["custom"] if c["id"] != sid]


# --- labwc ----------------------------------------------------------------------------------------

def _keybind(parent, key: str, actions_xml: str):
    kb = ET.SubElement(parent, "keybind", key=key)
    if key in ("Super_L", "Super_R"):
        kb.set("onRelease", "yes")
    for action in ET.fromstring(f"<x>{actions_xml}</x>"):
        kb.append(action)


def apply(data: dict):
    """Writes your changes into ~/.config/labwc/rc.xml and reloads labwc."""
    from jaceos.settings import LabwcConfig   # the same file Settings edits for windows and touchpad
    lc = LabwcConfig()
    kb = lc.node("keyboard")
    for old in kb.findall("keybind"):
        kb.remove(old)
    # Keys that end up doing something; a default key that's no longer any of these is cleared.
    assigned = {k for sid, keys in data["keys"].items() if sid in BY_ID for k in keys}
    assigned |= {k for c in data["custom"] for k in c["keys"]}
    cleared = set()
    for sid, keys in data["keys"].items():
        if sid not in BY_ID:
            continue
        for k in BY_ID[sid][3]:
            if k not in keys and k not in assigned and k not in cleared:
                cleared.add(k)
                _keybind(kb, k, '<action name="None" />')
    for sid, keys in data["keys"].items():
        if sid in BY_ID:
            for k in keys:
                _keybind(kb, k, BY_ID[sid][4])
    for c in data["custom"]:
        for k in c["keys"]:
            _keybind(kb, k, f'<action name="Execute" command="{_xml_attr(c["command"])}" />')
    lc.save()


def _xml_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


# --- showing and reading keys ---------------------------------------------------------------------

def pretty(key: str) -> str:
    """"W-S-s" -> "Win+Shift+S"."""
    if key in ("Super_L", "Super_R"):
        return "Win"
    parts = key.split("-")
    mods, name = parts[:-1], parts[-1] or "-"
    names = {"Return": "Enter", "period": ".", "comma": ",", "Escape": "Esc", "Print": "Print Screen",
             "Left": "←", "Right": "→", "Up": "↑", "Down": "↓", "space": "Space", "Delete": "Delete",
             "XF86AudioRaiseVolume": "Volume up key", "XF86AudioLowerVolume": "Volume down key",
             "XF86AudioMute": "Mute key", "XF86AudioMicMute": "Mic mute key",
             "XF86MonBrightnessUp": "Brightness up key", "XF86MonBrightnessDown": "Brightness down key",
             "XF86AudioPlay": "Play key", "XF86AudioNext": "Next key", "XF86AudioPrev": "Previous key",
             "minus": "-", "equal": "=", "slash": "/", "backslash": "\\", "grave": "`"}
    shown = names.get(name, name.upper() if len(name) == 1 else name)
    return "+".join([MODIFIERS.get(m, m) for m in mods] + [shown])


def from_event(keyval: int, state) -> str | None:
    """A key press in GTK -> labwc's name for it, or None while only modifiers are held."""
    from gi.repository import Gdk
    name = Gdk.keyval_name(Gdk.keyval_to_lower(keyval)) or ""
    if name in ("Super_L", "Super_R") and not (state & (Gdk.ModifierType.CONTROL_MASK |
                                                        Gdk.ModifierType.ALT_MASK |
                                                        Gdk.ModifierType.SHIFT_MASK)):
        return "Super_L"
    if name.startswith(("Shift", "Control", "Alt", "Super", "Meta", "Hyper", "ISO_")) or not name:
        return None
    mods = []
    if state & Gdk.ModifierType.SUPER_MASK:
        mods.append("W")
    if state & Gdk.ModifierType.CONTROL_MASK:
        mods.append("C")
    if state & Gdk.ModifierType.ALT_MASK:
        mods.append("A")
    if state & Gdk.ModifierType.SHIFT_MASK:
        mods.append("S")
    return "-".join(mods + [name])
