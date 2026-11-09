"""
Conversion from build123d shape to PyVista mesh.

If the model looks "flat", with wrong normals, or has holes in the
surface, the problem is almost always here, not in the build123d
geometry itself.
"""

import numpy as np
import pyvista as pv


def build123d_to_pv_mesh(
    part, tolerance: float = 0.1, angular_tolerance: float = 0.25
) -> pv.PolyData:
    """Tessellates `part` (build123d Part/Compound) and returns a PyVista mesh.

    This mesh is only for the preview. The angular tolerance (radians) is
    looser than the default (0.1): curved faces get 10-20 times fewer
    triangles, which makes complex shapes (text, SVG) much faster to
    display, and smooth shading hides the difference. The STL export does
    not use this mesh."""
    verts, tris = part.tessellate(tolerance, angular_tolerance)

    if not verts or not tris:
        return pv.PolyData()

    points = np.array([(v.X, v.Y, v.Z) for v in verts], dtype=float)
    tri_idx = np.asarray(tris, dtype=np.int64)
    faces = np.hstack([np.full((len(tri_idx), 1), 3, dtype=np.int64), tri_idx]).ravel()

    mesh = pv.PolyData(points, faces)

    # build123d tessellates face by face, so vertices on shared edges are
    # duplicated. Merging them makes the mesh connected: without this,
    # consistent_normals/auto_orient_normals work on isolated faces and
    # can flip some of them inward.
    mesh = mesh.clean(tolerance=1e-6, absolute=True)

    return mesh.compute_normals(
        cell_normals=True,
        point_normals=True,
        consistent_normals=True,
        auto_orient_normals=True,
    )
