"""
2D profiles (contours) from which the box is extruded.

No Qt or PyVista dependency in here: only build123d. Every builder
returns a planar Face lying on the XY plane, with the center of its
bounding box on the origin, so the rest of the code can treat every
shape the same way.

Supported shapes:
  - rect     rectangle (width x depth)
  - circle   circle (width = diameter)
  - ellipse  ellipse (width x depth = full axes)
  - polygon  regular polygon with N sides (width = circumscribed diameter)
  - stadium  rectangle with fully rounded ends / slot (width x depth)
  - svg      closed contour imported from an SVG file, scaled so its
             bounding box has the requested width (depth follows the
             original aspect ratio)
  - text     custom text set in a TrueType/OpenType font. The outline of
             each glyph (holes of letters like A, B, O included) comes from
             the OpenCascade font module, so no manual glyph parsing is
             needed. Scaled to the requested overall width; an optional
             "grow" thickens the letters so they fuse into one piece.
"""

import functools
import logging
import math
from dataclasses import dataclass
from pathlib import Path

from build123d import (
    Circle,
    Compound,
    Ellipse,
    Face,
    Kind,
    Pos,
    Rectangle,
    RegularPolygon,
    SlotOverall,
    Text,
    Wire,
    import_svg,
    offset,
)

log = logging.getLogger(__name__)

SHAPE_RECT = "rect"
SHAPE_CIRCLE = "circle"
SHAPE_ELLIPSE = "ellipse"
SHAPE_POLYGON = "polygon"
SHAPE_STADIUM = "stadium"
SHAPE_SVG = "svg"
SHAPE_TEXT = "text"

SHAPE_CODES = [
    SHAPE_RECT,
    SHAPE_CIRCLE,
    SHAPE_ELLIPSE,
    SHAPE_POLYGON,
    SHAPE_STADIUM,
    SHAPE_SVG,
    SHAPE_TEXT,
]

MIN_SIDES = 3
MAX_SIDES = 64

# Shapes whose depth is NOT a free parameter (derived from the width).
SHAPES_WITHOUT_DEPTH = {SHAPE_CIRCLE, SHAPE_POLYGON, SHAPE_SVG, SHAPE_TEXT}

# Font used when no font file is chosen (looked up among the system fonts).
DEFAULT_FONT = "Arial"
MAX_GROW = 10.0

# Angle (degrees) between the two outgoing tangents of a vertex below which
# the vertex is considered a real corner. Two edges that continue smoothly
# have outgoing tangents pointing in opposite directions (180 degrees).
_CORNER_ANGLE_DEG = 175.0

_POINT_TOL = 1e-3  # mm


@dataclass(frozen=True)
class ShapeSpec:
    """Hashable description of the shape (used as part of cache keys).

    `svg_token` changes every time an SVG is (re)imported, so that the
    geometry cache is invalidated even when the path is the same.
    """

    kind: str = SHAPE_RECT
    sides: int = 6
    svg_path: str = ""
    svg_token: int = 0
    text: str = ""
    font_path: str = ""
    grow: float = 0.0  # mm, thickens the text outline (letters fuse together)


class ShapeError(Exception):
    """Raised when a profile cannot be built. `code` is a stable identifier
    used to pick the translated message."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code
        self.detail = detail


class SvgImportError(ShapeError):
    """Raised when an SVG file cannot be used as a box profile."""


@dataclass(frozen=True)
class SvgProfile:
    face: Face  # contour (with holes cut), bounding box centered on the origin
    holes_used: int
    shapes_ignored: int  # open paths + closed shapes outside the main contour


# (path, token) -> SvgProfile
_svg_store: dict[tuple[str, int], SvgProfile] = {}


def _center_on_origin(face: Face) -> Face:
    bb = face.bounding_box()
    c = bb.center()
    return Pos(-c.X, -c.Y, 0) * face


def _faces_of(shape) -> list[Face]:
    return list(shape.faces())


def load_svg_profile(path: str, token: int) -> SvgProfile:
    """Reads the SVG and stores the resulting profile under (path, token).

    The largest closed shape is the contour. Other closed shapes that lie
    inside it are cut out as holes. Open paths and closed shapes outside
    the contour are ignored. Raises SvgImportError if no closed shape is
    found or the file cannot be read."""

    if not Path(path).is_file():
        raise SvgImportError("file_not_found", path)

    try:
        raw = import_svg(path)
    except Exception as e:
        raise SvgImportError("read_failed", str(e)) from e

    faces: list[Face] = []
    ignored = 0

    for item in raw:
        if isinstance(item, Face):
            faces.append(item)
        elif isinstance(item, Wire) and item.is_closed:
            try:
                faces.append(Face(item))
            except Exception:
                ignored += 1
        else:
            ignored += 1  # open path

    faces = [f for f in faces if f.area > 1e-6]
    if not faces:
        raise SvgImportError("no_closed_shape")

    faces.sort(key=lambda f: f.area, reverse=True)
    contour = faces[0]
    holes_used = 0

    cb = contour.bounding_box()
    for other in faces[1:]:
        ob = other.bounding_box()
        inside_bbox = (
            ob.min.X >= cb.min.X - _POINT_TOL
            and ob.min.Y >= cb.min.Y - _POINT_TOL
            and ob.max.X <= cb.max.X + _POINT_TOL
            and ob.max.Y <= cb.max.Y + _POINT_TOL
        )
        if not inside_bbox:
            ignored += 1
            continue
        try:
            cut = _faces_of(contour.cut(other))
        except Exception:
            ignored += 1
            continue
        if len(cut) >= 1:
            best = max(cut, key=lambda f: f.area)
            if best.area < contour.area - 1e-6:
                contour = best
                holes_used += 1
                continue
        ignored += 1

    bb = contour.bounding_box()
    if bb.size.X < 1e-6 or bb.size.Y < 1e-6:
        raise SvgImportError("degenerate_shape")

    profile = SvgProfile(_center_on_origin(contour), holes_used, ignored)
    _svg_store[(path, token)] = profile
    return profile


def _as_face(sketch) -> Face:
    return sketch.face()


def _svg_face(spec: ShapeSpec, width: float) -> Face:
    profile = _svg_store.get((spec.svg_path, spec.svg_token))
    if profile is None:
        raise SvgImportError("not_loaded", spec.svg_path)

    bb = profile.face.bounding_box()
    factor = width / bb.size.X
    return profile.face.scale(factor)


@functools.lru_cache(maxsize=8)
def _raw_text_faces(text: str, font_path: str) -> tuple:
    """Glyph faces of `text` at a reference size (cached: reading the font
    is the slow part, the rest only scales)."""
    if font_path and Path(font_path).is_file():
        shaped = Text(text, font_size=100, font_path=font_path)
    else:
        shaped = Text(text, font_size=100, font=DEFAULT_FONT)

    faces = tuple(f for f in shaped.faces() if f.area > 1e-6)
    if not faces:
        raise ShapeError("text_no_outline")
    return faces


def _grow_faces(faces: list, grow: float) -> list:
    """Thickens every face by `grow` mm. If OCCT cannot offset a face (it
    happens when the grow is larger than a hole of the glyph, e.g. the
    counter of an 'e'), the holes of that face are dropped and it is tried
    again: a hole that small would have closed anyway."""
    out = []
    for face in faces:
        try:
            out.extend(offset(face, amount=grow, kind=Kind.ARC).faces())
            continue
        except Exception:
            pass
        try:
            solid_face = Face(face.outer_wire())
            out.extend(offset(solid_face, amount=grow, kind=Kind.ARC).faces())
        except Exception as e:
            log.debug("Glyph kept without growing: %s", e)
            out.append(face)
    return out


def _union_faces(faces: list) -> list:
    """Fuses overlapping faces into as few faces as possible. Falls back to
    the faces as they are when the result is not a valid shape."""
    if len(faces) < 2:
        return faces
    try:
        fused = faces[0].fuse(*faces[1:], tol=1e-3).clean().fix()
        merged = list(fused.faces())
        if merged and fused.is_valid:
            return merged
    except Exception as e:
        log.debug("Glyph fusion skipped: %s", e)
    return faces


def _offset_faces(faces: list, delta: float) -> list:
    """Offsets glyph faces by `delta` mm (> 0 grows, < 0 shrinks, 0 keeps).
    Overlapping results of a growth are fused. A face that vanishes when
    shrinking is simply dropped (it has no cavity)."""
    if abs(delta) < 1e-9:
        return list(faces)

    if delta > 0:
        return _union_faces(_grow_faces(faces, delta))

    out = []
    for face in faces:
        try:
            result = offset(face, amount=delta, kind=Kind.INTERSECTION)
            out.extend(f for f in result.faces() if f.area > 1e-6)
        except Exception as e:
            log.debug("Glyph too thin for this wall, no cavity for it: %s", e)
    return out


def _scaled_text_faces(spec: ShapeSpec, width: float) -> list:
    """Glyph faces scaled so that, once thickened by `grow`, the overall
    width is `width`, and centered on the origin. Both the outer contour
    and the cavity contour start from these same faces."""
    if not spec.text.strip():
        raise ShapeError("text_empty")

    grow = max(0.0, min(MAX_GROW, spec.grow))
    target = width - 2 * grow
    if target < 1.0:
        raise ShapeError("grow_too_large")

    faces = _raw_text_faces(spec.text, spec.font_path)
    block = Compound(list(faces))
    bb = block.bounding_box()
    if bb.size.X < 1e-6:
        raise ShapeError("text_no_outline")

    # Scale the whole text as ONE block: scaling each glyph separately
    # would shrink every letter around its own center and keep the gaps.
    c = bb.center()
    block = (Pos(-c.X, -c.Y, 0) * block).scale(target / bb.size.X)
    return list(block.faces())


def _text_profile(spec: ShapeSpec, width: float):
    """Outer contour of the text: glyphs thickened by `grow`.

    Each glyph is a face whose holes (counters of A, B, O...) are real
    inner contours. With grow > 0 the glyphs are fused, so touching or
    overlapping letters become a single piece."""
    grow = max(0.0, min(MAX_GROW, spec.grow))
    return Compound(_offset_faces(_scaled_text_faces(spec, width), grow))


def text_inner_faces(spec: ShapeSpec, width: float, wall: float) -> list:
    """Cavity contour of the text box: the original glyphs offset by
    (grow - wall). Computed from the glyphs themselves, not by shrinking
    the (possibly fused, messy) outer contour, which is far more robust."""
    grow = max(0.0, min(MAX_GROW, spec.grow))
    return _offset_faces(_scaled_text_faces(spec, width), grow - wall)


def build_outer_profile(spec: ShapeSpec, width: float, depth: float) -> Face:
    """Outer contour of the box, centered on the origin (XY plane)."""

    kind = spec.kind

    if kind == SHAPE_CIRCLE:
        face = _as_face(Circle(width / 2))
    elif kind == SHAPE_ELLIPSE:
        face = _as_face(Ellipse(width / 2, depth / 2))
    elif kind == SHAPE_POLYGON:
        sides = max(MIN_SIDES, min(MAX_SIDES, int(spec.sides)))
        face = _as_face(RegularPolygon(radius=width / 2, side_count=sides))
    elif kind == SHAPE_STADIUM:
        if width >= depth:
            face = _as_face(SlotOverall(width, depth))
        else:
            face = _as_face(SlotOverall(depth, width, rotation=90))
    elif kind == SHAPE_SVG:
        face = _svg_face(spec, width)
    elif kind == SHAPE_TEXT:
        # Already centered; the cavity contour is derived from the same base,
        # so no extra recentering here (it would move only the outer one).
        return _text_profile(spec, width)
    else:  # SHAPE_RECT and any unknown code
        face = _as_face(Rectangle(width, depth))

    return _center_on_origin(face)


def corner_points(face: Face) -> list[tuple[float, float]]:
    """XY positions of the real corners of the contour (and of its holes):
    vertices where the two adjacent edges do NOT continue smoothly. Only
    these vertical edges of the extruded solid get a fillet; a smooth
    profile (circle, ellipse, stadium sides, spline joints) has none."""

    # vertex key -> list of outgoing unit tangents (x, y)
    vertices: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
    scale = 1.0 / _POINT_TOL

    def add(point, tangent, sign):
        key = (round(point.X * scale / 10), round(point.Y * scale / 10))
        tx, ty = tangent.X * sign, tangent.Y * sign
        norm = math.hypot(tx, ty)
        if norm < 1e-12:
            return
        vertices.setdefault(key, []).append((point.X, point.Y, tx / norm, ty / norm))

    for wire in face.wires():
        for edge in wire.edges():
            try:
                add(edge.position_at(0.0), edge.tangent_at(0.0), +1)
                add(edge.position_at(1.0), edge.tangent_at(1.0), -1)
            except Exception as e:
                log.debug("Edge skipped while looking for corners: %s", e)

    corners = []
    for entries in vertices.values():
        if len(entries) < 2:
            continue
        (x, y, ax, ay), (_, _, bx, by) = entries[0], entries[1]
        cos_angle = max(-1.0, min(1.0, ax * bx + ay * by))
        angle = math.degrees(math.acos(cos_angle))
        if angle < _CORNER_ANGLE_DEG:
            corners.append((x, y))

    return corners
