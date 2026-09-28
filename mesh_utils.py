"""
Conversion from build123d shape to PyVista mesh.

If the model looks "flat", with wrong normals, or has holes in the
surface, the problem is almost always here, not in the build123d
geometry itself.
"""

import numpy as np
import pyvista as pv


def _vertex_to_tuple(v):
    # build123d Vector exposes .X, .Y, .Z as properties
    if hasattr(v, "X"):
        return float(v.X), float(v.Y), float(v.Z)

    if hasattr(v, "to_tuple"):
        t = v.to_tuple()
        return float(t[0]), float(t[1]), float(t[2])

    return float(v[0]), float(v[1]), float(v[2])


def build123d_to_pv_mesh(part, tolerance: float = 0.1) -> pv.PolyData:
    """Tessellates `part` (build123d Part/Compound) and returns a PyVista mesh."""
    verts, tris = part.tessellate(tolerance)

    points = np.array([_vertex_to_tuple(v) for v in verts], dtype=float)

    faces = np.array(
        [[3, int(t[0]), int(t[1]), int(t[2])] for t in tris],
        dtype=np.int64,
    ).ravel()

    mesh = pv.PolyData(points)
    mesh.faces = faces

    if mesh.n_cells == 0:
        return mesh

    mesh = mesh.triangulate()
    mesh = mesh.compute_normals(
        cell_normals=True,
        point_normals=True,
        consistent_normals=True,
        auto_orient_normals=True,
    )

    # SELF-DIAGNOSIS: if the normals are degenerate (all equal,
    # guaranteed "flat" effect), recompute without auto_orient.
    normals = np.asarray(mesh.point_data["Normals"], dtype=float)
    if normals.size and np.allclose(normals, normals[0], atol=1e-8):
        mesh = mesh.compute_normals(
            cell_normals=True,
            point_normals=True,
            consistent_normals=True,
            auto_orient_normals=False,
        )

    return mesh
