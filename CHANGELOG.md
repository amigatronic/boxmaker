# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/) once
the first tagged release is published.

## [v.1.0]

### Added
- STEP export alongside STL, via a format dropdown in the save dialog.
  STEP preserves true B-Rep geometry, so the exported model stays fully
  editable (holes, bosses, grooves, etc.) in any downstream CAD program.
- Multi-language UI (English, Italian, Spanish, French), switchable at
  runtime without restarting the app.
- Corner rounding (fillet) on the 4 outer and 4 inner corners, with an
  automatically clamped, geometrically safe radius.
- Session persistence (window position, all control values, selected
  language) across launches.
- Realistic rendering mode with ray-traced shadows (optional).
- On-screen dimension overlay and 3D axis/dimension annotations.
- Cut-away preview on any axis (X/Y/Z), independent of the exported
  model.
- Loading splash screen with staged progress during startup.

### Notes
- The project currently supports **prismatic (rectangular) boxes only**.
  See the Roadmap section in [README.md](README.md) for planned shapes
  (cylindrical, octagonal/N-gon, freeform spline-based extrusion).
- `box_maker.py` is kept in the repository as the original standalone
  prototype; it predates the modular structure (`geometry.py`,
  `rendering.py`, etc.) and the i18n system, and is not used by
  `main.py`.

## [0.1.0] - initial internal version

- First working version: parametric rectangular box with adjustable
  width/depth/height/wall thickness, live 3D preview, STL export.
