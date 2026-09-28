"""
Session persistence between one launch and the next.

Uses QSettings (native to Qt, no extra dependency): Windows registry,
.ini file in ~/.config on Linux, plist on macOS. Requires no special
permissions and no paths to manage by hand.

We save two separate things:
  - "language": a plain string, read BEFORE building the UI (needed to
    create the labels already in the right language from startup).
  - "state_json": a single JSON blob with all slider/checkbox/color
    values, applied AFTER the widgets exist. A single blob instead of
    many individual keys avoids the type inconsistencies that QSettings
    can introduce across different platforms (bool/float saved as string
    on some backends).
"""

import json

from PySide6.QtCore import QSettings

_ORG = "BoxMakerHobby"
_APP = "BoxMaker"


def get_settings() -> QSettings:
    return QSettings(_ORG, _APP)


def save_window_geometry(window, settings: QSettings | None = None):
    settings = settings or get_settings()
    settings.setValue("window/geometry", window.saveGeometry())


def restore_window_geometry(window, settings: QSettings | None = None) -> bool:
    settings = settings or get_settings()
    geo = settings.value("window/geometry")
    if geo is None:
        return False
    window.restoreGeometry(geo)
    return True


def load_language(default: str, settings: QSettings | None = None) -> str:
    settings = settings or get_settings()
    value = settings.value("language", default)
    return value if isinstance(value, str) else default


def save_language(lang: str, settings: QSettings | None = None):
    settings = settings or get_settings()
    settings.setValue("language", lang)


def load_state(settings: QSettings | None = None) -> dict:
    settings = settings or get_settings()
    raw = settings.value("state_json", "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def save_state(state: dict, settings: QSettings | None = None):
    settings = settings or get_settings()
    settings.setValue("state_json", json.dumps(state))
