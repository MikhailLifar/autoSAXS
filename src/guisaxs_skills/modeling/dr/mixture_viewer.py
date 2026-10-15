"""Advanced MIXTURE results viewer (model combo + mixture/component previews)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...liveview.ui.panels.right.polydisperse.plots import MixtureDistPlot
from ...ui.passport_format import format_display_number
from ...ui.style import COLOR_ACCENT, COLOR_MUTED_TEXT, configure_readonly_copyable_table
from ..catalogs.mixture_models import MixtureModelCatalog, MixtureModelEntry, build_mixture_model_catalog
from ..plots import MplViewerDialog

# Match ``QListWidget::item:selected`` / panel chrome in ``ui/style.py`` (dark theme).
_COLOR_SELECTION_SURFACE = "#1b2a3d"
_COLOR_PANEL_BORDER = "#2a3646"
_PREVIEW_OBJECT_NAME = "mixtureSelectablePreview"


def _caption_table(rows: list[tuple[str, str]], parent: Optional[QWidget] = None) -> QTableWidget:
    tbl = QTableWidget(len(rows), 2, parent)
    tbl.setHorizontalHeaderLabels(["", ""])
    tbl.horizontalHeader().setVisible(False)
    tbl.verticalHeader().setVisible(False)
    configure_readonly_copyable_table(tbl)
    tbl.setShowGrid(False)
    tbl.setMaximumHeight(72)
    tbl.setMinimumHeight(54)
    for i, (metric, value) in enumerate(rows):
        tbl.setItem(i, 0, QTableWidgetItem(metric))
        tbl.setItem(i, 1, QTableWidgetItem(value))
    tbl.resizeColumnsToContents()
    tbl.resizeRowsToContents()
    return tbl


class _SelectablePreview(QFrame):
    """Left-column mixture/component preview: click selects the right large view only."""

    def __init__(
        self,
        *,
        title: str,
        view_key: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.view_key = view_key
        self.setObjectName(_PREVIEW_OBJECT_NAME)
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)
        self._selected = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)
        self._title = QLabel(title)
        self._title.setStyleSheet(f"color: {COLOR_MUTED_TEXT};")
        lay.addWidget(self._title)
        self.plot = MixtureDistPlot(figsize=(2.4, 1.6))
        self.plot.setMinimumHeight(100)
        self.plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay.addWidget(self.plot, 1)
        self._caption_host = QWidget()
        self._caption_lay = QVBoxLayout(self._caption_host)
        self._caption_lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._caption_host, 0)
        self._on_select = None
        self.plot.mpl_connect("button_press_event", self._on_plot_click)
        self._apply_chrome()

    def set_handlers(self, *, on_select) -> None:
        """Wire select-only; left previews must not open the standard enlarge viewer."""
        self._on_select = on_select

    def set_caption_rows(self, rows: list[tuple[str, str]]) -> None:
        while self._caption_lay.count():
            item = self._caption_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if rows:
            self._caption_lay.addWidget(_caption_table(rows, self))

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self._apply_chrome()

    def _apply_chrome(self) -> None:
        # Object-name scoped so QSS does not cascade onto child QTableWidget (also a QFrame)
        # or the matplotlib canvas — light ``QFrame { background: … }`` was whitening
        # captions (invisible light text) and washing the selected card.
        if self._selected:
            self.setStyleSheet(
                f"QFrame#{_PREVIEW_OBJECT_NAME} {{"
                f" border: 2px solid {COLOR_ACCENT}; border-radius: 4px;"
                f" background: {_COLOR_SELECTION_SURFACE}; }}"
            )
        else:
            self.setStyleSheet(
                f"QFrame#{_PREVIEW_OBJECT_NAME} {{"
                f" border: 1px solid {_COLOR_PANEL_BORDER}; border-radius: 4px;"
                f" background: transparent; }}"
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._on_select is not None:
            self._on_select(self.view_key)
        super().mousePressEvent(event)

    def _on_plot_click(self, ev: object) -> None:
        if getattr(ev, "inaxes", None) is None or int(getattr(ev, "button", 0)) != 1:
            return
        if self._on_select is not None:
            self._on_select(self.view_key)


class MixtureAdvancedViewerDialog(QDialog):
    """
    2-column MIXTURE results viewer.

    Left (1): mixture + component previews (select which view).
    Right (2): model combo (top) + large square selected view (bottom).
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MIXTURE advanced results")
        self.resize(1280, 820)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self._catalog: Optional[MixtureModelCatalog] = None
        self._entry: Optional[MixtureModelEntry] = None
        self._view_key = "mixture"
        self._left_cards: Dict[str, _SelectablePreview] = {}
        self._enlarge_dlg: Optional[MplViewerDialog] = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        # ---- Left column ----
        left_wrap = QWidget()
        left = QVBoxLayout(left_wrap)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(6)
        left.addWidget(QLabel("Views (mixture / components)"))
        self._left_scroll = QScrollArea()
        self._left_scroll.setWidgetResizable(True)
        self._left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._left_body = QWidget()
        self._left_lay = QVBoxLayout(self._left_body)
        self._left_lay.setContentsMargins(2, 2, 2, 2)
        self._left_lay.setSpacing(8)
        self._left_lay.addStretch(1)
        self._left_scroll.setWidget(self._left_body)
        left.addWidget(self._left_scroll, 1)

        # ---- Right column ----
        right_wrap = QWidget()
        right = QVBoxLayout(right_wrap)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Model:"))
        self._combo = QComboBox()
        self._combo.setMinimumWidth(280)
        self._combo.currentIndexChanged.connect(self._on_combo)
        model_row.addWidget(self._combo, 1)
        right.addLayout(model_row)

        self._big_title = QLabel("Selected view")
        self._big_title.setStyleSheet(f"color: {COLOR_MUTED_TEXT};")
        right.addWidget(self._big_title)
        self._big = MixtureDistPlot(figsize=(6.0, 6.0))
        self._big.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._big.setMinimumSize(420, 420)
        right.addWidget(self._big, 1)
        self._big.mpl_connect("button_press_event", self._on_big_click)

        root.addWidget(left_wrap, 1)
        root.addWidget(right_wrap, 2)

    def present(
        self,
        *,
        subdir: str,
        best_label: str = "",
        r_min_ang: float = 5.0,
        r_max_ang: float = 120.0,
        select_key: str = "",
    ) -> None:
        cat = build_mixture_model_catalog(
            subdir,
            best_label=best_label,
            r_min_ang=r_min_ang,
            r_max_ang=r_max_ang,
        )
        self.set_catalog(cat, select_key=select_key or cat.best_key)
        self.show()
        self.raise_()
        self.activateWindow()

    def set_catalog(self, catalog: MixtureModelCatalog, *, select_key: str = "") -> None:
        self._catalog = catalog
        self._combo.blockSignals(True)
        self._combo.clear()
        self._entries_by_index: List[MixtureModelEntry] = []
        select_idx = 0
        key = (select_key or catalog.best_key or "").strip()
        for i, entry in enumerate(catalog.entries):
            self._combo.addItem(entry.label)
            self._entries_by_index.append(entry)
            if entry.key == key:
                select_idx = i
        self._combo.setCurrentIndex(select_idx if self._entries_by_index else -1)
        self._combo.blockSignals(False)
        if not self._entries_by_index:
            self._entry = None
            self._clear_left()
            self._big.clear_plot()
            return
        self._load_entry(self._entries_by_index[select_idx])

    def _on_combo(self, *_a) -> None:
        idx = self._combo.currentIndex()
        if idx < 0 or idx >= len(getattr(self, "_entries_by_index", [])):
            return
        self._load_entry(self._entries_by_index[idx])

    def _clear_left(self) -> None:
        while self._left_lay.count():
            item = self._left_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._left_cards = {}
        self._left_lay.addStretch(1)

    def _load_entry(self, entry: MixtureModelEntry) -> None:
        self._entry = entry
        self._view_key = "mixture"
        self._rebuild_left_cards(entry)
        self._refresh_big()

    def _rebuild_left_cards(self, entry: MixtureModelEntry) -> None:
        from autosaxs.skill.model_mixture.mixture import (
            component_distribution_curves,
            distribution_curve_for_model,
        )

        self._clear_left()
        cat = self._catalog
        r_min = float(cat.r_min_ang) if cat else 5.0
        r_max = float(cat.r_max_ang) if cat else 120.0
        row = entry.row

        mix = _SelectablePreview(title="Mixture D(R)", view_key="mixture", parent=self._left_body)
        mix.set_handlers(on_select=self._select_view)
        mix.plot.plot_from_model_row(row, r_min_ang=r_min, r_max_ang=r_max, label=entry.csv_label)
        # Mixture caption: n_phases / dist / BIC_χ²
        mix_rows = []
        if entry.n_phases:
            mix_rows.append(("n_phases", str(entry.n_phases)))
        if entry.dist:
            mix_rows.append(("dist", entry.dist))
        if entry.bic_chi2 is not None:
            mix_rows.append(("BIC_χ²", format_display_number(entry.bic_chi2)))
        mix.set_caption_rows(mix_rows[:3])
        self._left_lay.insertWidget(self._left_lay.count() - 1, mix)
        self._left_cards["mixture"] = mix

        comps = component_distribution_curves(row, r_min_ang=r_min, r_max_ang=r_max)
        for c in comps:
            key = f"comp:{c['index']}"
            card = _SelectablePreview(
                title=f"Component {c['index']}",
                view_key=key,
                parent=self._left_body,
            )
            card.set_handlers(on_select=self._select_view)
            self._plot_component(card.plot, c, label=f"φ{c['index']}")
            card.set_caption_rows(
                [
                    ("volume fraction", format_display_number(c["vol"])),
                    ("center (nm)", format_display_number(c["Rout_nm"])),
                    ("spread (nm)", format_display_number(c["dRout_nm"])),
                ]
            )
            self._left_lay.insertWidget(self._left_lay.count() - 1, card)
            self._left_cards[key] = card

        # Keep total curve available for enlarge payload consistency.
        _ = distribution_curve_for_model
        self._sync_selection_chrome()

    @staticmethod
    def _plot_component(plot: MixtureDistPlot, comp: dict[str, Any], *, label: str) -> None:
        R_nm = np.asarray(comp["R_nm"], dtype=float)
        P = np.asarray(comp["P"], dtype=float)
        m = np.isfinite(R_nm) & np.isfinite(P)
        if not m.any():
            plot.clear_plot()
            return
        plot._click_path = None
        plot._click_viewer = "mixture_dist"
        plot._click_payload = {
            "component": dict(comp),
            "label": label,
            "R_nm": R_nm,
            "P": P,
        }
        plot._ax.clear()
        plot._ax.plot(R_nm[m], P[m], "C1-", lw=1.4, label=label)
        plot._ax.set_xlabel("R (nm)")
        plot._ax.set_ylabel("P(R)")
        plot._ax.legend(fontsize=7)
        plot._ax.grid(True, alpha=0.2)
        plot._fig.tight_layout()
        plot.draw_idle()
        plot.setCursor(Qt.PointingHandCursor)

    def _select_view(self, view_key: str) -> None:
        self._view_key = view_key
        self._sync_selection_chrome()
        self._refresh_big()

    def _sync_selection_chrome(self) -> None:
        for key, card in self._left_cards.items():
            card.set_selected(key == self._view_key)

    def _refresh_big(self) -> None:
        entry = self._entry
        cat = self._catalog
        if entry is None or cat is None:
            self._big.clear_plot()
            return
        r_min = float(cat.r_min_ang)
        r_max = float(cat.r_max_ang)
        title = "Mixture"
        if self._view_key == "mixture":
            self._big.plot_from_model_row(
                entry.row, r_min_ang=r_min, r_max_ang=r_max, label=entry.csv_label
            )
            title = f"Mixture — {entry.csv_label}"
        elif self._view_key.startswith("comp:"):
            from autosaxs.skill.model_mixture.mixture import component_distribution_curves

            try:
                idx = int(self._view_key.split(":", 1)[1])
            except ValueError:
                idx = -1
            comps = component_distribution_curves(entry.row, r_min_ang=r_min, r_max_ang=r_max)
            comp = next((c for c in comps if int(c["index"]) == idx), None)
            if comp is None:
                self._big.clear_plot()
                return
            self._plot_component(self._big, comp, label=f"φ{idx}")
            title = f"Component {idx} — {entry.csv_label}"
        else:
            self._big.clear_plot()
            return
        self._big_title.setText(title)

    def _on_big_click(self, ev: object) -> None:
        if getattr(ev, "inaxes", None) is None or int(getattr(ev, "button", 0)) != 1:
            return
        self._enlarge_view(self._view_key)

    def _enlarge_view(self, view_key: str) -> None:
        entry = self._entry
        cat = self._catalog
        if entry is None or cat is None:
            return
        r_min = float(cat.r_min_ang)
        r_max = float(cat.r_max_ang)

        def _make() -> MixtureDistPlot:
            return MixtureDistPlot(figsize=(8, 6))

        if view_key == "mixture":

            def _populate(plot: MixtureDistPlot) -> None:
                plot.plot_from_model_row(
                    entry.row, r_min_ang=r_min, r_max_ang=r_max, label=entry.csv_label
                )

            title = f"MIXTURE D(R) — {entry.csv_label}"
        elif view_key.startswith("comp:"):
            from autosaxs.skill.model_mixture.mixture import component_distribution_curves

            try:
                idx = int(view_key.split(":", 1)[1])
            except ValueError:
                return
            comps = component_distribution_curves(entry.row, r_min_ang=r_min, r_max_ang=r_max)
            comp = next((c for c in comps if int(c["index"]) == idx), None)
            if comp is None:
                return

            def _populate(plot: MixtureDistPlot) -> None:
                self._plot_component(plot, comp, label=f"φ{idx}")

            title = f"Component {idx} D(R) — {entry.csv_label}"
        else:
            return

        if self._enlarge_dlg is None:
            plot = _make()
            self._enlarge_dlg = MplViewerDialog(title=title, plot=plot, parent=self)
        _populate(self._enlarge_dlg.plot)
        self._enlarge_dlg.present(title=title)
