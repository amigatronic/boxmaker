"""
Model export.

Warning: always exports the FULL solid (never the clipped version used
only for the preview) — it is the caller's responsibility to pass the
correct part.

Two formats are supported:
  - STL: a triangulated mesh (no real surfaces/topology). Fine for
    printing as-is, but a CAD program can only reverse-engineer it back
    into editable geometry (lossy, imprecise on fillets).
  - STEP: a true B-Rep export (build123d/OCCT preserves exact surfaces,
    edges and vertices), so it opens as native, fully editable geometry
    in any CAD (holes, bosses, grooves, etc. can be added directly).
"""

from build123d import export_stl, export_step


def export_to_stl(part, path: str) -> None:
    export_stl(part, path)


def export_to_step(part, path: str) -> None:
    export_step(part, path)
