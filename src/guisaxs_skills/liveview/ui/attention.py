"""Slow breathing accent highlight for next-step coaching."""

from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence

from PyQt5.QtCore import QObject, QTimer
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QLineEdit, QPushButton, QToolButton, QWidget

# Match ``guisaxs_skills.ui.style`` accent.
_ACCENT = QColor("#4c8dff")
_PERIOD_MS = 1600
_TICK_MS = 40
_ALPHA_MIN = 0.35
_ALPHA_MAX = 1.0

_BUTTON_STYLE = """
QPushButton {{
    background: #182232;
    border: 2px solid {color};
    border-radius: 12px;
    padding: 7px 10px;
    color: #e7eef6;
}}
"""

_TOOL_STYLE = """
QToolButton {{
    background: #182232;
    border: 2px solid {color};
    border-radius: 12px;
    padding: 4px;
}}
"""

_LINE_STYLE = """
QLineEdit {{
    background: #0f151d;
    border: 2px solid {color};
    border-radius: 8px;
    padding: 6px;
    color: #e7eef6;
}}
"""

_FRAME_STYLE = """
QWidget {{
    border: 3px solid {color};
    border-radius: 10px;
    background-color: rgba(76, 141, 255, 0.04);
}}
"""


class AttentionPulse(QObject):
    """Pulse a visible accent border on one or more widgets (same phase)."""

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._targets: list[QWidget] = []
        self._orig_style: dict[int, str] = {}
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)

    def set_targets(self, widgets: Sequence[Optional[QWidget]] | Iterable[Optional[QWidget]]) -> None:
        clean: list[QWidget] = []
        seen: set[int] = set()
        for w in widgets:
            if w is None:
                continue
            wid = id(w)
            if wid in seen:
                continue
            seen.add(wid)
            clean.append(w)
        if clean == self._targets:
            if clean and not self._timer.isActive():
                self._timer.start()
            return
        self.clear()
        self._targets = clean
        for w in clean:
            self._orig_style[id(w)] = w.styleSheet() or ""
            pulse = getattr(w, "set_attention_pulse", None)
            if callable(pulse):
                pulse(True)
        if clean:
            self._phase = 0.0
            self._timer.start()
            self._tick()

    def clear(self) -> None:
        self._timer.stop()
        for w in self._targets:
            try:
                pulse = getattr(w, "set_attention_pulse", None)
                if callable(pulse):
                    pulse(False)
                orig = self._orig_style.pop(id(w), "")
                w.setStyleSheet(orig)
            except RuntimeError:
                self._orig_style.pop(id(w), None)
        self._targets = []
        self._orig_style.clear()

    def _color_css(self, alpha: float) -> str:
        a = max(0.0, min(1.0, float(alpha)))
        return (
            f"rgba({_ACCENT.red()}, {_ACCENT.green()}, {_ACCENT.blue()}, {a:.3f})"
        )

    def _apply_border(self, w: QWidget, color: str) -> None:
        orig = self._orig_style.get(id(w), "")
        if isinstance(w, QPushButton):
            w.setStyleSheet(orig + "\n" + _BUTTON_STYLE.format(color=color))
        elif isinstance(w, QToolButton):
            w.setStyleSheet(orig + "\n" + _TOOL_STYLE.format(color=color))
        elif isinstance(w, QLineEdit):
            w.setStyleSheet(orig + "\n" + _LINE_STYLE.format(color=color))
        else:
            w.setStyleSheet(orig + "\n" + _FRAME_STYLE.format(color=color))

    def _tick(self) -> None:
        if not self._targets:
            self._timer.stop()
            return
        self._phase = (self._phase + float(_TICK_MS) / float(_PERIOD_MS)) % 1.0
        wave = 0.5 + 0.5 * math.sin(self._phase * 2.0 * math.pi)
        alpha = _ALPHA_MIN + (_ALPHA_MAX - _ALPHA_MIN) * wave
        color = self._color_css(alpha)
        alive: list[QWidget] = []
        for w in self._targets:
            try:
                pulse = getattr(w, "set_attention_pulse", None)
                if callable(pulse):
                    pulse(True, alpha=alpha)
                else:
                    self._apply_border(w, color)
                alive.append(w)
            except RuntimeError:
                self._orig_style.pop(id(w), None)
        self._targets = alive
        if not alive:
            self._timer.stop()
