"""
Spatial references in the 3D viewport: orientation compass, XYZ axes
anchored to the part, and dimension lines (W/D/H) detached from the model.

Only drawing/annotation here: no part geometry logic (that stays in
geometry.py).

Note: the labels (add_point_labels) use always_visible=False when
possible, so they respect the depth test and correctly disappear behind
geometry that occludes them. It becomes True when antialiasing
(SSAA/FXAA) is active: that render pass seems to interfere with the
labels' visibility test (same kind of conflict already seen between FXAA
and shadows) - better always visible than invisible.
"""

import numpy as np
import pyvista as pv

_AXIS_COLORS = {"x": "red", "y": "green", "z": "blue"}


def setup_orientation_widget(plotter):
    """Small fixed XYZ compass in the corner of the viewport, to orient
    yourself with the camera. Must be called only once at startup, not
    on every update — unlike the axes/dimensions below, it doesn't
    depend on the dimensions."""
    try:
        plotter.add_axes(interactive=False)
    except Exception as e:
        print("Orientation widget not available:", e)

    try:
        # Interactive navigation cube (FreeCAD/SolidWorks/Fusion style):
        # click on a face/edge/vertex to snap the camera to that standard
        # view. Stays usable with the mouse at any time, independent of
        # the rest of the interaction (rotate, pan, zoom keep working
        # normally on the rest of the viewport).
        plotter.add_camera_orientation_widget()
    except Exception as e:
        print("Navigation cube not available:", e)


def _dimension_line_mesh(p1, p2, tick_direction, tick_len):
    """Technical-drawing style polyline: main segment + a small
    perpendicular tick at each end."""
    p1 = np.array(p1, dtype=float)
    p2 = np.array(p2, dtype=float)
    tick = np.array(tick_direction, dtype=float) * tick_len

    points = np.array([
        p1 - tick, p1 + tick,  # starting tick
        p1, p2,                 # main segment
        p2 - tick, p2 + tick,  # ending tick
    ])

    lines = np.hstack([[2, 0, 1], [2, 2, 3], [2, 4, 5]])

    mesh = pv.PolyData(points)
    mesh.lines = lines
    return mesh


def _actor_names(prefix: str) -> list[str]:
    return [
        f"{prefix}_axis_x", f"{prefix}_axis_y", f"{prefix}_axis_z",
        f"{prefix}_axis_x_label", f"{prefix}_axis_y_label", f"{prefix}_axis_z_label",
        f"{prefix}_dim_w", f"{prefix}_dim_d", f"{prefix}_dim_h",
        f"{prefix}_dim_w_label", f"{prefix}_dim_d_label", f"{prefix}_dim_h_label",
    ]


def update_reference_annotations(
    plotter, width: float, depth: float, height: float,
    visible: bool = True, prefix: str = "ref",
    label_always_visible: bool = False,
):
    """Draws (or removes) XYZ axes at the part's origin + 3 dimension
    lines (width/depth/height) detached from the model, with ticks and
    numeric value. Each call replaces the previous actors in-place via
    `name`, consistent with the rest of the rendering."""

    names = _actor_names(prefix)

    if not visible:
        for n in names:
            try:
                plotter.remove_actor(n)
            except Exception:
                pass
        return

    max_dim = max(width, depth, height)
    axis_len = max_dim * 0.35

    axes_spec = [
        ("x", (1.0, 0.0, 0.0), names[0], names[3]),
        ("y", (0.0, 1.0, 0.0), names[1], names[4]),
        ("z", (0.0, 0.0, 1.0), names[2], names[5]),
    ]

    for label_txt, unit_dir, arrow_name, label_name in axes_spec:
        color = _AXIS_COLORS[label_txt]
        arrow = pv.Arrow(start=(0, 0, 0), direction=unit_dir, scale=axis_len)
        plotter.add_mesh(arrow, color=color, name=arrow_name)

        tip = tuple(c * axis_len for c in unit_dir)
        plotter.add_point_labels(
            [tip], [label_txt.upper()],
            font_size=14, text_color=color, bold=True,
            always_visible=label_always_visible, name=label_name,
        )

    offset = max_dim * 0.18
    tick_len = max_dim * 0.02

    # Width dimension (X): detached in front of/below the part
    w_p1 = (-width / 2, -depth / 2 - offset, -height / 2)
    w_p2 = (width / 2, -depth / 2 - offset, -height / 2)
    plotter.add_mesh(
        _dimension_line_mesh(w_p1, w_p2, (0, 1, 0), tick_len),
        color="black", line_width=1.5, name=names[6],
    )
    plotter.add_point_labels(
        [((w_p1[0] + w_p2[0]) / 2, w_p1[1], w_p1[2])], [f"{width:.2f} mm"],
        font_size=11, text_color="black", always_visible=label_always_visible, name=names[9],
    )

    # Depth dimension (Y): detached at the side of the part
    d_p1 = (-width / 2 - offset, -depth / 2, -height / 2)
    d_p2 = (-width / 2 - offset, depth / 2, -height / 2)
    plotter.add_mesh(
        _dimension_line_mesh(d_p1, d_p2, (1, 0, 0), tick_len),
        color="black", line_width=1.5, name=names[7],
    )
    plotter.add_point_labels(
        [(d_p1[0], (d_p1[1] + d_p2[1]) / 2, d_p1[2])], [f"{depth:.2f} mm"],
        font_size=11, text_color="black", always_visible=label_always_visible, name=names[10],
    )

    # Height dimension (Z): detached in the corner
    h_p1 = (-width / 2 - offset, -depth / 2 - offset, -height / 2)
    h_p2 = (-width / 2 - offset, -depth / 2 - offset, height / 2)
    plotter.add_mesh(
        _dimension_line_mesh(h_p1, h_p2, (1, 0, 0), tick_len),
        color="black", line_width=1.5, name=names[8],
    )
    plotter.add_point_labels(
        [(h_p1[0], h_p1[1], (h_p1[2] + h_p2[2]) / 2)], [f"{height:.2f} mm"],
        font_size=11, text_color="black", always_visible=label_always_visible, name=names[11],
    )
