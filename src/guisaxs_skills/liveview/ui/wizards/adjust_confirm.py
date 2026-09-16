"""Confirm / dirty-close / coach highlight shared by P(r) and D(R) adjust wizards."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from PyQt5.QtCore import QObject
from PyQt5.QtWidgets import QMessageBox, QPushButton, QWidget

from ..attention import AttentionPulse

_FLOAT_KEYS = frozenset(
    {"dmax_nm", "rmax_nm", "rmin_nm", "alpha", "rg_nm"}
)
_FLOAT_EPS = 1e-6


def params_equivalent(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool:
    """Compare adjust-wizard param dicts (optional keys omitted when auto / unset)."""
    da = dict(a or {})
    db = dict(b or {})
    keys = set(da) | set(db)
    for k in keys:
        va = da.get(k)
        vb = db.get(k)
        if va is None and vb is None:
            continue
        if va is None or vb is None:
            return False
        if k in _FLOAT_KEYS:
            try:
                if abs(float(va) - float(vb)) > _FLOAT_EPS:
                    return False
            except (TypeError, ValueError):
                if va != vb:
                    return False
        else:
            if va != vb:
                return False
    return True


class AdjustConfirmController(QObject):
    """
    Owns the Confirm button: inactive when UI matches the last committed (on-disk) params,
    coach-pulsed when dirty, and gates dialog close with OK/Cancel.
    """

    def __init__(
        self,
        dialog: QWidget,
        *,
        get_current_params: Callable[[], Mapping[str, Any]],
        on_confirm: Callable[[], None],
        warning_title: str = "Unconfirmed changes",
        warning_text: str = (
            "You have unconfirmed parameter changes. Close without applying them to disk?"
        ),
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent if parent is not None else dialog)
        self._dialog = dialog
        self._get_current = get_current_params
        self._on_confirm = on_confirm
        self._warning_title = warning_title
        self._warning_text = warning_text
        self._committed: dict[str, Any] = {}
        self._controls_enabled = True
        self._close_permitted = False
        self._btn = QPushButton("Confirm", dialog)
        self._btn.setEnabled(False)
        self._btn.setToolTip("Write parameters and re-run the skill on disk")
        self._btn.clicked.connect(self._handle_confirm)
        self._attention = AttentionPulse(dialog)

    @property
    def button(self) -> QPushButton:
        return self._btn

    def set_committed(self, params: Mapping[str, Any] | None) -> None:
        self._committed = dict(params or {})
        self._close_permitted = False
        self.refresh()

    def set_controls_enabled(self, enabled: bool) -> None:
        self._controls_enabled = bool(enabled)
        self.refresh()

    def is_dirty(self) -> bool:
        return not params_equivalent(self._get_current(), self._committed)

    def refresh(self) -> None:
        dirty = self.is_dirty()
        can_confirm = dirty and self._controls_enabled
        self._btn.setEnabled(can_confirm)
        if can_confirm:
            self._attention.set_targets([self._btn])
        else:
            self._attention.clear()

    def confirm_close_if_dirty(self) -> bool:
        """Return True if the dialog may close (clean, or user OK'd discard).

        Idempotent for a single close sequence: QDialog ``reject`` + ``closeEvent``
        both call this, so the first OK arms a one-shot permit.
        """
        if self._close_permitted or not self.is_dirty():
            return True
        resp = QMessageBox.question(
            self._dialog,
            self._warning_title,
            self._warning_text,
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if resp == QMessageBox.Ok:
            self._close_permitted = True
            return True
        return False

    def _handle_confirm(self) -> None:
        self._on_confirm()
        self.set_committed(self._get_current())
