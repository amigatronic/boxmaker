# Contributing to Box Maker

Thanks for considering a contribution — this is a small hobby project
and any help is welcome, from bug reports to full features.

## Before you start

This project is evolving quickly (see the Roadmap in
[README.md](README.md)). If you're planning a larger feature (e.g. a
new box shape), please open an issue first to discuss the approach —
it can save you from reworking something that's already half-planned.

## Reporting bugs

Please include:

- Your OS and Python version (or, if using the packaged `.exe`, the
  release version number)
- Steps to reproduce
- What you expected vs. what happened
- The console output, if the app printed an error (the app logs errors
  to stdout/stderr; run it from a terminal to capture them)

## Development setup

```bash
git clone https://github.com/<your-username>/box-maker.git
cd box-maker
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Code style and conventions

- **All code, comments, docstrings, log/console messages, and
  identifiers must be in English.** Only the strings in `locales/*.json`
  are translated (see below).
- Follow the existing module boundaries: geometry logic stays in
  `geometry.py` (no Qt/PyVista imports there), rendering stays in
  `rendering.py`, etc. `main_window.py` is the wiring layer — if you're
  adding geometry, it likely belongs in `geometry.py`, not there.
- Keep functions small and testable where practical; this is a GUI app,
  so full unit test coverage isn't expected, but pure-logic functions
  (geometry, dimension math) benefit from it.

## Adding or updating a translation

Every UI string lives in a flat JSON file: `locales/<language-code>.json`.
To add a new language:

1. Copy `locales/en.json` to `locales/<code>.json`.
2. Translate the **values** only — never the keys (the text to the
   left of the colon must stay identical).
3. Leave any `{placeholder}` (e.g. `{w:.2f}`) exactly as-is; only the
   surrounding text should be translated.
4. Add `<code>` to `SUPPORTED_LANGUAGES` and its display name to
   `LANGUAGE_NAMES` in `i18n.py`.

No other code changes are needed — the app picks up the new language
automatically.

## Submitting changes

1. Fork the repository and create a branch from `main`.
2. Make your changes, keeping commits focused and descriptive.
3. Verify the app still starts and the feature you touched works as
   expected (`python main.py`).
4. Open a pull request describing what changed and why.

## License

By contributing, you agree that your contributions will be licensed
under the project's [GNU GPLv3 license](LICENSE).
