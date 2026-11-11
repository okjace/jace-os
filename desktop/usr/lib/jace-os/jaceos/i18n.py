"""Translations for the Jace OS apps.

Catalogs are JSON files in /usr/share/jace-os/locale, one per language ("de.json",
"pt_BR.json"), each mapping the English text to its translation. Text without a translation
stays in English. GNOME apps, Jace Browser and the other apps follow the system language
by themselves (Settings → Keyboard & language).

  from jaceos.i18n import _
  label = Gtk.Label(label=_("Continue"))
"""
import json
import os
from pathlib import Path

from jaceos import SHARE

LOCALE_DIR = SHARE / "locale"
RTL = ("ar", "he", "fa", "ur")

_catalog: dict = {}
_language = "en"


def _load(code: str) -> dict:
    try:
        return json.loads((LOCALE_DIR / f"{code}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def set_language(locale: str):
    """locale: like "pt_BR.UTF-8", "de_DE" or "fr". Uses pt_BR.json, then pt.json."""
    global _catalog, _language
    full = (locale or "en").split(".")[0]
    short = full.split("_")[0]
    _language = short
    _catalog = {**_load(short), **_load(full)} if short != "en" else {}


def language() -> str:
    return _language


def is_rtl() -> bool:
    return _language in RTL


def _(text: str) -> str:
    return _catalog.get(text) or text


def system_locale() -> str:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(var):
            return os.environ[var]
    try:
        for line in Path("/etc/locale.conf").read_text().splitlines():
            if line.startswith("LANG="):
                return line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    return "en_US.UTF-8"


set_language(system_locale())
