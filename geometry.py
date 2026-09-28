"""
Parametric modeling of the box.

No Qt or PyVista dependency in here: only build123d and measurement
calculations. This is the module to check if the PROBLEM is in the
SHAPE of the solid (wrong cavity, section that cuts badly, etc.).
"""

from dataclasses import dataclass

from build123d import Box, Part, Pos


@dataclass(frozen=True)
class BoxDimensions:
    """Box measurements, in millimeters.

    width  -> Width (X axis)
    depth  -> Depth (Y axis)
    height -> Height (Z axis)
    wall   -> wall thickness
    """

    width: float
    depth: float
    height: float
    wall: float

    @property
    def inner_width(self) -> float:
        return self.width - 2 * self.wall

    @property
    def inner_depth(self) -> float:
        return self.depth - 2 * self.wall

    @property
    def inner_height(self) -> float:
        # the cavity starts half a wall thickness above the bottom and reaches the rim
        return self.height - self.wall

    @property
    def has_valid_cavity(self) -> bool:
        return (
            self.wall > 0
            and self.inner_width > 0
            and self.inner_depth > 0
            and self.inner_height > 0
        )

    def describe(self) -> str:
        """Human-readable text with the measurements, for the UI panel and 3D overlay."""
        lines = [
            f"Outer (WxDxH): {self.width:.2f} x {self.depth:.2f} x {self.height:.2f} mm",
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


def build_box(dims: BoxDimensions) -> Part:
    """Creates the solid or hollow shape according to `dims`. Also used for STL export."""
    outer = Box(dims.width, dims.depth, dims.height)

    if not dims.has_valid_cavity:
        return outer

    inner = Pos(0, 0, dims.wall / 2) * Box(
        dims.inner_width, dims.inner_depth, dims.inner_height
    )

    return outer - inner


def apply_preview_clip(part: Part, axis: str, pos_fraction: float, dims: BoxDimensions) -> Part:
    """Section (cut-away) ONLY for the 3D preview. Never touches the model to be exported.

    `axis` uses stable, UI-language-independent codes:
    "none", "X", "Y", "Z" (not the translated text shown in the combo box)."""
    if axis == "none":
        return part

    pos_fraction = max(0.0, min(1.0, pos_fraction))

    w, h, d = dims.width, dims.depth, dims.height
    far = max(w, h, d) * 5 + 100
    eps = 0.01

    if axis == "X":
        x_cut = -w / 2 + w * pos_fraction - eps
        cutter = Pos(x_cut + far / 2, 0, 0) * Box(far, h * 3 + 100, d * 3 + 100)
        return part - cutter

    if axis == "Y":
        y_cut = -h / 2 + h * pos_fraction - eps
        cutter = Pos(0, y_cut + far / 2, 0) * Box(w * 3 + 100, far, d * 3 + 100)
        return part - cutter

    if axis == "Z":
        z_cut = -d / 2 + d * pos_fraction - eps
        cutter = Pos(0, 0, z_cut + far / 2) * Box(w * 3 + 100, h * 3 + 100, far)
        return part - cutter

    return part



# ----------------------------------------------------------------------
# Corner rounding (fillet).
#
# Philosophy: we do NOT round "every edge there is". We only round:
#   - the 4 vertical edges (full height) of the outer shell and of the
#     opening (inner) rim - the point that hits when dropped on its side;
#   - the 4 horizontal floor-wall edges, outer (base perimeter) and inner
#     (where the cavity floor meets the walls) - the point that hits when
#     dropped on its base.
# For each corner, the vertical and horizontal edges are passed to the
# SAME fillet() call, so that OCCT generates a true 3D blend at the
# shared vertex instead of two disconnected sequential operations.
# Top edges (opening rim) are deliberately left sharp: they are not a
# typical impact point and rounding them would compromise the edge a
# lid might rest on.
# ----------------------------------------------------------------------

_POSITION_TOL = 1e-3  # mm, tolerance for recognizing a "corner" edge


def _close(a: float, b: float, tol: float = _POSITION_TOL) -> bool:
    return abs(a - b) < tol


def _edge_direction(edge):
    try:
        delta = edge.end_point() - edge.start_point()
    except Exception:
        return None

    if delta.length < 1e-6:
        return None

    return delta.normalized()


def _is_vertical_edge(edge) -> bool:
    """True if the edge is a straight segment parallel to the Z axis."""
    direction = _edge_direction(edge)
    return direction is not None and abs(abs(direction.Z) - 1.0) < 1e-3


def _is_horizontal_edge(edge) -> bool:
    """True if the edge is a straight segment lying on a horizontal plane."""
    direction = _edge_direction(edge)
    return direction is not None and abs(direction.Z) < 1e-3


def _classify_corner_edges(part: Part, dims: BoxDimensions):
    """Returns (outer_edges, inner_edges) to be filleted for each corner:
    vertical + horizontal floor-wall, identified by position (not by
    index), so they remain valid for any combination of measurements and
    on every recomputation after a previous fillet."""

    outer_x, outer_y = dims.width / 2, dims.depth / 2
    bottom_z = -dims.height / 2

    if dims.has_valid_cavity:
        inner_x, inner_y = dims.inner_width / 2, dims.inner_depth / 2
        floor_z = dims.wall - dims.height / 2
    else:
        inner_x = inner_y = floor_z = None

    outer_edges = []
    inner_edges = []

    for edge in part.edges():
        mid = edge.center()
        x, y, z = abs(mid.X), abs(mid.Y), mid.Z

        if _is_vertical_edge(edge):
            if _close(x, outer_x) and _close(y, outer_y):
                outer_edges.append(edge)
            elif inner_x is not None and _close(x, inner_x) and _close(y, inner_y):
                inner_edges.append(edge)
            continue

        if _is_horizontal_edge(edge):
            # outer floor-wall edge: runs along one side of the outer
            # rectangle, at base height
            if _close(z, bottom_z) and (
                (_close(x, 0.0) and _close(y, outer_y))
                or (_close(y, 0.0) and _close(x, outer_x))
            ):
                outer_edges.append(edge)
                continue

            # inner floor-wall edge of the cavity
            if floor_z is not None and _close(z, floor_z) and (
                (_close(x, 0.0) and _close(y, inner_y))
                or (_close(y, 0.0) and _close(x, inner_x))
            ):
                inner_edges.append(edge)
                continue

    return outer_edges, inner_edges


def compute_fillet_radii(dims: BoxDimensions, requested_radius: float) -> tuple[float, float]:
    """Computes (effective_outer_radius, effective_inner_radius), already
    clamped to a geometrically safe value: it must not make the walls too
    thin, nor exceed the height available for the floor-wall blend. Used
    both for the actual fillet and to show the actually applied value in
    the UI."""

    if requested_radius <= 0:
        return 0.0, 0.0

    if dims.has_valid_cavity:
        max_outer = min(dims.wall, dims.height) * 0.9
        max_inner = min(
            min(dims.inner_width, dims.inner_depth) / 2,
            dims.wall,
            dims.inner_height,
        ) * 0.9
    else:
        max_outer = min(dims.width, dims.depth, dims.height) / 2 * 0.9
        max_inner = 0.0

    outer_r = max(0.0, min(requested_radius, max_outer))
    inner_r = max(0.0, min(requested_radius, max_inner))

    return outer_r, inner_r


def apply_edge_fillets(part: Part, dims: BoxDimensions, requested_radius: float) -> Part:
    """Applies the fillet to the 4 outer corners (vertical + base) and the
    4 inner corners (vertical + cavity floor).

    The two groups are filleted in two separate passes because they
    require different radii (the outer one is limited by wall thickness
    and height, the inner one also by the cavity size). After the first
    fillet the solid's topology changes, so the edges for the second pass
    are reselected on the updated result, not reused from the original
    solid.
    """

    outer_r, inner_r = compute_fillet_radii(dims, requested_radius)

    if outer_r <= 0 and inner_r <= 0:
        return part

    result = part

    if outer_r > 0:
        outer_edges, _ = _classify_corner_edges(result, dims)
        if outer_edges:
            try:
                result = result.fillet(outer_r, outer_edges)
            except Exception as e:
                print("Outer fillet not applied (radius not valid for this geometry):", e)

    if inner_r > 0:
        _, inner_edges = _classify_corner_edges(result, dims)
        if inner_edges:
            try:
                result = result.fillet(inner_r, inner_edges)
            except Exception as e:
                print("Inner fillet not applied (radius not valid for this geometry):", e)

    return result
