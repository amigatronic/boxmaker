"""
Entry point. Run with:  python main.py
"""

import os

os.environ.setdefault("QT_OPENGL", "desktop")

import logging
import sys

# We silence ONLY the known VTK noise (shader compilation related to
# shadows), not all warnings: a global setLevel(ERROR) would also hide
# real, unrelated future errors. The filter looks at the message content,
# not the level, so it lets any other warning through.
_VTK_NOISE_MARKERS = ("vtkShaderProgram", "vtkOpenGL", "ERROR: In vtk")


class _VtkNoiseFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        return not any(marker in msg for marker in _VTK_NOISE_MARKERS)


logging.getLogger().addFilter(_VtkNoiseFilter())

from PySide6 import QtWidgets, QtCore, QtGui

QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseDesktopOpenGL, True)

# Universal fix (does not depend on GPU brand/model): explicitly tells Qt
# which OpenGL version/profile to request for its widgets, BEFORE the
# QApplication is created. Without this, on some Qt+VTK version
# combinations the context Qt creates by default is not enough for the
# shaders required by VTK (#version 150 = OpenGL 3.2 core), even if the
# GPU supports much newer versions (here e.g. OpenGL 4.5).
_fmt = QtGui.QSurfaceFormat()
_fmt.setVersion(3, 2)
_fmt.setProfile(QtGui.QSurfaceFormat.CoreProfile)
_fmt.setDepthBufferSize(24)
_fmt.setStencilBufferSize(8)
QtGui.QSurfaceFormat.setDefaultFormat(_fmt)

try:
    import vtk
    vtk.vtkObject.GlobalWarningDisplayOff()
except Exception:
    pass


def _build_splash_pixmap(loading_text: str, progress: float) -> QtGui.QPixmap:
    """Drawn in code instead of from an image file: no extra asset
    to bundle into the executable.

    `progress` ranges from 0.0 to 1.0. NOTE: it is not a "real"
    percentage (Python does not give an easy way to know how much of an
    import is left) - these are fixed stages assigned based on the typical
    relative cost of each library, not a real-time measurement. It still
    gives real progress feedback (each stage fires when that library is
    ACTUALLY loaded), just not proportional to time with precision."""
    width, height = 480, 260
    pixmap = QtGui.QPixmap(width, height)
    pixmap.fill(QtGui.QColor("#cfd3d8"))

    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)

    painter.setPen(QtGui.QColor("#333333"))
    painter.setFont(QtGui.QFont("Segoe UI", 28, QtGui.QFont.Bold))
    title_rect = pixmap.rect().adjusted(0, -40, 0, 0)
    painter.drawText(title_rect, QtCore.Qt.AlignCenter, "Box Maker")

    painter.setPen(QtGui.QColor("#666666"))
    painter.setFont(QtGui.QFont("Segoe UI", 10))
    sub_rect = pixmap.rect().adjusted(0, 30, 0, 0)
    painter.drawText(sub_rect, QtCore.Qt.AlignCenter, loading_text)

    bar_w, bar_h = 320, 10
    bar_x = (width - bar_w) // 2
    bar_y = height - 50

    painter.setPen(QtGui.QPen(QtGui.QColor("#999999"), 1))
    painter.setBrush(QtGui.QColor("#ffffff"))
    painter.drawRoundedRect(bar_x, bar_y, bar_w, bar_h, 4, 4)

    fill_w = int(bar_w * max(0.0, min(1.0, progress)))
    if fill_w > 0:
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("#bf7f00"))  # same orange as the default box color
        painter.drawRoundedRect(bar_x, bar_y, fill_w, bar_h, 4, 4)

    painter.end()
    return pixmap


def main():
    app = QtWidgets.QApplication(sys.argv)

    # session.py and i18n.py are lightweight (they don't import
    # build123d/vtk), so we can load the saved language right away,
    # without negating the effect of the splash screen that covers the
    # heavy import further below.
    import session
    from i18n import Translator, DEFAULT_LANGUAGE
    saved_lang = session.load_language(DEFAULT_LANGUAGE)
    splash_i18n = Translator(saved_lang)

    splash = QtWidgets.QSplashScreen(
        _build_splash_pixmap(splash_i18n.t("splash_loading"), 0.0)
    )
    splash.show()
    app.processEvents()

    def set_progress(fraction: float, key: str):
        splash.setPixmap(_build_splash_pixmap(splash_i18n.t(key), fraction))
        app.processEvents()  # force Qt to redraw IMMEDIATELY, not on the next event loop turn

    # Staged import instead of a single "from main_window import
    # MainWindow": that single line would do all the heavy loading at
    # once, with no way to update the progress bar in between. By
    # pre-importing the heaviest libraries here one at a time,
    # main_window.py will find them already cached (instant import) when
    # it imports them in turn.
    set_progress(0.15, "splash_stage_geometry")
    import build123d  # noqa: F401

    set_progress(0.55, "splash_stage_render")
    import pyvista  # noqa: F401 (vtk was already imported above)

    set_progress(0.80, "splash_stage_ui")
    from main_window import MainWindow

    set_progress(0.95, "splash_stage_window")
    win = MainWindow()

    set_progress(1.0, "splash_loading")
    splash.finish(win)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
