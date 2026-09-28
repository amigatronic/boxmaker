"""
3D viewport (PyVista) management: lights, shadows, mesh style, dimension
overlay. Knows nothing about build123d or Qt beyond the plotter widget
passed to it from the outside.

If the PROBLEM is visual (shadows not showing up, wrong colors, mesh
disappearing), look here.
"""

from dataclasses import dataclass

import pyvista as pv


@dataclass
class RenderStyle:
    base_color: tuple
    edge_color: tuple
    show_edges: bool
    smooth_shading: bool
    line_width: float
    realistic: bool


class SceneLights:
    """The three lights used in the viewport (headlight + key + fill)."""

    def __init__(self, plotter):
        self.plotter = plotter
        self.head = None
        self.key = None
        self.fill = None
        self._setup()

    def _setup(self):
        try:
            self.head = pv.Light(light_type="headlight", intensity=0.5)
            self.plotter.add_light(self.head)

            self.key = pv.Light(
                position=(300, 300, 600), focal_point=(0, 0, 0), intensity=0.9
            )
            self.key.positional = True
            self.key.cone_angle = 180
            self.plotter.add_light(self.key)

            self.fill = pv.Light(
                position=(-300, -300, 300), focal_point=(0, 0, 0), intensity=0.25
            )
            self.fill.positional = True
            self.fill.cone_angle = 180
            self.plotter.add_light(self.fill)
        except Exception as e:
            print("Lights not available, using default lighting:", e)

    def set_headlight_intensity(self, value: float):
        if self.head is not None:
            self.head.intensity = value


class ShadowController:
    """Enables/disables ray-traced shadows, with a silent fallback if not supported."""

    def __init__(self, plotter, lights: SceneLights):
        self.plotter = plotter
        self.lights = lights
        self._supported = None  # None = not yet tested on this system

    def enable(self):
        self.lights.set_headlight_intensity(0.0)

        if self._supported is False:
            return

        try:
            self.plotter.enable_shadows()
            self._supported = True
        except Exception as e:
            self._supported = False
            print("Shadows not available, using advanced shading only:", e)

    def disable(self):
        self.lights.set_headlight_intensity(0.5)
        try:
            self.plotter.disable_shadows()
        except Exception:
            pass


def render_mesh(plotter, mesh, style: RenderStyle, actor_name: str = "model"):
    """Replaces (in-place, without clear) the mesh actor in the plotter."""
    common_kwargs = dict(
        color=style.base_color,
        smooth_shading=style.smooth_shading,
        show_edges=style.show_edges,
        edge_color=style.edge_color,
        line_width=style.line_width,
        name=actor_name,
    )

    if style.realistic:
        plotter.add_mesh(
            mesh, ambient=0.1, diffuse=0.85, specular=0.5, specular_power=40,
            **common_kwargs,
        )
    else:
        plotter.add_mesh(
            mesh, ambient=0.15, diffuse=0.9, specular=0.2, specular_power=30,
            **common_kwargs,
        )


def update_dimension_overlay(plotter, text: str, name: str = "dims_overlay"):
    """Writes the current dimensions in the corner of the 3D viewport (also useful in screenshots)."""
    plotter.add_text(
        text,
        position="upper_left",
        font_size=10,
        color="black",
        name=name,
    )
