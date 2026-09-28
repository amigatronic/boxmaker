"""
Main window: brings together UI, geometry, rendering, export,
localization and session persistence.

If the PROBLEM is "I pressed a button and nothing happens" or "the
right slider doesn't update the right thing", look here - it's the
module that connects all the others.
"""

from PySide6 import QtWidgets, QtCore, QtGui
from pyvistaqt import QtInteractor
import pyvista as pv
import time

# DEBUG: detailed diagnostics on the 3D widget rebuild (dimensions,
# timing of each phase). Set to False to silence it once resolved.
DEBUG_REBUILD = False


def _dbg(t0, msg):
    if DEBUG_REBUILD:
        print(f"[REBUILD +{time.perf_counter() - t0:7.4f}s] {msg}")


# Known VTK+Qt bug on Windows: certain NVIDIA driver + Qt-shared OpenGL
# context combinations fail to compile shaders ("Could not set shader
# program") when multisample anti-aliasing is enabled by default. It
# must be disabled BEFORE any plotter/QtInteractor is created.
pv.global_theme.multi_samples = 0

from geometry import BoxDimensions, build_box, apply_preview_clip, apply_edge_fillets, compute_fillet_radii
from mesh_utils import build123d_to_pv_mesh
from rendering import SceneLights, ShadowController, RenderStyle, render_mesh, update_dimension_overlay
from dimension_annotations import setup_orientation_widget, update_reference_annotations
from exporting import export_to_stl, export_to_step
from widgets import LabeledSlider, PrecisionSlider
from i18n import Translator, SUPPORTED_LANGUAGES, LANGUAGE_NAMES, DEFAULT_LANGUAGE
import session


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        # --- Language: must be decided BEFORE building the UI, so the
        # labels are already born translated instead of having to be recreated ---
        saved_lang = session.load_language(DEFAULT_LANGUAGE)
        self.i18n = Translator(saved_lang)

        self.resize(1200, 700)

        self._full_result = None
        self._full_dims: BoxDimensions | None = None
        self._camera_initialized = False
        self._loading_state = False  # avoids a burst of recomputations during session restore

        self.box_color = QtGui.QColor("#bf7f00")
        self.edge_color = QtGui.QColor(0, 0, 0)

        # Tracking for hot re-translation (language change without restart)
        self._row_label_widgets: list[tuple[QtWidgets.QWidget, str]] = []  # (field widget, key)
        self._self_label_widgets: list[tuple[QtWidgets.QWidget, str]] = []  # (checkbox/button, key)

        central = QtWidgets.QWidget()
        root_layout = QtWidgets.QHBoxLayout(central)

        controls = QtWidgets.QWidget()
        self.form = QtWidgets.QFormLayout(controls)
        self.form.setContentsMargins(8, 8, 8, 8)

        # --- Language selector, at the top of the panel ---
        self.lang_combo = QtWidgets.QComboBox()
        for code in SUPPORTED_LANGUAGES:
            self.lang_combo.addItem(LANGUAGE_NAMES[code], userData=code)
        self.lang_combo.setCurrentIndex(SUPPORTED_LANGUAGES.index(self.i18n.lang))
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)
        self._add_row("label_language", self.lang_combo)

        # --- Slider + spinbox for physical measurements: precision 0.01 mm (10 microns) ---
        self.slider_w = PrecisionSlider(20.0, 200.0, 80.0, decimals=2)
        self.slider_h = PrecisionSlider(20.0, 200.0, 50.0, decimals=2)
        self.slider_d = PrecisionSlider(10.0, 200.0, 20.0, decimals=2)
        self.slider_wall = PrecisionSlider(0.0, 20.0, 2.0, decimals=2)

        self._add_row("label_width", self.slider_w)
        self._add_row("label_depth", self.slider_h)
        self._add_row("label_height", self.slider_d)
        self._add_row("label_wall", self.slider_wall)

        # --- Edge rounding (fillet) ---
        self.chk_fillet = QtWidgets.QCheckBox()
        self.chk_fillet.setChecked(False)
        self._add_self_row("checkbox_fillet", self.chk_fillet)

        self.slider_fillet = PrecisionSlider(0.0, 10.0, 1.0, decimals=2)
        self.slider_fillet.setEnabled(False)
        self._add_row("label_fillet_radius", self.slider_fillet)

        # --- Dimensions summary panel ---
        self.dims_label = QtWidgets.QLabel()
        self.dims_label.setFont(QtGui.QFont("Consolas", 9))
        self.dims_label.setStyleSheet(
            "background: #f0f0f0; border: 1px solid #ccc; padding: 6px;"
        )
        self.dims_label.setWordWrap(True)
        self.form.addRow(self.dims_label)

        # --- Section: the combo uses stable CODES ("none","X","Y","Z"),
        # not the translated text, so changing language doesn't break the logic ---
        self.clip_axis = QtWidgets.QComboBox()
        self.clip_axis.addItem(self.i18n.t("clip_axis_none"), userData="none")
        self.clip_axis.addItem("X", userData="X")
        self.clip_axis.addItem("Y", userData="Y")
        self.clip_axis.addItem("Z", userData="Z")

        self.clip_pos = LabeledSlider(0, 100, 50, unit="%")

        self._add_row("label_clip_axis", self.clip_axis)
        self._add_row("label_clip_pos", self.clip_pos)

        self.chk_edges = QtWidgets.QCheckBox()
        self.chk_edges.setChecked(False)

        self.slider_line = LabeledSlider(1, 50, 10, unit="")

        self._add_self_row("checkbox_show_edges", self.chk_edges)
        self._add_row("label_edge_thickness", self.slider_line)

        self.chk_smooth = QtWidgets.QCheckBox()
        self.chk_smooth.setChecked(True)
        self._add_self_row("checkbox_smooth", self.chk_smooth)

        self.chk_realistic = QtWidgets.QCheckBox()
        # Always off by default (see note on _cycle_shadows_workaround
        # further below): turning it on at every startup would mean an
        # unrequested automatic flicker. Whoever wants it turns it on
        # knowingly.
        self.chk_realistic.setChecked(False)
        self._add_self_row("checkbox_realistic", self.chk_realistic)

        self.chk_overlay = QtWidgets.QCheckBox()
        self.chk_overlay.setChecked(True)
        self._add_self_row("checkbox_overlay", self.chk_overlay)

        self.chk_axes = QtWidgets.QCheckBox()
        self.chk_axes.setChecked(True)
        self._add_self_row("checkbox_axes", self.chk_axes)

        self.btn_box_color = QtWidgets.QPushButton()
        self.btn_box_color.clicked.connect(self.pick_box_color)
        self._add_self_row("button_box_color", self.btn_box_color)

        self.btn_edge_color = QtWidgets.QPushButton()
        self.btn_edge_color.clicked.connect(self.pick_edge_color)
        self._add_self_row("button_edge_color", self.btn_edge_color)

        self.btn_reset_view = QtWidgets.QPushButton()
        self.btn_reset_view.clicked.connect(self.reset_view)
        self._add_self_row("button_reset_view", self.btn_reset_view)

        self.btn_export = QtWidgets.QPushButton()
        self.btn_export.clicked.connect(self.export_stl)
        self._add_self_row("button_export_stl", self.btn_export)

        self.root_layout = root_layout
        self.plotter = self._create_plotter()
        # Synced IMMEDIATELY with the checkbox's real state: if it stayed
        # None, the very first update_model() would be mistaken for a mode
        # change (None != False), triggering a needless rebuild and
        # capturing a "garbage" camera position from the first plotter
        # (never rendered, default VTK camera).
        self._last_realistic = self.chk_realistic.isChecked()

        # QStackedWidget instead of adding the plotter directly to the
        # layout: every page of the stack immediately gets the correct
        # final size even if it's not the visible one - the new widget,
        # built during a mode change, is already populated at the right
        # size instead of a default that would only be corrected after
        # the swap (cause of the wrong-size flash frame).
        self.plotter_stack = QtWidgets.QStackedWidget()
        self.plotter_stack.addWidget(self.plotter)

        # Curtain: flat background-colored page, shown IMMEDIATELY as
        # soon as a rebuild is needed, so the user doesn't see the VTK
        # widget initializing (flash/glitch) - the real work happens
        # "behind", invisible, and the curtain is removed only once the
        # result is ready.
        self.curtain = QtWidgets.QWidget()
        self.curtain.setStyleSheet(
            "background: qlineargradient(x1:0, y1:1, x2:0, y2:0, "
            "stop:0 #cfd3d8, stop:1 #ffffff);"
        )
        self.plotter_stack.addWidget(self.curtain)

        root_layout.addWidget(controls)
        root_layout.addWidget(self.plotter_stack, 1)

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
        self.chk_realistic.stateChanged.connect(self._on_realistic_toggled)
        self.chk_overlay.stateChanged.connect(self.schedule_update)
        self.chk_axes.stateChanged.connect(self.schedule_update)
        self.chk_fillet.stateChanged.connect(self._on_fillet_toggled)
        self.slider_fillet.valueChanged.connect(self.schedule_update)

        self.retranslate_ui()
        self._restore_session()

        self.update_model()

    # ------------------------------------------------------------------
    # UI construction helpers: they register the widget for hot
    # re-translation, so there's no need for a second list to keep in sync.
    # ------------------------------------------------------------------
    def _add_row(self, key: str, field_widget: QtWidgets.QWidget):
        self.form.addRow(self.i18n.t(key), field_widget)
        self._row_label_widgets.append((field_widget, key))

    def _add_self_row(self, key: str, widget: QtWidgets.QWidget):
        widget.setText(self.i18n.t(key))
        self.form.addRow(widget)
        self._self_label_widgets.append((widget, key))

    def retranslate_ui(self):
        """Reapplies all texts in the current language, without restarting
        the app or losing the controls' state (values, selections, etc.)."""
        self.setWindowTitle(self.i18n.t("window_title"))

        for field_widget, key in self._row_label_widgets:
            label = self.form.labelForField(field_widget)
            if label is not None:
                label.setText(self.i18n.t(key))

        for widget, key in self._self_label_widgets:
            widget.setText(self.i18n.t(key))

        # The section combo uses stable codes: only update the displayed
        # text of the "none" entry, the index/selection doesn't change.
        self.clip_axis.setItemText(0, self.i18n.t("clip_axis_none"))

    def _on_language_changed(self, index: int):
        code = self.lang_combo.itemData(index)
        if not code or code == self.i18n.lang:
            return
        self.i18n.set_language(code)
        self.retranslate_ui()
        self.update_model()

    # ------------------------------------------------------------------
    # Session persistence: application state (sliders, checkboxes, colors)
    # + window position. Language already handled separately, before the UI.
    # ------------------------------------------------------------------
    def _collect_state(self) -> dict:
        return {
            "width": self.slider_w.value(),
            "depth": self.slider_h.value(),
            "height": self.slider_d.value(),
            "wall": self.slider_wall.value(),
            "fillet_enabled": self.chk_fillet.isChecked(),
            "fillet_radius": self.slider_fillet.value(),
            "clip_axis_index": self.clip_axis.currentIndex(),
            "clip_pos": self.clip_pos.value(),
            "show_edges": self.chk_edges.isChecked(),
            "edge_thickness": self.slider_line.value(),
            "smooth_shading": self.chk_smooth.isChecked(),
            "show_overlay": self.chk_overlay.isChecked(),
            "show_axes": self.chk_axes.isChecked(),
            "box_color": self.box_color.name(),
            "edge_color": self.edge_color.name(),
        }

    def _restore_session(self):
        self._loading_state = True
        try:
            state = session.load_state()

            self.slider_w.setValue(state.get("width", self.slider_w.value()))
            self.slider_h.setValue(state.get("depth", self.slider_h.value()))
            self.slider_d.setValue(state.get("height", self.slider_d.value()))
            self.slider_wall.setValue(state.get("wall", self.slider_wall.value()))

            self.chk_fillet.setChecked(state.get("fillet_enabled", False))
            self.slider_fillet.setValue(state.get("fillet_radius", self.slider_fillet.value()))
            self.slider_fillet.setEnabled(self.chk_fillet.isChecked())

            clip_index = state.get("clip_axis_index", 0)
            if 0 <= clip_index < self.clip_axis.count():
                self.clip_axis.setCurrentIndex(clip_index)
            self.clip_pos.setValue(state.get("clip_pos", self.clip_pos.value()))

            self.chk_edges.setChecked(state.get("show_edges", False))
            self.slider_line.setValue(state.get("edge_thickness", self.slider_line.value()))
            self.chk_smooth.setChecked(state.get("smooth_shading", True))
            # "realistic" intentionally NOT restored from the session:
            # it always starts off (see the comment on the checkbox in __init__).
            self.chk_overlay.setChecked(state.get("show_overlay", True))
            self.chk_axes.setChecked(state.get("show_axes", True))

            box_color = state.get("box_color")
            if box_color:
                self.box_color = QtGui.QColor(box_color)

            edge_color = state.get("edge_color")
            if edge_color:
                self.edge_color = QtGui.QColor(edge_color)

            session.restore_window_geometry(self)
        finally:
            self._loading_state = False

    def _save_session(self):
        session.save_state(self._collect_state())
        session.save_language(self.i18n.lang)
        session.save_window_geometry(self)

    # ------------------------------------------------------------------
    # Shadows: "warm-up" to avoid artifacts/shader error spam on first
    # use in the session (known VTK+Qt bug on shadow framebuffer
    # initialization). A quick off->on cycle fixes it.
    # The checkbox always starts off (see __init__), so this cycle only
    # fires when the user knowingly turns it on - never automatically
    # at startup.
    # ------------------------------------------------------------------
    def _cycle_shadows_workaround(self):
        self.chk_realistic.blockSignals(True)
        try:
            self.chk_realistic.setChecked(False)
            self.update_model()

            self.chk_realistic.setChecked(True)
            self.update_model()
        finally:
            self.chk_realistic.blockSignals(False)

    def _on_realistic_toggled(self, _state):
        if self.chk_realistic.isChecked():
            self._cycle_shadows_workaround()
        self.schedule_update()

    # ------------------------------------------------------------------
    # Creation/recreation of the VTK 3D widget.
    #
    # Repeatedly toggling shadows on/off on the SAME widget leaves VTK's
    # internal state corrupted (antialiasing that never re-activates,
    # shadows that stop working on the next re-activation - see the
    # history of failed attempts). The only reliable method found is to
    # recreate the widget from scratch on every mode change, instead of
    # trying to clean up the existing one's state.
    # ------------------------------------------------------------------
    def _create_plotter(self) -> QtInteractor:
        plotter = QtInteractor(self)
        plotter.set_background("#cfd3d8", top="#ffffff")

        self.lights = SceneLights(plotter)
        self.shadows = ShadowController(plotter, self.lights)
        setup_orientation_widget(plotter)

        if not self.chk_realistic.isChecked():
            # SSAA (supersampling) instead of FXAA: noticeably higher
            # quality, and like FXAA it doesn't use the multisample
            # framebuffer that had caused problems with shadows (different
            # mechanism: rendering at higher resolution then downscaled,
            # not hardware multisampling) - negligible GPU cost for this
            # geometry.
            try:
                plotter.enable_anti_aliasing("ssaa")
            except Exception as e:
                print("SSAA not available, trying FXAA:", e)
                try:
                    plotter.enable_anti_aliasing("fxaa")
                except Exception as e2:
                    print("FXAA not available, edges without antialiasing:", e2)

        return plotter

    def reset_view(self):
        self.plotter.reset_camera()
        try:
            self.plotter.render()
        except Exception:
            pass

    def closeEvent(self, event):
        self._save_session()

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
        if self._loading_state:
            return
        self.timer.start(120)

    def _on_fillet_toggled(self, _state):
        self.slider_fillet.setEnabled(self.chk_fillet.isChecked())
        self.schedule_update()

    def pick_box_color(self):
        color = QtWidgets.QColorDialog.getColor(
            self.box_color, self, self.i18n.t("dialog_pick_box_color")
        )
        if color.isValid():
            self.box_color = color
            self.update_model()

    def pick_edge_color(self):
        color = QtWidgets.QColorDialog.getColor(
            self.edge_color, self, self.i18n.t("dialog_pick_edge_color")
        )
        if color.isValid():
            self.edge_color = color
            self.update_model()

    def _format_dims_summary(self, dims: BoxDimensions) -> str:
        t = self.i18n.t
        lines = [t("dims_outer", w=dims.width, d=dims.depth, h=dims.height)]
        if dims.has_valid_cavity:
            lines.append(
                t("dims_inner", iw=dims.inner_width, idepth=dims.inner_depth, ih=dims.inner_height)
            )
            lines.append(t("dims_wall", wall=dims.wall))
        else:
            lines.append(t("dims_no_cavity"))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Main loop: reads the sliders, rebuilds geometry and mesh
    #
    # When the 3D widget needs to be recreated (shadow mode change), the
    # technique is the "dual playfield" one: the new widget is built and
    # fully populated ENTIRELY OFF-SCREEN (not yet in the layout), and
    # only once the work is done is it swapped with the old one. This way
    # the user never sees an empty frame mid-work. The camera position is
    # saved before the swap and reapplied to the new widget, so the
    # framing isn't lost.
    # ------------------------------------------------------------------
    def update_model(self):
        try:
            dims = BoxDimensions(
                width=self.slider_w.value(),
                depth=self.slider_h.value(),
                height=self.slider_d.value(),
                wall=self.slider_wall.value(),
            )

            summary = self._format_dims_summary(dims)

            full_result = build_box(dims)

            if self.chk_fillet.isChecked() and self.slider_fillet.value() > 0:
                requested_r = self.slider_fillet.value()
                outer_r, inner_r = compute_fillet_radii(dims, requested_r)
                full_result = apply_edge_fillets(full_result, dims, requested_r)

                if outer_r < requested_r or inner_r < requested_r:
                    summary += "\n" + self.i18n.t(
                        "fillet_clamped", req=requested_r, outer=outer_r, inner=inner_r
                    )
                else:
                    summary += "\n" + self.i18n.t("fillet_applied", outer=outer_r)

            self.dims_label.setText(summary)

            self._full_result = full_result
            self._full_dims = dims

            preview_result = full_result

            axis = self.clip_axis.currentData()
            if axis and axis != "none":
                pos = self.clip_pos.value() / 100.0
                try:
                    preview_result = apply_preview_clip(full_result, axis, pos, dims)
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

            style = RenderStyle(
                base_color=(
                    self.box_color.redF(),
                    self.box_color.greenF(),
                    self.box_color.blueF(),
                ),
                edge_color=(
                    self.edge_color.redF(),
                    self.edge_color.greenF(),
                    self.edge_color.blueF(),
                ),
                show_edges=self.chk_edges.isChecked(),
                smooth_shading=self.chk_smooth.isChecked(),
                line_width=self.slider_line.value() / 10.0,
                realistic=self.chk_realistic.isChecked(),
            )

            needs_rebuild = style.realistic != self._last_realistic
            self._last_realistic = style.realistic

            old_plotter = None
            saved_camera = None
            restored_camera = False
            t0 = time.perf_counter()

            if needs_rebuild:
                _dbg(t0, f"REBUILD START. stack.size={self.plotter_stack.size()} "
                         f"old_plotter.size={self.plotter.size()}")

                # Curtain IMMEDIATELY, before any heavy work, and force Qt
                # to actually draw it right now (otherwise, since
                # everything is synchronous, it risks being drawn together
                # with the final result in the same repaint cycle,
                # negating the effect).
                self.plotter_stack.setCurrentWidget(self.curtain)
                QtWidgets.QApplication.processEvents()
                _dbg(t0, "curtain shown")

                old_plotter = self.plotter

                if self._camera_initialized:
                    try:
                        saved_camera = old_plotter.camera_position
                    except Exception:
                        saved_camera = None
                _dbg(t0, f"camera saved: {saved_camera}")

                plotter = self._create_plotter()
                _dbg(t0, f"new plotter created. size BEFORE addWidget={plotter.size()}")

                self.plotter_stack.addWidget(plotter)

                # FIX: the stack automatically resizes only the CURRENT
                # page, not the hidden ones - the new widget stays at the
                # default size (100x30) until it becomes visible. Force
                # the resize by hand, so the render below already happens
                # at the right size.
                plotter.resize(self.plotter_stack.size())

                _dbg(t0, f"added to stack + forced resize. size AFTER={plotter.size()} "
                         f"stack.size={self.plotter_stack.size()}")

                if saved_camera is not None:
                    try:
                        plotter.camera_position = saved_camera
                        restored_camera = True
                    except Exception:
                        restored_camera = False
                _dbg(t0, f"camera restored={restored_camera}")
            else:
                plotter = self.plotter

            if style.realistic:
                self.shadows.enable()
            else:
                self.shadows.disable()
            if needs_rebuild:
                _dbg(t0, "shadows set")

            render_mesh(plotter, mesh, style)
            if needs_rebuild:
                _dbg(t0, f"mesh rendered. plotter.size={plotter.size()}")

            if restored_camera:
                self._camera_initialized = True
            elif not self._camera_initialized:
                plotter.reset_camera()
                self._camera_initialized = True
                if needs_rebuild:
                    _dbg(t0, "reset_camera() called (no saved camera)")

            if needs_rebuild:
                _dbg(t0, f"BEFORE plotter.render(). plotter.size={plotter.size()} "
                         f"isVisible={plotter.isVisible()}")
                try:
                    plotter.render()
                except Exception as e:
                    _dbg(t0, f"plotter.render() failed: {e}")
                _dbg(t0, "AFTER plotter.render()")

                _dbg(t0, f"BEFORE setCurrentWidget. stack.currentWidget size={self.plotter_stack.currentWidget().size()}")
                self.plotter_stack.setCurrentWidget(plotter)
                _dbg(t0, f"AFTER setCurrentWidget. plotter.isVisible={plotter.isVisible()} "
                         f"plotter.size={plotter.size()}")

                self.plotter_stack.removeWidget(old_plotter)
                try:
                    old_plotter.close()
                except Exception:
                    pass
                old_plotter.deleteLater()
                self.plotter = plotter
                _dbg(t0, "REBUILD END")

            # Corner text AND 3D axes/dimensions added here, AFTER any
            # swap: adding them earlier (on the not-yet-visible/swapped
            # widget) causes a visual jump - empirically identified as
            # the real cause of the glitch. This specifically concerns
            # anything using add_text/add_point_labels (labels), not the
            # plain meshes (box, arrows, lines), which in fact cause no
            # issues even when added earlier.
            if self.chk_overlay.isChecked():
                update_dimension_overlay(self.plotter, summary)
            else:
                try:
                    self.plotter.remove_actor("dims_overlay")
                except Exception:
                    pass

            update_reference_annotations(
                self.plotter, dims.width, dims.depth, dims.height,
                visible=self.chk_axes.isChecked(),
                # In realistic mode (shadows on, AA off to avoid the
                # render-pass conflict) label occlusion works correctly:
                # always_visible=False. In normal mode (AA on) occlusion
                # seems to break due to the same kind of conflict already
                # seen between FXAA and shadows: always_visible=True,
                # always on top but at least visible.
                label_always_visible=not style.realistic,
            )

        except Exception as e:
            print("Error while updating the model:", e)

    def export_stl(self):
        # Two filters in the same dialog: STL for printing as-is, STEP
        # for a true B-Rep export that stays editable in any CAD (holes,
        # bosses, grooves, etc. can be added after the fact). The chosen
        # filter, not just the extension the user may or may not have
        # typed, decides the format below.
        path, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self, self.i18n.t("dialog_save_stl_title"), "box.stl",
            "STL (*.stl);;STEP (*.step)"
        )

        if not path:
            return

        wants_step = "step" in selected_filter.lower()

        if wants_step:
            if not path.lower().endswith((".step", ".stp")):
                path += ".step"
        else:
            if not path.lower().endswith(".stl"):
                path += ".stl"

        if self._full_result is None:
            return

        try:
            if wants_step:
                export_to_step(self._full_result, path)
            else:
                export_to_stl(self._full_result, path)
            print("Model exported:", path)
            if self._full_dims is not None:
                print(self._full_dims.describe())
        except Exception as e:
            print("Error during model export:", e)
