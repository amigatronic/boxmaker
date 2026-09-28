"""
GPU/OpenGL diagnostics used by VTK.
Run with:  python gpu_check.py

If under "Renderer" you see "Intel" instead of the name of your dedicated
card (NVIDIA/AMD), that's the problem: Windows is launching python.exe on
the integrated GPU instead of the dedicated one.
"""

import pyvista as pv

pl = pv.Plotter(off_screen=True)
pl.add_mesh(pv.Sphere())
pl.show(auto_close=False)

info = pl.render_window.ReportCapabilities()
print(info)

pl.close()
