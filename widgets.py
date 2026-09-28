"""
Small reusable Qt widgets for the UI.
"""

from PySide6 import QtWidgets, QtCore


class LabeledSlider(QtWidgets.QWidget):
    """Horizontal slider + always-updated numeric label (e.g. "80 mm").

    Exposes the same valueChanged signal as the internal slider, so it
    can be used as a drop-in replacement for a QSlider in the rest of
    the UI.
    """

    valueChanged = QtCore.Signal(int)

    def __init__(self, min_val: int, max_val: int, initial: int, unit: str = "mm", parent=None):
        super().__init__(parent)

        self._unit = unit

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setMinimum(min_val)
        self.slider.setMaximum(max_val)
        self.slider.setValue(initial)

        self.value_label = QtWidgets.QLabel(self._format(initial))
        self.value_label.setMinimumWidth(56)
        self.value_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        layout.addWidget(self.slider, 1)
        layout.addWidget(self.value_label)

        self.slider.valueChanged.connect(self._on_value_changed)

    def _format(self, value: int) -> str:
        return f"{value} {self._unit}"

    def _on_value_changed(self, value: int):
        self.value_label.setText(self._format(value))
        self.valueChanged.emit(value)

    def value(self) -> int:
        return self.slider.value()

    def setValue(self, value: int):
        self.slider.setValue(value)


class PrecisionSlider(QtWidgets.QWidget):
    """Synchronized slider + QDoubleSpinBox, for measurements with decimals.

    The slider is for quick "by eye" adjustment; the spinbox lets you type
    the exact value (e.g. 18.15 mm) or fine-tune it with the arrows in
    steps of `1 / 10**decimals`.

    With decimals=2 the minimum step is 0.01 mm = 10 microns, the typical
    resolution limit of an FDM/resin 3D printer — going further would have
    no practical effect on the printed part.
    """

    valueChanged = QtCore.Signal(float)

    def __init__(
        self,
        min_val: float,
        max_val: float,
        initial: float,
        decimals: int = 2,
        unit: str = "mm",
        parent=None,
    ):
        super().__init__(parent)

        self._scale = 10 ** decimals
        self._updating = False

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setMinimum(round(min_val * self._scale))
        self.slider.setMaximum(round(max_val * self._scale))
        self.slider.setValue(round(initial * self._scale))

        self.spin = QtWidgets.QDoubleSpinBox()
        self.spin.setDecimals(decimals)
        self.spin.setRange(min_val, max_val)
        self.spin.setSingleStep(1 / self._scale)
        self.spin.setSuffix(f" {unit}")
        self.spin.setValue(initial)
        self.spin.setMinimumWidth(90)

        layout.addWidget(self.slider, 1)
        layout.addWidget(self.spin)

        self.slider.valueChanged.connect(self._on_slider_changed)
        self.spin.valueChanged.connect(self._on_spin_changed)

    def _on_slider_changed(self, raw: int):
        if self._updating:
            return
        self._updating = True
        value = raw / self._scale
        self.spin.setValue(value)
        self._updating = False
        self.valueChanged.emit(value)

    def _on_spin_changed(self, value: float):
        if self._updating:
            return
        self._updating = True
        self.slider.setValue(round(value * self._scale))
        self._updating = False
        self.valueChanged.emit(value)

    def value(self) -> float:
        return self.spin.value()

    def setValue(self, value: float):
        self.spin.setValue(value)
