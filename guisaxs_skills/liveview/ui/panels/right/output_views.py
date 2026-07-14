from __future__ import annotations

import os
from pathlib import Path

from PyQt5.QtWidgets import QLabel, QStackedWidget, QVBoxLayout, QWidget

from .....ui.preview_panel import PreviewPanel
from ....services.artifacts import norm_artifact_path
from autosaxs.skill.gnom_fit_common import failure_message_from_result


class AnalysisOutputViews(QWidget):
    """Per-mode analysis output previews."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stack = QStackedWidget(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._stack)

        self._stack.addWidget(self._build_off())
        self._stack.addWidget(self._build_monodisperse_placeholder())
        self._stack.addWidget(self._build_dr())
        self._stack.addWidget(self._build_mixture())

    def stack(self):
        return self._stack

    def set_mode_index(self, idx: int) -> None:
        self._stack.setCurrentIndex(idx)

    def _build_monodisperse_placeholder(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self._mono_hint = QLabel("—")
        self._mono_hint.setWordWrap(True)
        self._mono_status = QLabel("—")
        self._mono_status.setWordWrap(True)
        lay.addWidget(self._mono_hint)
        lay.addWidget(self._mono_status, 1)
        lay.addStretch(1)
        return w

    def update_monodisperse_summary(self, *, hint: str = "", status: str = "") -> None:
        if hasattr(self, "_mono_hint"):
            self._mono_hint.setText(hint or "—")
        if hasattr(self, "_mono_status"):
            self._mono_status.setText(status or "—")

    def _build_off(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self._hint_off = QLabel("—")
        self._hint_off.setWordWrap(True)
        lay.addWidget(self._hint_off)
        lay.addStretch(1)
        return w

    def _build_dr(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self._hint_fit_dr = QLabel("—")
        self._hint_fit_dr.setWordWrap(True)
        self._fit_plot_dr = PreviewPanel()
        self._fit_plot_dr.setMinimumHeight(120)
        self._hint_dr = QLabel("—")
        self._hint_dr.setWordWrap(True)
        self._dr_plot = PreviewPanel()
        self._dr_plot.setMinimumHeight(120)
        lay.addWidget(QLabel("Fit"))
        lay.addWidget(self._hint_fit_dr)
        lay.addWidget(self._fit_plot_dr, 1)
        lay.addWidget(QLabel("d(r)"))
        lay.addWidget(self._hint_dr)
        lay.addWidget(self._dr_plot, 1)
        return w

    def _build_mixture(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self._hint_mix_c = QLabel("—")
        self._hint_mix_c.setWordWrap(True)
        self._mix_comp = PreviewPanel()
        self._mix_comp.setMinimumHeight(120)
        self._hint_mix_d = QLabel("—")
        self._hint_mix_d.setWordWrap(True)
        self._mix_dist = PreviewPanel()
        self._mix_dist.setMinimumHeight(120)
        lay.addWidget(QLabel("Cmp"))
        lay.addWidget(self._hint_mix_c)
        lay.addWidget(self._mix_comp, 1)
        lay.addWidget(QLabel("Dist"))
        lay.addWidget(self._hint_mix_d)
        lay.addWidget(self._mix_dist, 1)
        return w

    def clear_previews(self) -> None:
        self._fit_plot_dr.show_path("")
        self._hint_fit_dr.setText("—")
        self._hint_fit_dr.setVisible(True)
        self._dr_plot.show_path("")
        self._hint_dr.setText("—")
        self._hint_dr.setVisible(True)
        self._mix_comp.show_path("")
        self._hint_mix_c.setVisible(True)
        self._mix_dist.show_path("")
        self._hint_mix_d.setVisible(True)

    def ingest_skill_result(self, result: dict, *, watchdir: Path, skill_name: str = "") -> None:
        if not isinstance(result, dict):
            return
        if result.get("comparison_path") or result.get("distributions_path"):
            self._apply_mixture_outputs(result)
            return
        if self._result_targets_fit_sizes(result):
            if self._looks_like_atsas_fit_failure(result):
                self._apply_fit_sizes_failure(failure_message_from_result(result, skill_id="fit_sizes"))
            else:
                self._apply_fit_sizes_outputs(result)
            return
        _ = watchdir, skill_name

    @staticmethod
    def _looks_like_atsas_fit_failure(result: dict) -> bool:
        ok = result.get("atsas_fit_ok")
        if ok is False:
            return True
        if isinstance(ok, str) and ok.strip().lower() in ("false", "0", "no"):
            return True
        gf = result.get("gnom_failed")
        if gf is True:
            return True
        if isinstance(gf, str) and gf.strip().lower() in ("true", "1", "yes"):
            return True
        msg = result.get("failure_message")
        return isinstance(msg, str) and bool(msg.strip())

    @staticmethod
    def _result_targets_fit_sizes(result: dict) -> bool:
        return "best_dr_png_path" in result or "dr_csv_path" in result

    def _apply_fit_sizes_failure(self, message: str) -> None:
        text = (message or "").strip() or "GNOM failed."
        self._fit_plot_dr.show_path("")
        self._hint_fit_dr.setText(text)
        self._hint_fit_dr.setVisible(True)
        self._dr_plot.show_path("")
        self._hint_dr.setText(text)
        self._hint_dr.setVisible(True)

    def _apply_fit_sizes_outputs(self, result: dict) -> None:
        fp = norm_artifact_path(result.get("fit_vs_exp_png_path"))
        dp = norm_artifact_path(result.get("best_dr_png_path"))
        if fp and os.path.isfile(fp):
            self._hint_fit_dr.setVisible(False)
            self._fit_plot_dr.show_path(fp)
        else:
            self._fit_plot_dr.show_path("")
            self._hint_fit_dr.setText("—")
            self._hint_fit_dr.setVisible(True)
        if dp and os.path.isfile(dp):
            self._hint_dr.setVisible(False)
            self._dr_plot.show_path(dp)
        else:
            self._dr_plot.show_path("")
            self._hint_dr.setText("—")
            self._hint_dr.setVisible(True)

    def _apply_mixture_outputs(self, result: dict) -> None:
        c = norm_artifact_path(result.get("comparison_path"))
        d = norm_artifact_path(result.get("distributions_path"))
        if c and os.path.isfile(c):
            self._hint_mix_c.setVisible(False)
            self._mix_comp.show_path(c)
        else:
            self._mix_comp.show_path("")
            self._hint_mix_c.setVisible(True)
        if d and os.path.isfile(d):
            self._hint_mix_d.setVisible(False)
            self._mix_dist.show_path(d)
        else:
            self._mix_dist.show_path("")
            self._hint_mix_d.setVisible(True)
