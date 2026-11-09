"""
Installed fonts, read straight from the font folders.

No Qt dependency. Why this exists: C:\\Windows\\Fonts is a virtual shell
folder; an "Open file" dialog pointed at it can show nothing or fail with
"insufficient permissions". The real .ttf/.otf files are plain files that
can be read without any problem, so we list them ourselves.
"""

import logging
import os
from dataclasses import dataclass

from fontTools.ttLib import TTFont

log = logging.getLogger(__name__)

FONT_EXTENSIONS = (".ttf", ".otf", ".ttc")


@dataclass(frozen=True)
class FontEntry:
    family: str
    style: str
    path: str

    @property
    def label(self) -> str:
        return f"{self.family} - {self.style}" if self.style else self.family


def default_font_dirs() -> list[str]:
    """Folders with installed fonts: system-wide and per-user (Windows 10)."""
    dirs = []

    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or "C:\\Windows"
    dirs.append(os.path.join(windir, "Fonts"))

    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))

    return [d for d in dirs if os.path.isdir(d)]


def _read_names(path: str) -> tuple[str, str] | None:
    """(family, style) of a font file, or None if it is not a usable font."""
    try:
        font = TTFont(path, lazy=True, fontNumber=0)
    except Exception:
        return None

    try:
        # A font without outlines (bitmap-only) cannot give a contour.
        if "glyf" not in font and "CFF " not in font and "CFF2" not in font:
            return None
        names = font["name"]
        family = names.getBestFamilyName() or ""
        style = names.getBestSubFamilyName() or ""
        if not family:
            return None
        return family, style
    except Exception:
        return None
    finally:
        try:
            font.close()
        except Exception:
            pass


def is_usable_font(path: str) -> bool:
    """True if `path` is a font file we can take outlines from."""
    return os.path.isfile(path) and _read_names(path) is not None


def scan_fonts(dirs: list[str] | None = None) -> list[FontEntry]:
    """Installed fonts, sorted by family and style, without duplicates.
    Files that cannot be read are skipped silently."""
    if dirs is None:
        dirs = default_font_dirs()

    seen = set()
    entries = []

    for folder in dirs:
        try:
            names = sorted(os.listdir(folder))
        except OSError as e:
            log.warning("Cannot list fonts in %s: %s", folder, e)
            continue

        for name in names:
            if not name.lower().endswith(FONT_EXTENSIONS):
                continue

            path = os.path.join(folder, name)
            key = os.path.normcase(path)
            if key in seen:
                continue
            seen.add(key)

            info = _read_names(path)
            if info is not None:
                entries.append(FontEntry(info[0], info[1], path))

    entries.sort(key=lambda e: (e.family.lower(), e.style.lower()))
    return entries
