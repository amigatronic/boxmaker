import sys

import numpy as np
import pyvista as pv

from build123d import (
    Box,
    Part,
    Pos,
    export_stl,
)

from PySide6 import QtWidgets, QtCore, QtGui
from pyvistaqt import QtInteractor


def build_box(w, h, d, wall):
    outer = Box(w, h, d)

    if wall <= 0:
        return outer

    iw = w - 2 * wall
    ih = h - 2 * wall
    idh = d - wall

    if iw <= 0 or ih <= 0 or idh <= 0:
        return outer

    inner = Pos(0, 0, wall / 2) * Box(iw, ih, idh)

    return outer - inner


def apply_preview_clip(part, axis, pos_fraction, w, h, d):
    if axis == "Nessuna":
        return part

    pos_fraction = max(0.0, min(1.0, pos_fraction))

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


def vertex_to_tuple(v):
    # build123d Vector exposes .X, .Y, .Z as properties
    if hasattr(v, "X"):
        return float(v.X), float(v.Y), float(v.Z)

    if hasattr(v, "to_tuple"):
        t = v.to_tuple()
        return float(t[0]), float(t[1]), float(t[2])

    return float(v[0]), float(v[1]), float(v[2])


def build123d_to_pv_mesh(part, tolerance=0.1):
    verts, tris = part.tessellate(tolerance)

    points = np.array(
        [vertex_to_tuple(v) for v in verts],
        dtype=float
    )

    faces = np.array(
        [
            [3, int(t[0]), int(t[1]), int(t[2])]
            for t in tris
        ],
        dtype=np.int64
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
    # which guarantee the "flat" effect), recompute without auto_orient.
    n = np.asarray(mesh.point_data["Normals"], dtype=float)
    if n.size and np.allclose(n, n[0], atol=1e-8):
        mesh = mesh.compute_normals(
            cell_normals=True,
            point_normals=True,
            consistent_normals=True,
            auto_orient_normals=False,
        )

    return mesh


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Box Maker offline")
        self.resize(1200, 700)

        self._full_result = None
        self._rt_ok = None
        self._camera_initialized = False
        self._warmup_done = False

        self.box_color = QtGui.QColor("#bf7f00")
        self.edge_color = QtGui.QColor(0, 0, 0)

        central = QtWidgets.QWidget()
        root_layout = QtWidgets.QHBoxLayout(central)

        controls = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(controls)
        form.setContentsMargins(8, 8, 8, 8)

        self.slider_w = self.make_slider(20, 200, 80)
        self.slider_h = self.make_slider(20, 200, 50)
        self.slider_d = self.make_slider(10, 200, 20)
        self.slider_wall = self.make_slider(0, 20, 2)

        form.addRow("Larghezza", self.slider_w)
        form.addRow("Profondità", self.slider_h)
        form.addRow("Altezza", self.slider_d)
        form.addRow("Spessore parete", self.slider_wall)

        self.clip_axis = QtWidgets.QComboBox()
        self.clip_axis.addItems(["Nessuna", "X", "Y", "Z"])

        self.clip_pos = self.make_slider(0, 100, 50)

        form.addRow("Sezione", self.clip_axis)
        form.addRow("Pos. sezione", self.clip_pos)

        self.chk_edges = QtWidgets.QCheckBox("Mostra spigoli")
        self.chk_edges.setChecked(False)

        self.slider_line = self.make_slider(1, 50, 10)

        form.addRow(self.chk_edges)
        form.addRow("Spessore spigoli", self.slider_line)

        self.chk_smooth = QtWidgets.QCheckBox("Ombreggiatura liscia")
        self.chk_smooth.setChecked(True)
        form.addRow(self.chk_smooth)

        self.chk_realistic = QtWidgets.QCheckBox("Rendering realistico (ombre)")
        self.chk_realistic.setChecked(True)
        form.addRow(self.chk_realistic)

        self.btn_box_color = QtWidgets.QPushButton("Colore box...")
        self.btn_box_color.clicked.connect(self.pick_box_color)
        form.addRow(self.btn_box_color)

        self.btn_edge_color = QtWidgets.QPushButton("Colore spigoli...")
        self.btn_edge_color.clicked.connect(self.pick_edge_color)
        form.addRow(self.btn_edge_color)

        btn_view = QtWidgets.QPushButton("Reset vista")
        btn_view.clicked.connect(self.reset_view)
        form.addRow(btn_view)

        export_button = QtWidgets.QPushButton("Esporta STL")
        export_button.clicked.connect(self.export_stl)
        form.addRow(export_button)

        self.plotter = QtInteractor(self)
        self.plotter.set_background("#cfd3d8", top="#ffffff")

        self.setup_lights()

        root_layout.addWidget(controls)
        root_layout.addWidget(self.plotter, 1)

        self.setCentralWidget(central)

        self.timer = QtCore.QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.update_model)

        self.slider_w.valueChanged.connect(self.schedule_update)
        self.slider_h.valueChanged.connect(self.schedule_update)
        self.slider_d.valueChanged.connect(self.schedule_update)
        self.slider_wall.valueChanged.connect(self.schedule_update)
        self.clip_pos.valueChanged.connect(self.schedule_update)
        self.slider_line.valueChanged.connect(self.schedule_update)

        self.clip_axis.currentIndexChanged.connect(self.schedule_update)
        self.chk_edges.stateChanged.connect(self.schedule_update)
        self.chk_smooth.stateChanged.connect(self.schedule_update)
        self.chk_realistic.stateChanged.connect(self.schedule_update)

        self.update_model()

        QtCore.QTimer.singleShot(150, self._warmup_shadows)

    def make_slider(self, min_val, max_val, initial):
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setMinimum(min_val)
        slider.setMaximum(max_val)
        slider.setValue(initial)
        return slider

    def setup_lights(self):
        self.light_head = None
        self.light_key = None
        self.light_fill = None

        try:
            self.light_head = pv.Light(light_type="headlight", intensity=0.5)
            self.plotter.add_light(self.light_head)

            self.light_key = pv.Light(
                position=(300, 300, 600),
                focal_point=(0, 0, 0),
                intensity=0.9,
            )
            self.light_key.positional = True
            self.light_key.cone_angle = 180
            self.plotter.add_light(self.light_key)

            self.light_fill = pv.Light(
                position=(-300, -300, 300),
                focal_point=(0, 0, 0),
                intensity=0.25,
            )
            self.light_fill.positional = True
            self.light_fill.cone_angle = 180
            self.plotter.add_light(self.light_fill)
        except Exception as e:
            print("Lights not available, using default lighting:", e)

    def try_enable_ray_tracing(self):
        if self.light_head is not None:
            self.light_head.intensity = 0.0

        if self._rt_ok is False:
            return

        try:
            self.plotter.enable_shadows()
            self._rt_ok = True
        except Exception as e:
            self._rt_ok = False
            print("Shadows not available, using advanced shading only:", e)

    def disable_ray_tracing(self):
        if self.light_head is not None:
            self.light_head.intensity = 0.5

        try:
            self.plotter.disable_shadows()
        except Exception:
            pass

    def _warmup_shadows(self):
        if self._warmup_done:
            return
        self._warmup_done = True

        if not self.chk_realistic.isChecked():
            return

        self.chk_realistic.blockSignals(True)
        try:
            self.chk_realistic.setChecked(False)
            self.update_model()

            self.chk_realistic.setChecked(True)
            self.update_model()
        finally:
            self.chk_realistic.blockSignals(False)

    def reset_view(self):
        self.plotter.reset_camera()
        try:
            self.plotter.render()
        except Exception:
            pass

    def closeEvent(self, event):
        try:
            import vtk
            vtk.vtkObject.GlobalWarningDisplayOff()
        except Exception:
            pass

        try:
            self.plotter.disable_shadows()
            self.plotter.renderer.SetPass(None)
            self.plotter.render()
        except Exception:
            pass

        try:
            self.plotter.close()
        except Exception:
            pass

        event.accept()

    def schedule_update(self, *_):
        self.timer.start(120)

    def pick_box_color(self):
        color = QtWidgets.QColorDialog.getColor(
            self.box_color, self, "Colore box"
        )
        if color.isValid():
            self.box_color = color
            self.update_model()

    def pick_edge_color(self):
        color = QtWidgets.QColorDialog.getColor(
            self.edge_color, self, "Colore spigoli"
        )
        if color.isValid():
            self.edge_color = color
            self.update_model()

    def update_model(self):
        try:
            w = self.slider_w.value()
            h = self.slider_h.value()
            d = self.slider_d.value()
            wall = self.slider_wall.value()

            full_result = build_box(w, h, d, wall)
            self._full_result = full_result

            preview_result = full_result

            axis = self.clip_axis.currentText()

            if axis != "Nessuna":
                pos = self.clip_pos.value() / 100.0
                try:
                    preview_result = apply_preview_clip(
                        full_result, axis, pos, w, h, d
                    )
                except Exception as clip_error:
                    print("Error in the section, using the whole model:", clip_error)
                    preview_result = full_result

            mesh = build123d_to_pv_mesh(preview_result)

            if mesh.n_points == 0 or mesh.n_cells == 0:
                try:
                    self.plotter.remove_actor("model")
                except Exception:
                    pass
                return

            base = (
                self.box_color.redF(),
                self.box_color.greenF(),
                self.box_color.blueF()
            )

            edge_col = (
                self.edge_color.redF(),
                self.edge_color.greenF(),
                self.edge_color.blueF()
            )

            line_w = self.slider_line.value() / 10.0

            realistic = self.chk_realistic.isChecked()

            if realistic:
                self.try_enable_ray_tracing()
            else:
                self.disable_ray_tracing()

            # No clear(): the actor is replaced in-place via name
            # and the lights stay active.
            if realistic:
                self.plotter.add_mesh(
                    mesh,
                    color=base,
                    smooth_shading=self.chk_smooth.isChecked(),
                    show_edges=self.chk_edges.isChecked(),
                    edge_color=edge_col,
                    line_width=line_w,
                    ambient=0.1,
                    diffuse=0.85,
                    specular=0.5,
                    specular_power=40,
                    name="model",
                )
            else:
                self.plotter.add_mesh(
                    mesh,
                    color=base,
                    smooth_shading=self.chk_smooth.isChecked(),
                    show_edges=self.chk_edges.isChecked(),
                    edge_color=edge_col,
                    line_width=line_w,
                    ambient=0.15,
                    diffuse=0.9,
                    specular=0.2,
                    specular_power=30,
                    name="model",
                )

            if not self._camera_initialized:
                self.plotter.reset_camera()
                self._camera_initialized = True

        except Exception as e:
            print("Error while updating the model:", e)

    def export_stl(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Salva STL", "box.stl", "STL (*.stl)"
        )

        if not path:
            return

        if not path.lower().endswith(".stl"):
            path += ".stl"

        if self._full_result is None:
            return

        try:
            export_stl(self._full_result, path)
            print("STL exported:", path)
        except Exception as e:
            print("Error during STL export:", e)


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
