from __future__ import annotations

from typing import Callable

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QComboBox, QLabel, QStackedWidget, QVBoxLayout, QWidget

from ....session.state import AnalysisMode, LiveviewSessionState, LiveviewState


MODE_ITEMS: tuple[tuple[str, AnalysisMode], ...] = (
    ("Off", AnalysisMode.OFF),
    ("Monodisperse analysis", AnalysisMode.MONODISPERSE),
    ("Polydisperse analysis: d(r)", AnalysisMode.POLYDISPERSE_DR),
    ("Polydisperse analysis: mixture", AnalysisMode.POLYDISPERSE_MIXTURE),
)


class AnalysisModeSelector(QWidget):
    analysis_mode_changed = pyqtSignal(object)
    modeling_enabled_changed = pyqtSignal(bool)

    def __init__(
        self,
        *,
        state: LiveviewSessionState,
        on_open_fit_sizes: Callable[[], None],
        on_open_fit_mixture: Callable[[], None],
        on_open_monodisperse: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._combo_block = False

        self._combo = QComboBox()
        for label, mode in MODE_ITEMS:
            self._combo.addItem(label, mode)
        self._combo.currentIndexChanged.connect(self._on_combo_changed)

        self._state_a_placeholder = QLabel("Select a mode; it applies after calibration.")
        self._state_a_placeholder.setWordWrap(True)

        self._params_stack = QStackedWidget()
        self._params_stack.addWidget(self._page_empty())
        self._params_stack.addWidget(self._page_monodisperse(on_open_monodisperse))
        self._params_stack.addWidget(self._page_fit_sizes(on_open_fit_sizes))
        self._params_stack.addWidget(self._page_fit_mixture(on_open_fit_mixture))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._combo)
        lay.addWidget(self._state_a_placeholder)
        lay.addWidget(self._params_stack, 0)

    @property
    def params_stack(self) -> QStackedWidget:
        return self._params_stack

    def mode_stack_index(self, mode: AnalysisMode) -> int:
        for i, (_lbl, m) in enumerate(MODE_ITEMS):
            if m == mode:
                return i
        return 0

    def sync_from_state(self, *, fit_skills_available: bool) -> None:
        in_a = self._state.current_state() == LiveviewState.A
        self._state_a_placeholder.setVisible(in_a)
        self.setEnabled(fit_skills_available)
        self._set_combo_to_state()

    def force_off(self) -> None:
        prev = self._state.analysis_enabled()
        self._state.analysis_mode = AnalysisMode.OFF
        self._set_combo_to_state()
        self.analysis_mode_changed.emit(AnalysisMode.OFF)
        if prev:
            self.modeling_enabled_changed.emit(False)

    def set_output_stack_index(self, output_stack) -> None:
        output_stack.setCurrentIndex(self.mode_stack_index(self._state.analysis_mode))

    def _set_combo_to_state(self) -> None:
        self._combo_block = True
        try:
            idx = self.mode_stack_index(self._state.analysis_mode)
            self._combo.setCurrentIndex(idx)
            self._params_stack.setCurrentIndex(idx)
        finally:
            self._combo_block = False

    def _on_combo_changed(self, _idx: int) -> None:
        if self._combo_block:
            return
        mode = self._combo.currentData()
        if not isinstance(mode, AnalysisMode):
            return
        prev = self._state.analysis_enabled()
        self._state.analysis_mode = mode
        idx = self.mode_stack_index(mode)
        self._params_stack.setCurrentIndex(idx)
        self.analysis_mode_changed.emit(mode)
        now = self._state.analysis_enabled()
        if prev != now:
            self.modeling_enabled_changed.emit(now)

    @staticmethod
    def _page_empty() -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel(""))
        return w

    @staticmethod
    def _page_monodisperse(open_wizard: Callable[[], None]) -> QWidget:
        from PyQt5.QtWidgets import QPushButton

        w = QWidget()
        lay = QVBoxLayout(w)
        btn = QPushButton("Monodisperse analysis")
        btn.setToolTip("Open the monodisperse analysis wizard (Guinier → GNOM → shape)")
        btn.clicked.connect(open_wizard)
        lay.addWidget(btn)
        lay.addStretch(1)
        return w

    @staticmethod
    def _page_fit_sizes(open_wizard: Callable[[], None]) -> QWidget:
        from PyQt5.QtWidgets import QPushButton

        w = QWidget()
        lay = QVBoxLayout(w)
        btn = QPushButton("Set fit_sizes (d(r))…")
        btn.clicked.connect(open_wizard)
        lay.addWidget(btn)
        lay.addStretch(1)
        return w

    @staticmethod
    def _page_fit_mixture(open_wizard: Callable[[], None]) -> QWidget:
        from PyQt5.QtWidgets import QPushButton

        w = QWidget()
        lay = QVBoxLayout(w)
        btn = QPushButton("Set fit_mixture (MIXTURE)…")
        btn.clicked.connect(open_wizard)
        lay.addWidget(btn)
        lay.addStretch(1)
        return w
