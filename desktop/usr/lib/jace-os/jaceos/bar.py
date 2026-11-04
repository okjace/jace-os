"""The Jace OS top bar (Waybar), built from your choices in Settings → Top bar.

  jace-bar            write the bar's config and start Waybar (run at sign-in)
  jace-bar --reload   write it again and tell the running bar to reload

Your choices are in ~/.config/jace-os/bar.json. The bar's look is
/usr/share/jace-os/xdg/waybar/style.css (or ~/.config/waybar/style.css). If you write
your own ~/.config/waybar/config.jsonc, that's used instead and Settings leaves it alone.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from jaceos import config_dir

# id: (title, what it shows, zone, on by default)
MODULES = {
    "start": ("Start button", "The J button that opens the start menu", "left", True),
    "workspaces": ("Desktops", "Your desktops, to switch between them", "left", True),
    "taskbar": ("Open windows", "An icon for each open window", "left", True),
    "clock": ("Clock", "Click it for the calendar", "center", True),
    "media": ("Now playing", "What's playing, with play and pause", "center", True),
    "privacy": ("Camera, microphone and screen sharing", "Shows when something is using them", "right", True),
    "tray": ("App icons (tray)", "Icons apps put in the bar", "right", True),
    "updates": ("Updates", "Shows when updates are ready", "right", True),
    "weather": ("Weather", "From wttr.in, which sees your internet address to guess where you are", "right", False),
    "cpu": ("Processor", "How busy the processor is", "right", False),
    "memory": ("Memory", "How much memory is in use", "right", False),
    "temperature": ("Temperature", "How hot the processor is", "right", False),
    "disk": ("Disk space", "How full your disk is", "right", False),
    "clipboard": ("Clipboard history", "Things you copied (also Win+V)", "right", False),
    "dnd": ("Do not disturb", "Hide notifications until you turn it off", "right", True),
    "caffeine": ("Keep awake", "Stop the screen locking or turning off", "right", False),
    "power-profile": ("Power mode", "Battery saver, balanced or performance", "right", False),
    "backlight": ("Brightness", "Screen brightness (scroll to change)", "right", False),
    "sound": ("Volume", None, "right", True),
    "network": ("Wi-Fi and network", None, "right", True),
    "bluetooth": ("Bluetooth", None, "right", True),
    "battery": ("Battery", None, "right", True),
    "account": ("Your account", "Your picture and name; click for your Jace account", "right", True),
    "power": ("Power button", "Lock, sign out, restart and shut down", "right", True),
}
ORDER = list(MODULES)

# Window app id -> .desktop entry (without .desktop) for the taskbar's icons.
APP_ID_ENTRIES = {
    "jace": "jace-browser",
    "io.github.jacelauncher.JaceLauncher": "jace-launcher",
    "io.github.jace_deb.JaceSocial": "jace-social",
    "io.github.okjace.JaceStore": "jace-store",
    "org.jaceos.Settings": "jace-settings",
    "org.jaceos.Welcome": "jace-welcome",
    "org.jaceos.Installer": "jace-welcome",
    "org.jaceos.Migrate": "jace-browser",
    "zen": "zen",
}

DEFAULTS = {
    "position": "top",
    "size": "normal",
    "modules": {k: v[3] for k, v in MODULES.items()},
    "clock": {"24h": True, "seconds": False, "date": True},
}
SIZES = {"compact": 30, "normal": 34, "large": 40}


def _file() -> Path:
    return config_dir() / "bar.json"


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        mine = json.loads(_file().read_text())
    except (OSError, ValueError):
        mine = {}
    cfg["position"] = mine.get("position", cfg["position"])
    cfg["size"] = mine.get("size", cfg["size"])
    cfg["modules"].update(mine.get("modules", {}))
    cfg["clock"].update(mine.get("clock", {}))
    return cfg


def save(cfg: dict):
    _file().write_text(json.dumps(cfg, indent=2))
    reload()


def clock_formats(clock: dict) -> tuple[str, int]:
    """(Waybar clock format, update interval in seconds). Waybar formats with C++ chrono,
    which has no GNU "%-d"-style flags, so days and 12-hour hours keep their leading zero."""
    t = "%H:%M" if clock.get("24h", True) else "%I:%M"
    if clock.get("seconds"):
        t += ":%S"
    if not clock.get("24h", True):
        t += " %p"
    if clock.get("date", True):
        t = "%a %b %d  " + t
    return "{:" + t + "}", 1 if clock.get("seconds") else 30


def waybar_config(cfg: dict) -> dict:
    fmt, interval = clock_formats(cfg["clock"])
    defs = {
        "start": ("custom/start", {"format": "J", "tooltip-format": "Start (Windows key)",
                                   "on-click": "jace-menu", "on-click-right": "jace-settings"}),
        "workspaces": ("ext/workspaces", {"format": "{name}", "on-click": "activate", "sort-by-id": True}),
        "taskbar": ("wlr/taskbar", {"format": "{icon}", "icon-size": 18, "tooltip-format": "{title}",
                                    "on-click": "minimize-raise", "on-click-middle": "close",
                                    "on-click-right": "maximize", "ignore-list": ["Waybar"],
                                    # Exact entries for apps whose window id isn't their .desktop name, so the
                                    # taskbar never guesses (and shows another app's icon).
                                    "app_ids-mapping": APP_ID_ENTRIES}),
        "clock": ("clock", {"format": fmt, "interval": interval, "on-click": "jace-calendar",
                            "tooltip-format": "<tt>{calendar}</tt>",
                            "calendar": {"mode": "month",
                                         "format": {"today": "<span color='#3ddc84'><b>{}</b></span>"}}}),
        "media": ("mpris", {"format": "{status_icon}  {dynamic}", "dynamic-len": 40,
                            "dynamic-order": ["title", "artist"], "dynamic-separator": " · ",
                            "status-icons": {"playing": "󰏤", "paused": "󰐊", "stopped": "󰐊"},
                            "tooltip-format": "{player}: {title} · {artist}\nClick: play/pause · "
                                              "Right-click: next · Middle-click: previous",
                            "on-click": "play-pause", "on-click-right": "next", "on-click-middle": "previous"}),
        "privacy": ("privacy", {"icon-size": 14, "icon-spacing": 6, "transition-duration": 200}),
        "tray": ("tray", {"icon-size": 16, "spacing": 8}),
        "updates": ("custom/updates", {"exec": "jace-updates --waybar", "return-type": "json", "interval": 1800,
                                       "signal": 8, "format": "{}", "on-click": "jace-settings --page updates"}),
        "weather": ("custom/weather", {"exec": "curl -fsm 10 'https://wttr.in/?format=%c%t' | tr -d '+'",
                                       "interval": 1800, "format": "{}",
                                       "on-click": "xdg-open https://wttr.in"}),
        "cpu": ("cpu", {"format": "󰍛 {usage}%", "interval": 3, "on-click": "gnome-system-monitor"}),
        "memory": ("memory", {"format": "󰘚 {percentage}%", "interval": 5,
                              "tooltip-format": "{used:0.1f} GB of {total:0.1f} GB", "on-click": "gnome-system-monitor"}),
        "temperature": ("temperature", {"format": "󰔏 {temperatureC}°", "critical-threshold": 85}),
        "disk": ("disk", {"format": "󰋊 {percentage_used}%", "path": "/",
                          "tooltip-format": "{used} of {total} used", "on-click": "baobab"}),
        "clipboard": ("custom/clipboard", {"format": "󰅌", "tooltip-format": "Clipboard history (Win+V)",
                                           "on-click": "jace-clipboard"}),
        "dnd": ("custom/dnd", {"exec": "jace-dnd --waybar", "return-type": "json", "interval": 60, "signal": 10,
                               "format": "{}", "on-click": "jace-dnd --toggle"}),
        "caffeine": ("idle_inhibitor", {"format": "{icon}", "format-icons": {"activated": "󰅶", "deactivated": "󰾪"},
                                        "tooltip-format-activated": "Keeping the screen awake",
                                        "tooltip-format-deactivated": "Keep the screen awake"}),
        "power-profile": ("power-profiles-daemon", {"format": "{icon}", "format-icons": {
            "default": "󰾅", "performance": "󰓅", "balanced": "󰾅", "power-saver": "󰾆"}}),
        "backlight": ("backlight", {"format": "󰃟 {percent}%", "on-scroll-up": "brightnessctl set +5%",
                                    "on-scroll-down": "brightnessctl set 5%-"}),
        "sound": ("pulseaudio", {"format": "{icon} {volume}%", "format-muted": "󰝟",
                                 "format-icons": {"default": ["󰕿", "󰖀", "󰕾"], "headphone": "󰋋"},
                                 "scroll-step": 5, "on-click": "jace-settings --page sound",
                                 "on-click-right": "wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle"}),
        "network": ("network", {"format-wifi": "󰤨", "format-ethernet": "󰈀", "format-disconnected": "󰤭",
                                "tooltip-format-wifi": "{essid} ({signalStrength}%)",
                                "tooltip-format-ethernet": "{ifname}: {ipaddr}",
                                "on-click": "jace-settings --page network"}),
        "bluetooth": ("bluetooth", {"format": "󰂯", "format-off": "󰂲", "format-disabled": "",
                                    "format-connected": "󰂱 {num_connections}",
                                    "tooltip-format-connected": "{device_enumerate}",
                                    "tooltip-format-enumerate-connected": "{device_alias}",
                                    "on-click": "jace-settings --page bluetooth"}),
        "battery": ("battery", {"states": {"warning": 25, "critical": 10}, "format": "{icon} {capacity}%",
                                "format-charging": "󰂄 {capacity}%", "format-icons": ["󰁺", "󰁼", "󰁾", "󰂀", "󰁹"],
                                "on-click": "jace-settings --page power"}),
        # Your picture and name, as one island (both refresh on signal 9 when you sign in or out).
        "account": ("group/account", {"orientation": "horizontal", "modules": ["image#avatar", "custom/jace"]}),
        "power": ("custom/power", {"format": "⏻", "tooltip-format": "Lock, sleep, restart or shut down",
                                   "on-click": "jace-power"}),
    }
    out = {"layer": "top", "position": cfg["position"], "height": SIZES.get(cfg["size"], 34),
           "margin-top": 6 if cfg["position"] == "top" else 0,
           "margin-bottom": 6 if cfg["position"] == "bottom" else 0,
           "margin-left": 8, "margin-right": 8, "spacing": 6, "exclusive": True,
           "modules-left": [], "modules-center": [], "modules-right": []}
    for mid in ORDER:
        if not cfg["modules"].get(mid):
            continue
        name, conf = defs[mid]
        out[f"modules-{MODULES[mid][2]}"].append(name)
        out[name] = conf
        if mid == "account":
            out["image#avatar"] = {"exec": "jace-account --avatar", "size": 22, "interval": 600, "signal": 9,
                                   "on-click": "jace-settings --page account"}
            out["custom/jace"] = {"exec": "jace-account --waybar", "return-type": "json", "interval": 300,
                                  "signal": 9, "format": "{}", "on-click": "jace-settings --page account"}
    return out


def _paths():
    user = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "waybar"
    style = user / "style.css"
    if not style.exists():
        style = Path(os.environ.get("JACE_OS_ROOT", "/")) / "usr/share/jace-os/xdg/waybar/style.css"
    own = user / "config.jsonc"
    generated = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "jace-os" / "waybar.jsonc"
    return own, generated, style


def write() -> Path:
    own, generated, _style = _paths()
    if own.exists():
        return own
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_text("// Made by jace-bar from Settings → Top bar. Edits here are replaced.\n" +
                         json.dumps(waybar_config(load()), indent=2, ensure_ascii=False))
    return generated


def reload():
    write()
    # Waybar re-reads its config on SIGUSR2.
    subprocess.run(["pkill", "-USR2", "-x", "waybar"], capture_output=True)
    subprocess.run(["pkill", "-RTMIN+10", "-x", "waybar"], capture_output=True)


def main():
    if "--reload" in sys.argv:
        reload()
        return
    config = write()
    _own, _gen, style = _paths()
    os.execvp("waybar", ["waybar", "-c", str(config), "-s", str(style)])
