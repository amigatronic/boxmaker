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
import logging
import os
import time

# DEBUG: detailed diagnostics on the 3D widget rebuild (dimensions,
# timing of each phase). Set to False to silence it once resolved.
DEBUG_REBUILD = False


def _dbg(t0, msg):
    if DEBUG_REBUILD:
        log.info("[REBUILD +%7.4fs] %s", time.perf_counter() - t0, msg)


# Known VTK+Qt bug on Windows: certain NVIDIA driver + Qt-shared OpenGL
# context combinations fail to compile shaders ("Could not set shader
# program") when multisample anti-aliasing is enabled by default. It
# must be disabled BEFORE any plotter/QtInteractor is created.
pv.global_theme.multi_samples = 0

from fonts import scan_fonts, is_usable_font
from geometry import BoxDimensions, build_filleted_box, apply_preview_clip
from shapes import (
    ShapeSpec, ShapeError, SvgImportError, load_svg_profile, SHAPE_CODES, SHAPE_RECT, SHAPE_CIRCLE,
    SHAPE_POLYGON, SHAPE_SVG, SHAPE_TEXT, SHAPES_WITHOUT_DEPTH, MIN_SIDES, MAX_SIDES, MAX_GROW,
    DEFAULT_FONT,
)
from mesh_utils import build123d_to_pv_mesh
from rendering import SceneLights, ShadowController, RenderStyle, render_mesh, update_dimension_overlay
from dimension_annotations import setup_orientation_widget, update_reference_annotations
from exporting import export_to_stl, export_to_step
from widgets import LabeledSlider, PrecisionSlider
from i18n import Translator, SUPPORTED_LANGUAGES, LANGUAGE_NAMES, DEFAULT_LANGUAGE
import session

log = logging.getLogger(__name__)

# While a slider is being dragged, the model updates live only if the last
# update took less than this (seconds); otherwise it waits for the release.
LIVE_UPDATE_LIMIT_S = 0.4


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        # --- Language: must be decided BEFORE building the UI, so the
        # labels are already born translated instead of having to be recreated ---
        saved_lang = session.load_language(DEFAULT_LANGUAGE)
        self.i18n = Translator(saved_lang)

        self.resize(1200, 700)

        self._full_result = None
        # Imported SVG profile: path + token (the token changes at every
        # import and invalidates the geometry cache)
        self._svg_path = ""
        self._svg_token = 0
        self._last_shape_code = SHAPE_RECT
        self._font_path = ""  # empty = system font (DEFAULT_FONT)
        self._last_update_seconds = 0.0  # duration of the last model update
        self._full_dims: BoxDimensions | None = None
        self._camera_initialized = False
        self._loading_state = False  # avoids a burst of recomputations during session restore

        # Caches: geometry depends only on dimensions + fillet, the mesh
        # also on the section. Style-only changes (colors, edges, smooth,
        # overlay...) reuse them and skip OCCT entirely.
        self._geom_cache_key = None
        self._geom_cache = None  # FilletOutcome
        self._mesh_cache_key = None
        self._mesh_cache = None

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

        # --- Shape of the box: the 2D profile that gets extruded ---
        self.shape_combo = QtWidgets.QComboBox()
        for code in SHAPE_CODES:
            self.shape_combo.addItem("", userData=code)  # texts set in retranslate_ui
        self.shape_combo.currentIndexChanged.connect(self._on_shape_changed)
        self._add_row("label_shape", self.shape_combo)

        # --- Slider + spinbox for physical measurements: precision 0.01 mm (10 microns) ---
        self.slider_w = PrecisionSlider(20.0, 200.0, 80.0, decimals=2)
        self.slider_depth = PrecisionSlider(20.0, 200.0, 50.0, decimals=2)
        self.slider_height = PrecisionSlider(10.0, 200.0, 20.0, decimals=2)
        self.slider_wall = PrecisionSlider(0.0, 20.0, 2.0, decimals=2)

        self._add_row("label_width", self.slider_w)
        self._add_row("label_depth", self.slider_depth)

        self.slider_sides = LabeledSlider(MIN_SIDES, MAX_SIDES, 6, unit="")
        self._add_row("label_sides", self.slider_sides)

        # SVG import: button + info about the loaded file
        self.svg_row = QtWidgets.QWidget()
        svg_layout = QtWidgets.QVBoxLayout(self.svg_row)
        svg_layout.setContentsMargins(0, 0, 0, 0)
        self.btn_import_svg = QtWidgets.QPushButton()
        self.btn_import_svg.clicked.connect(self._on_import_svg_clicked)
        self._self_label_widgets.append((self.btn_import_svg, "button_import_svg"))
        self.svg_info_label = QtWidgets.QLabel()
        self.svg_info_label.setWordWrap(True)
        svg_layout.addWidget(self.btn_import_svg)
        svg_layout.addWidget(self.svg_info_label)
        self._add_row("label_svg", self.svg_row)

        # Text shape: text, font file and letter growth
        self.text_edit = QtWidgets.QLineEdit("Logo")
        self.text_edit.setMaxLength(60)
        self.text_edit.textChanged.connect(self.schedule_update)
        self._add_row("label_text", self.text_edit)

        self.font_row = QtWidgets.QWidget()
        font_layout = QtWidgets.QVBoxLayout(self.font_row)
        font_layout.setContentsMargins(0, 0, 0, 0)

        # Installed fonts are read from the font folders (see fonts.py) and
        # filled in the first time the text shape is used. The first item
        # is always "system default".
        self.font_combo = QtWidgets.QComboBox()
        self.font_combo.setEditable(True)
        self.font_combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        completer = self.font_combo.completer()
        completer.setFilterMode(QtCore.Qt.MatchContains)
        completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self.font_combo.addItem("", userData="")
        self._fonts_loaded = False
        self.font_combo.activated.connect(self._on_font_selected)

        self.btn_choose_font = QtWidgets.QPushButton()
        self.btn_choose_font.clicked.connect(self._on_choose_font_clicked)
        self._self_label_widgets.append((self.btn_choose_font, "button_choose_font"))
        font_layout.addWidget(self.font_combo)
        font_layout.addWidget(self.btn_choose_font)
        self._add_row("label_font", self.font_row)

        self.slider_grow = PrecisionSlider(0.0, MAX_GROW, 0.0, decimals=2)
        self._add_row("label_grow", self.slider_grow)
        self._add_row("label_height", self.slider_height)
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

        # Sliders: while dragging, the model follows live only if the last
        # update was quick; with a heavy model (text, SVG, fillet) it is
        # updated when the handle is released.
        self._drag_sliders = [
            self.slider_w, self.slider_depth, self.slider_sides, self.slider_grow,
            self.slider_height, self.slider_wall, self.clip_pos, self.slider_line,
            self.slider_fillet,
        ]
        for slider in self._drag_sliders:
            slider.valueChanged.connect(self._on_param_changed)
            slider.released.connect(self.schedule_update)

        self.clip_axis.currentIndexChanged.connect(self.schedule_update)
        self.chk_edges.stateChanged.connect(self.schedule_update)
        self.chk_smooth.stateChanged.connect(self.schedule_update)
        self.chk_realistic.stateChanged.connect(self._on_realistic_toggled)
        self.chk_overlay.stateChanged.connect(self.schedule_update)
        self.chk_axes.stateChanged.connect(self.schedule_update)
        self.chk_fillet.stateChanged.connect(self._on_fillet_toggled)

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

        for index, code in enumerate(SHAPE_CODES):
            self.shape_combo.setItemText(index, self.i18n.t(f"shape_{code}"))

        self._apply_shape_controls()

    # ------------------------------------------------------------------
    # Shape selector (primitives + SVG profile)
    # ------------------------------------------------------------------
    def _current_shape_code(self) -> str:
        return self.shape_combo.currentData() or SHAPE_RECT

    def _apply_shape_controls(self):
        """Shows only the controls that make sense for the chosen shape and
        names the first measure accordingly (diameter or width)."""
        code = self._current_shape_code()

        self.form.setRowVisible(self.slider_depth, code not in SHAPES_WITHOUT_DEPTH)
        self.form.setRowVisible(self.slider_sides, code == SHAPE_POLYGON)
        self.form.setRowVisible(self.svg_row, code == SHAPE_SVG)
        self.form.setRowVisible(self.text_edit, code == SHAPE_TEXT)
        self.form.setRowVisible(self.font_row, code == SHAPE_TEXT)
        self.form.setRowVisible(self.slider_grow, code == SHAPE_TEXT)

        label = self.form.labelForField(self.slider_w)
        if label is not None:
            key = "label_diameter" if code in (SHAPE_CIRCLE, SHAPE_POLYGON) else "label_width"
            label.setText(self.i18n.t(key))

        self._refresh_svg_info()
        self._refresh_font_controls()

    def _refresh_font_controls(self):
        self.font_combo.setItemText(0, self.i18n.t("font_default", font=DEFAULT_FONT))
        if self._current_shape_code() == SHAPE_TEXT:
            self._ensure_font_list()
            self._select_font_in_combo()

    def _ensure_font_list(self):
        """Fills the combo box with the installed fonts (only once)."""
        if self._fonts_loaded:
            return
        self._fonts_loaded = True

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            entries = scan_fonts()
        except Exception:
            log.exception("Font scan failed")
            entries = []
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        self.font_combo.blockSignals(True)
        for entry in entries:
            self.font_combo.addItem(entry.label, userData=entry.path)
        self.font_combo.blockSignals(False)
        log.info("%d installed fonts listed", len(entries))

    def _select_font_in_combo(self):
        """Shows the current font in the combo box (adds it if it is a
        custom file that is not in the list)."""
        index = 0
        if self._font_path:
            index = self.font_combo.findData(self._font_path)
            if index < 0:
                label = self.i18n.t("font_file_entry", name=os.path.basename(self._font_path))
                self.font_combo.addItem(label, userData=self._font_path)
                index = self.font_combo.count() - 1

        self.font_combo.blockSignals(True)
        self.font_combo.setCurrentIndex(index)
        self.font_combo.blockSignals(False)

    def _on_font_selected(self, index: int):
        self._font_path = self.font_combo.itemData(index) or ""
        self.schedule_update()

    def _on_choose_font_clicked(self):
        # NOT the Windows Fonts folder: it is a virtual folder where the
        # dialog cannot open files; installed fonts are in the list above.
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            self.i18n.t("dialog_choose_font_title"),
            os.path.expanduser("~"),
            "Fonts (*.ttf *.otf *.ttc)",
        )
        if not path:
            return

        if not is_usable_font(path):
            QtWidgets.QMessageBox.warning(
                self, self.i18n.t("dialog_choose_font_title"), self.i18n.t("font_invalid")
            )
            return

        self._font_path = path
        self._select_font_in_combo()
        self.schedule_update()

    def _refresh_svg_info(self):
        if not self._svg_path:
            self.svg_info_label.setText(self.i18n.t("svg_none"))
            return
        profile_info = getattr(self, "_svg_summary", None)
        name = os.path.basename(self._svg_path)
        if profile_info is None:
            self.svg_info_label.setText(name)
        else:
            holes, ignored = profile_info
            self.svg_info_label.setText(
                self.i18n.t("svg_info", name=name, holes=holes, ignored=ignored)
            )

    def _set_shape_code(self, code: str):
        """Selects a shape in the combo box WITHOUT triggering the handler."""
        if code not in SHAPE_CODES:
            code = SHAPE_RECT
        self.shape_combo.blockSignals(True)
        self.shape_combo.setCurrentIndex(SHAPE_CODES.index(code))
        self.shape_combo.blockSignals(False)
        self._last_shape_code = code
        self._apply_shape_controls()

    def _on_shape_changed(self, index: int):
        if self._loading_state:
            return
        code = self._current_shape_code()

        if code == SHAPE_SVG and not self._svg_path:
            # An SVG shape needs a file: ask for it right away; if the user
            # cancels (or the file is not usable) go back to the previous shape.
            if not self._import_svg_dialog():
                self._set_shape_code(self._last_shape_code)
                return

        self._last_shape_code = code
        self._apply_shape_controls()
        self.schedule_update()

    def _on_import_svg_clicked(self):
        if self._import_svg_dialog():
            self.schedule_update()

    def _load_svg(self, path: str) -> bool:
        """Loads the SVG profile; shows the error to the user on failure."""
        try:
            profile = load_svg_profile(path, self._svg_token + 1)
        except SvgImportError as e:
            QtWidgets.QMessageBox.warning(
                self,
                self.i18n.t("svg_error_title"),
                self.i18n.t(f"svg_error_{e.code}", detail=e.detail),
            )
            return False
        except Exception as e:  # unexpected failure in the SVG/OCCT libraries
            log.exception("SVG import failed")
            QtWidgets.QMessageBox.warning(
                self, self.i18n.t("svg_error_title"),
                self.i18n.t("svg_error_read_failed", detail=str(e)),
            )
            return False

        self._svg_token += 1
        self._svg_path = path
        self._svg_summary = (profile.holes_used, profile.shapes_ignored)
        self._refresh_svg_info()
        return True

    def _import_svg_dialog(self) -> bool:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, self.i18n.t("dialog_import_svg_title"), self._svg_path or "", "SVG (*.svg)"
        )
        if not path:
            return False
        return self._load_svg(path)

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
            "depth": self.slider_depth.value(),
            "height": self.slider_height.value(),
            "wall": self.slider_wall.value(),
            "shape": self._current_shape_code(),
            "sides": self.slider_sides.value(),
            "svg_path": self._svg_path,
            "text": self.text_edit.text(),
            "font_path": self._font_path,
            "grow": self.slider_grow.value(),
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
            self.slider_depth.setValue(state.get("depth", self.slider_depth.value()))
            self.slider_height.setValue(state.get("height", self.slider_height.value()))
            self.slider_wall.setValue(state.get("wall", self.slider_wall.value()))
            self.slider_sides.setValue(state.get("sides", self.slider_sides.value()))

            self.text_edit.setText(state.get("text", self.text_edit.text()))
            self.slider_grow.setValue(state.get("grow", self.slider_grow.value()))
            font_path = state.get("font_path", "")
            self._font_path = font_path if font_path and is_usable_font(font_path) else ""

            shape_code = state.get("shape", SHAPE_RECT)
            svg_path = state.get("svg_path", "")
            if svg_path and os.path.isfile(svg_path):
                self._load_svg(svg_path)
            if shape_code == SHAPE_SVG and not self._svg_path:
                shape_code = SHAPE_RECT  # the file is gone: do not ask at startup
            self._set_shape_code(shape_code)

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
                log.warning("SSAA not available, trying FXAA: %s", e)
                try:
                    plotter.enable_anti_aliasing("fxaa")
                except Exception as e2:
                    log.warning("FXAA not available, edges without antialiasing: %s", e2)

        return plotter

    def _render_current_plotter(self):
        try:
            self.plotter.renderer.ResetCameraClippingRange()
            self.plotter.render()
        except Exception:
            pass

    def reset_view(self):
        self.plotter.reset_camera()
        try:
            self.plotter.render()
        except Exception:
            pass

    def closeEvent(self, event):
        self._save_session()

        try:
            from vtkmodules.vtkCommonCore import vtkObject
            vtkObject.GlobalWarningDisplayOff()
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

    def _on_param_changed(self, *_):
        """A slider/spin value changed. During a drag, a slow model waits for
        the release instead of recomputing at every step."""
        if self._last_update_seconds > LIVE_UPDATE_LIMIT_S and any(
            slider.is_dragging() for slider in self._drag_sliders
        ):
            return
        self.schedule_update()

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
        lines = []
        kind = dims.shape.kind
        if kind != SHAPE_RECT:
            name = t(f"shape_{kind}")
            if kind == SHAPE_POLYGON:
                name = t("shape_polygon_n", n=dims.shape.sides)
            elif kind == SHAPE_SVG:
                name = f"{name} ({os.path.basename(dims.shape.svg_path)})"
            elif kind == SHAPE_TEXT:
                name = f"{name}: {dims.shape.text}"
            lines.append(t("dims_shape", shape=name))
            if dims.profiles.pieces > 1:
                lines.append(t("dims_pieces", n=dims.profiles.pieces))
        lines.append(t("dims_outer", w=dims.bbox_width, d=dims.bbox_depth, h=dims.height))
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
    # ------------------------------------------------------------------
    # update_model() helpers. The plotter rebuild / camera / render block
    # stays inline in update_model() on purpose: its ORDER is delicate
    # (see the comments there) and must not be split across methods.
    # ------------------------------------------------------------------
    def _read_dims(self) -> BoxDimensions:
        code = self._current_shape_code()
        if code == SHAPE_SVG and not self._svg_path:
            code = SHAPE_RECT
        text = self.text_edit.text() if code == SHAPE_TEXT else ""
        if code == SHAPE_TEXT and not text.strip():
            code = SHAPE_RECT  # nothing to draw yet (the field is empty)
        shape = ShapeSpec(
            kind=code,
            sides=self.slider_sides.value(),
            svg_path=self._svg_path if code == SHAPE_SVG else "",
            svg_token=self._svg_token if code == SHAPE_SVG else 0,
            text=text,
            font_path=self._font_path if code == SHAPE_TEXT else "",
            grow=self.slider_grow.value() if code == SHAPE_TEXT else 0.0,
        )
        return BoxDimensions(
            width=self.slider_w.value(),
            depth=self.slider_depth.value(),
            height=self.slider_height.value(),
            wall=self.slider_wall.value(),
            shape=shape,
        )

    def _requested_fillet(self) -> float:
        """Requested fillet radius, or 0 when the fillet is off."""
        if self.chk_fillet.isChecked() and self.slider_fillet.value() > 0:
            return self.slider_fillet.value()
        return 0

    def _build_geometry(self, dims: BoxDimensions, requested_r: float):
        """Returns (geom_key, outcome). The solid is rebuilt only when
        dimensions, shape or fillet change."""
        geom_key = (dims, requested_r)
        if geom_key != self._geom_cache_key:
            self._geom_cache = build_filleted_box(dims, requested_r)
            self._geom_cache_key = geom_key

        return geom_key, self._geom_cache

    def _fillet_summary(self, dims, requested_r, outcome) -> str:
        """Extra summary line about the fillet ('' when the fillet is off).
        Radii are the ones REALLY applied (they can be smaller than the
        requested one)."""
        if requested_r <= 0:
            return ""

        t = self.i18n.t
        if not (outcome.outer_applied and outcome.inner_applied):
            return "\n" + t("fillet_failed", req=requested_r)

        outer_r, inner_r = outcome.outer_radius, outcome.inner_radius
        reduced = outer_r < requested_r - 1e-6

        if not dims.has_valid_cavity:
            # Closed prism: a single radius, applied to all the edges.
            if reduced:
                return "\n" + t("fillet_clamped_closed", req=requested_r, outer=outer_r)
            return "\n" + t("fillet_applied_closed", outer=outer_r)

        # The inner radius follows the outer one by design, so only the
        # OUTER radius can be "clamped" with respect to the request.
        if reduced:
            return "\n" + t("fillet_clamped", req=requested_r, outer=outer_r, inner=inner_r)
        return "\n" + t("fillet_applied", outer=outer_r, inner=inner_r)

    def _build_preview_mesh(self, full_result, dims, geom_key):
        """Mesh for the viewport (with the preview section if active).
        Rebuilt only when geometry or section change."""
        axis = self.clip_axis.currentData()
        clip_active = bool(axis) and axis != "none"
        clip_pos = self.clip_pos.value() if clip_active else 0

        mesh_key = (geom_key, axis if clip_active else "none", clip_pos)
        if mesh_key != self._mesh_cache_key:
            preview_result = full_result
            if clip_active:
                try:
                    preview_result = apply_preview_clip(
                        full_result, axis, clip_pos / 100.0, dims
                    )
                except Exception as clip_error:
                    log.warning("Error in the section, using the whole model: %s", clip_error)
                    preview_result = full_result

            self._mesh_cache = build123d_to_pv_mesh(preview_result)
            self._mesh_cache_key = mesh_key

        return self._mesh_cache

    def _read_style(self) -> RenderStyle:
        return RenderStyle(
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

    def _update_annotations(self, dims: BoxDimensions, summary: str):
        # Corner text AND 3D axes/dimensions added AFTER any plotter swap:
        # adding them earlier (on the not-yet-visible/swapped widget)
        # causes a visual jump - empirically identified as the real cause
        # of the glitch. This specifically concerns anything using
        # add_text/add_point_labels (labels), not the plain meshes (box,
        # arrows, lines), which cause no issues even when added earlier.
        if self.chk_overlay.isChecked():
            update_dimension_overlay(self.plotter, summary)
        else:
            try:
                self.plotter.remove_actor("dims_overlay")
            except Exception:
                pass

        update_reference_annotations(
            self.plotter, dims.bbox_width, dims.bbox_depth, dims.height,
            visible=self.chk_axes.isChecked(),
            # SSAA is now active in both modes, which breaks label
            # occlusion: always_visible=True keeps labels readable.
            label_always_visible=True,
        )

    def update_model(self):
        """Rebuilds the model and refreshes the preview. Shows a wait cursor
        and records how long it took (used to decide whether a slider drag
        can update the model live)."""
        start = time.perf_counter()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self._update_model_impl()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self._last_update_seconds = time.perf_counter() - start
            if self._last_update_seconds > 1.0:
                log.info("Slow model update: %.2f s", self._last_update_seconds)

    def _update_model_impl(self):
        try:
            dims = self._read_dims()
            summary = self._format_dims_summary(dims)

            requested_r = self._requested_fillet()
            geom_key, outcome = self._build_geometry(dims, requested_r)
            full_result = outcome.part
            summary += self._fillet_summary(dims, requested_r, outcome)
            self.dims_label.setText(summary)

            self._full_result = full_result
            self._full_dims = dims

            mesh = self._build_preview_mesh(full_result, dims, geom_key)

            if mesh.n_points == 0 or mesh.n_cells == 0:
                try:
                    self.plotter.remove_actor("model")
                except Exception:
                    pass
                self._update_annotations(dims, summary)
                self._render_current_plotter()
                return

            style = self._read_style()

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
                # The restored camera keeps the default clipping range of
                # the new widget, which is not fitted to the model: depth
                # precision is poor and shadow edges look jagged. Rotating
                # the view fixes it only because the interactor resets the
                # range. Do the same here, without moving the camera.
                try:
                    plotter.renderer.ResetCameraClippingRange()
                except Exception:
                    pass

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

                # Extra render once the new widget is really shown and
                # sized: the first frame of a freshly created SSAA+shadow
                # pipeline can show jagged edges until the next render.
                QtCore.QTimer.singleShot(50, self._render_current_plotter)

            self._update_annotations(dims, summary)

        except ShapeError as e:
            # Expected failure (e.g. growth larger than the text): tell the
            # user in the panel instead of only writing to the log.
            log.warning("Shape not available: %s", e)
            key = f"shape_error_{e.code}"
            text = self.i18n.t(key, detail=e.detail)
            if text == key:
                text = self.i18n.t("shape_error_generic", detail=str(e))
            self.dims_label.setText(text)
        except Exception:
            log.exception("Error while updating the model")

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
            log.info("Model exported: %s", path)
            if self._full_dims is not None:
                log.info("%s", self._full_dims.describe())
        except Exception:
            log.exception("Error during model export")
