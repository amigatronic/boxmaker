"""
Localization (i18n).

Each language is a flat key -> text JSON file in locales/<code>.json.
This format was chosen on purpose to be easy to read and translate "on
the fly" by an AI or a human translator: no nested structure, every line
is self-explanatory, placeholders in curly braces (e.g. {w:.2f}) must be
left identical, the rest of the text is free.

To add a language: copy locales/en.json, translate the values (not the
keys to the left of the colon), save as locales/<code>.json and add the
code to SUPPORTED_LANGUAGES + LANGUAGE_NAMES below. No other code needs
to be touched: the app will use it automatically.
"""

import json
from pathlib import Path

LOCALES_DIR = Path(__file__).parent / "locales"

DEFAULT_LANGUAGE = "en"

SUPPORTED_LANGUAGES = ["en", "it", "es", "fr"]

LANGUAGE_NAMES = {
    "en": "English",
    "it": "Italiano",
    "es": "Español",
    "fr": "Français",
}

_cache: dict[str, dict] = {}


def _load_language_file(lang: str) -> dict:
    if lang in _cache:
        return _cache[lang]

    path = LOCALES_DIR / f"{lang}.json"
    if not path.exists():
        return {}

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    _cache[lang] = data
    return data


class Translator:
    """Translates keys into text for the current language, with automatic
    fallback to English for any key missing from a partial translation
    (so an incomplete language doesn't break the UI)."""

    def __init__(self, lang: str = DEFAULT_LANGUAGE):
        self._fallback = _load_language_file(DEFAULT_LANGUAGE)
        self.set_language(lang)

    def set_language(self, lang: str):
        if lang not in SUPPORTED_LANGUAGES:
            lang = DEFAULT_LANGUAGE
        self.lang = lang
        self._strings = _load_language_file(lang)

    def t(self, key: str, **kwargs) -> str:
        """Returns the translated text for `key`, formatted with `kwargs`
        if the template contains placeholders (e.g. {w:.2f})."""
        template = self._strings.get(key, self._fallback.get(key, key))

        if not kwargs:
            return template

        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            # Malformed template or missing placeholder: better to show
            # something readable than crash the UI.
            return template
