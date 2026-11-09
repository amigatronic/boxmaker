"""
Parametric modeling of the box.

No Qt or PyVista dependency in here: only build123d and measurement
calculations. This is the module to check if the PROBLEM is in the
SHAPE of the solid (wrong cavity, section that cuts badly, etc.).

The box is the extrusion of a 2D profile (see shapes.py): rectangle,
circle, ellipse, regular polygon, stadium or a contour imported from SVG.
The cavity is the same profile shrunk by one wall thickness.
"""

import copy
import functools
import logging
import math
from dataclasses import dataclass, field
from typing import NamedTuple

from build123d import Box, Compound, Face, Kind, Part, Pos, extrude, offset

from shapes import SHAPE_TEXT, ShapeSpec, build_outer_profile, corner_points, text_inner_faces

log = logging.getLogger(__name__)

_UP = (0, 0, 1)


class FilletOutcome(NamedTuple):
    """Result of build_filleted_box: the part, what was really applied, and
    the radii actually used (they can be smaller than the requested ones)."""

    part: Part
    outer_applied: bool
    inner_applied: bool
    outer_radius: float = 0.0
    inner_radius: float = 0.0


# ----------------------------------------------------------------------
# Profiles (cached: they only depend on shape, width, depth and wall).
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Profiles:
    outer: object  # Face, or Compound of faces (text, multi-piece SVG)
    inner: object  # Compound of faces of the cavity profile, or None
    width: float  # bounding box of the outer profile
    depth: float
    inner_width: float  # bounding box of the cavity profile (0 if none)
    inner_depth: float
    outer_corners: tuple = field(default_factory=tuple)
    inner_corners: tuple = field(default_factory=tuple)
    pieces: int = 1  # separate faces of the outer profile (> 1: several boxes)


@functools.lru_cache(maxsize=32)
def _compute_profiles(spec: ShapeSpec, width: float, depth: float, wall: float) -> Profiles:
    outer = build_outer_profile(spec, width, depth)
    obb = outer.bounding_box()

    inner = None
    inner_w = inner_d = 0.0
    inner_corners: tuple = ()

    if wall > 0:
        # Each face is shrunk on its own (a compound of faces cannot be
        # offset inward in one go). A face thinner than two walls simply
        # has no cavity: that piece stays solid.
        inner_faces = []
        if spec.kind == SHAPE_TEXT:
            # Text: the cavity comes from the glyphs themselves (see shapes.py).
            inner_faces = [f for f in text_inner_faces(spec, width, wall) if f.area > 1e-6]
        else:
            for face in outer.faces():
                try:
                    # INTERSECTION keeps the inner corners sharp (the fillet,
                    # if any, is applied afterwards with its own radius).
                    candidate = offset(face, amount=-wall, kind=Kind.INTERSECTION)
                    inner_faces.extend(f for f in candidate.faces() if f.area > 1e-6)
                except Exception as e:
                    log.debug("No cavity for one face (wall too thick for it): %s", e)

        if inner_faces:
            candidate = Compound(inner_faces)
            ibb = candidate.bounding_box()
            if ibb.size.X > 1e-6 and ibb.size.Y > 1e-6:
                inner = candidate
                inner_w, inner_d = ibb.size.X, ibb.size.Y
                inner_corners = tuple(pt for f in inner_faces for pt in corner_points(f))

    return Profiles(
        outer=outer,
        inner=inner,
        width=obb.size.X,
        depth=obb.size.Y,
        inner_width=inner_w,
        inner_depth=inner_d,
        outer_corners=tuple(corner_points(outer)),
        inner_corners=inner_corners,
        pieces=len(list(outer.faces())),
    )


@dataclass(frozen=True)
class BoxDimensions:
    """Box measurements, in millimeters.

    width  -> Width (X axis). Diameter for circle/polygon, bounding box
              width for SVG shapes.
    depth  -> Depth (Y axis). Ignored by circle, polygon and SVG shapes.
    height -> Height (Z axis)
    wall   -> wall thickness
    shape  -> which 2D profile is extruded (default: rectangle)
    """

    width: float
    depth: float
    height: float
    wall: float
    shape: ShapeSpec = ShapeSpec()

    @property
    def profiles(self) -> Profiles:
        return _compute_profiles(self.shape, self.width, self.depth, self.wall)

    # Real bounding box of the outer profile (differs from width/depth for
    # polygons and SVG shapes).
    @property
    def bbox_width(self) -> float:
        return self.profiles.width

    @property
    def bbox_depth(self) -> float:
        return self.profiles.depth

    @property
    def inner_width(self) -> float:
        return self.profiles.inner_width

    @property
    def inner_depth(self) -> float:
        return self.profiles.inner_depth

    @property
    def inner_height(self) -> float:
        # the cavity starts one full wall thickness above the bottom (floor
        # thickness = wall) and reaches the rim
        return self.height - self.wall

    @property
    def has_valid_cavity(self) -> bool:
        return (
            self.wall > 0
            and self.inner_height > 0
            and self.profiles.inner is not None
        )

    def describe(self) -> str:
        """Human-readable text with the measurements, for the UI panel and 3D overlay."""
        lines = [
            f"Outer (WxDxH): {self.bbox_width:.2f} x {self.bbox_depth:.2f} x {self.height:.2f} mm",
        ]
        if self.has_valid_cavity:
            lines.append(
                "Inner cavity: "
                f"{self.inner_width:.2f} x {self.inner_depth:.2f} x {self.inner_height:.2f} mm"
            )
            lines.append(f"Wall thickness: {self.wall:.2f} mm")
        else:
            lines.append("No cavity (wall = 0 or too thick)")
        return "\n".join(lines)


# ----------------------------------------------------------------------
# Solids
# ----------------------------------------------------------------------
def _extrude_profile(profile, z_bottom: float, height: float) -> Part:
    # The profile lives in a cache: extrude a COPY so the solid never
    # shares topology (and the triangulation attached to it by the viewer)
    # with the cached face.
    solid = extrude(copy.copy(profile), amount=height, dir=_UP)
    return Pos(0, 0, z_bottom) * solid


def _make_outer_solid(dims: BoxDimensions) -> Part:
    return _extrude_profile(dims.profiles.outer, -dims.height / 2, dims.height)


def _make_cavity_cutter(dims: BoxDimensions) -> Part:
    """Solid used to carve the cavity (sharp corners).

    The cutter is taller than the cavity and sticks out above the rim: its
    top face is NOT coplanar with the top face of the outer box, which
    avoids a classic boolean-operation edge case. The resulting solid is
    identical (cavity floor at one wall thickness from the bottom, open at
    the top).
    """
    overshoot = max(dims.wall, 1.0)
    cutter_height = dims.inner_height + overshoot
    floor_z = -dims.height / 2 + dims.wall
    return _extrude_profile(dims.profiles.inner, floor_z, cutter_height)


def build_box(dims: BoxDimensions) -> Part:
    """Creates the solid or hollow shape according to `dims`, with sharp corners."""
    outer = _make_outer_solid(dims)

    if not dims.has_valid_cavity:
        return outer

    return outer - _make_cavity_cutter(dims)


def apply_preview_clip(part: Part, axis: str, pos_fraction: float, dims: BoxDimensions) -> Part:
    """Section (cut-away) ONLY for the 3D preview. Never touches the model to be exported.

    `axis` uses stable, UI-language-independent codes:
    "none", "X", "Y", "Z" (not the translated text shown in the combo box)."""
    if axis == "none":
        return part

    pos_fraction = max(0.0, min(1.0, pos_fraction))

    width, depth, height = dims.bbox_width, dims.bbox_depth, dims.height
    far = max(width, depth, height) * 5 + 100
    eps = 0.01

    if axis == "X":
        x_cut = -width / 2 + width * pos_fraction - eps
        cutter = Pos(x_cut + far / 2, 0, 0) * Box(far, depth * 3 + 100, height * 3 + 100)
        return part - cutter

    if axis == "Y":
        y_cut = -depth / 2 + depth * pos_fraction - eps
        cutter = Pos(0, y_cut + far / 2, 0) * Box(width * 3 + 100, far, height * 3 + 100)
        return part - cutter

    if axis == "Z":
        z_cut = -height / 2 + height * pos_fraction - eps
        cutter = Pos(0, 0, z_cut + far / 2) * Box(width * 3 + 100, depth * 3 + 100, far)
        return part - cutter

    return part


# ----------------------------------------------------------------------
# Corner rounding (fillet).
#
# Philosophy: we do NOT round "every edge there is". We only round:
#   - the vertical edges at the real corners of the outer shell and of the
#     cavity (smooth profiles like a circle have none) - the point that
#     hits when dropped on its side;
#   - the edges of the base, outer (base perimeter) and cavity (where the
#     cavity floor meets the walls) - the point that hits when dropped on
#     its base.
# Top edges (opening rim) are deliberately left sharp: they are not a
# typical impact point and rounding them would compromise the edge a lid
# might rest on.
#
# The fillets are applied to the two SEPARATE solids (outer shape and
# cavity cutter) BEFORE the boolean subtraction. Filleting the already
# hollowed part fails as soon as the outer radius exceeds the wall
# thickness (the outer blend would run into the cavity). The inner radius
# follows the outer one (see compute_fillet_radii), so the wall keeps a
# (nearly) uniform thickness around the corners and the cavity is always
# rounded.
#
# When there is no cavity (closed prism) there is no opening rim to
# protect, so ALL the perimeter edges (top and bottom) are rounded.
# ----------------------------------------------------------------------

_POSITION_TOL = 1e-3  # mm

# Radius multipliers tried in order when OCCT cannot build the fillet with
# the requested radius (typical with complex SVG contours).
_RETRY_FACTORS = (1.0, 0.7, 0.45, 0.25, 0.1)
_RETRY_FACTORS_HEAVY = (1.0, 0.35)
_HEAVY_EDGE_COUNT = 24

# Minimum inner radius, as a fraction of the outer radius. Keeps the inner
# corners rounded even when `outer - wall` would be zero or very small
# (outer radius <= wall): in that case the wall is slightly thicker at the
# corners, but the cavity is never left square.
INNER_MIN_RATIO = 0.5


_SAMPLES = (0.0, 0.25, 0.5, 0.75, 1.0)


def _select_fillet_edges(
    solid: Part,
    corners: tuple,
    bottom_z: float,
    top_z: float | None = None,
) -> list:
    """Edges to fillet on an extruded profile, identified by position (not
    by index): vertical edges located at a real corner of the profile,
    plus the horizontal edges of the bottom face (and of the top face when
    `top_z` is given).

    Edges are classified by sampling points ON the curve, not through
    bounding boxes: the bounding box of a curved edge is not reliable once
    the shape has a triangulation attached (it can come out much larger)."""
    edges = []

    for edge in solid.edges():
        try:
            pts = [edge.position_at(t) for t in _SAMPLES]
        except Exception:
            continue

        first = pts[0]
        same_xy = all(
            abs(p.X - first.X) < _POSITION_TOL and abs(p.Y - first.Y) < _POSITION_TOL
            for p in pts
        )
        zs = [p.Z for p in pts]
        z_span = max(zs) - min(zs)

        if same_xy and z_span > _POSITION_TOL:
            # vertical edge: only if it sits on a real corner
            for cx, cy in corners:
                if abs(first.X - cx) < 1e-2 and abs(first.Y - cy) < 1e-2:
                    edges.append(edge)
                    break
        elif z_span < _POSITION_TOL:
            z = zs[0]
            if abs(z - bottom_z) < _POSITION_TOL or (
                top_z is not None and abs(z - top_z) < _POSITION_TOL
            ):
                edges.append(edge)

    return edges


def _fillet_with_retry(solid: Part, edges: list, radius: float, label: str) -> tuple[Part, float]:
    """Tries `radius`, then progressively smaller radii. Returns
    (solid, applied_radius); applied_radius is 0 if nothing worked."""
    if radius <= 0:
        return solid, 0.0

    if not edges:
        log.warning("%s fillet not applied: no edges found.", label)
        return solid, 0.0

    # Every failed attempt on a complex contour (text, SVG) costs seconds:
    # try fewer radii there.
    factors = _RETRY_FACTORS if len(edges) <= _HEAVY_EDGE_COUNT else _RETRY_FACTORS_HEAVY

    last_error = None
    for factor in factors:
        r = radius * factor
        try:
            return solid.fillet(r, edges), r
        except Exception as e:
            last_error = e

    log.warning("%s fillet not applied (no radius worked): %s", label, last_error)
    return solid, 0.0


def _max_inner_radius(dims: BoxDimensions) -> float:
    return 0.9 * min(dims.inner_width / 2, dims.inner_depth / 2, dims.inner_height)


def _inner_radius_for(dims: BoxDimensions, outer_r: float) -> float:
    """Inner radius that goes with a given outer radius."""
    if outer_r <= 0 or not dims.has_valid_cavity:
        return 0.0
    inner_r = max(outer_r - dims.wall, outer_r * INNER_MIN_RATIO)
    return min(inner_r, _max_inner_radius(dims))


def compute_fillet_radii(dims: BoxDimensions, requested_radius: float) -> tuple[float, float]:
    """Computes (effective_outer_radius, effective_inner_radius), already
    clamped to a geometrically safe value.

    `requested_radius` is the OUTER radius. With a cavity, the inner radius
    is `max(outer - wall, outer * INNER_MIN_RATIO)`: concentric corners
    (uniform wall thickness) whenever possible, and never less than a
    fraction of the outer radius, so the inner corners always stay rounded.

    The outer radius is limited by the box size (half of the smaller side,
    and the height) and, when there is a cavity, by what the inner radius
    can safely be. Used both for the actual fillet and to show the
    actually applied values in the UI.
    """

    if requested_radius <= 0:
        return 0.0, 0.0

    safety = 0.9
    half_min_side = min(dims.bbox_width, dims.bbox_depth) / 2

    if not dims.has_valid_cavity:
        # Closed prism: top AND bottom edges are rounded, so the radius can
        # be at most half of the height (and of the smallest side).
        max_closed = safety * min(half_min_side, dims.height / 2)
        return max(0.0, min(requested_radius, max_closed)), 0.0

    max_outer = safety * min(half_min_side, dims.height)
    max_inner = _max_inner_radius(dims)
    # inner = max(outer - wall, outer * ratio) must not exceed max_inner
    max_outer = min(max_outer, dims.wall + max_inner, max_inner / INNER_MIN_RATIO)

    outer_r = max(0.0, min(requested_radius, max_outer))
    return outer_r, _inner_radius_for(dims, outer_r)


def build_filleted_box(dims: BoxDimensions, requested_radius: float = 0.0) -> FilletOutcome:
    """Builds the box (hollow when the cavity is valid) with rounded corners.

    Returns a FilletOutcome: the resulting part, one flag per group telling
    whether the fillet was really applied (a group that needs no fillet
    counts as applied) and the radii actually used. This lets the caller
    report a failure or a reduced radius instead of claiming a radius that
    is not there.
    """
    outer_target, _ = compute_fillet_radii(dims, requested_radius)
    profiles = dims.profiles
    bottom_z = -dims.height / 2

    outer = _make_outer_solid(dims)

    if not dims.has_valid_cavity:
        # Closed prism: every perimeter edge is rounded.
        if outer_target <= 0:
            return FilletOutcome(outer, True, True)
        edges = _select_fillet_edges(
            outer, profiles.outer_corners, bottom_z, top_z=dims.height / 2
        )
        outer, r = _fillet_with_retry(outer, edges, outer_target, "Closed prism")
        return FilletOutcome(outer, r > 0, True, r, 0.0)

    outer_r = 0.0
    if outer_target > 0:
        edges = _select_fillet_edges(outer, profiles.outer_corners, bottom_z)
        outer, outer_r = _fillet_with_retry(outer, edges, outer_target, "Outer")
    outer_ok = outer_target <= 0 or outer_r > 0

    cutter = _make_cavity_cutter(dims)
    inner_target = _inner_radius_for(dims, outer_r if outer_r > 0 else outer_target)
    inner_r = 0.0
    if inner_target > 0:
        floor_z = bottom_z + dims.wall
        edges = _select_fillet_edges(cutter, profiles.inner_corners, floor_z)
        cutter, inner_r = _fillet_with_retry(cutter, edges, inner_target, "Inner")
    inner_ok = inner_target <= 0 or inner_r > 0

    return FilletOutcome(outer - cutter, outer_ok, inner_ok, outer_r, inner_r)
