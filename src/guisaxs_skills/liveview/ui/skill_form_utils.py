from __future__ import annotations

from typing import Optional

from PyQt5.QtWidgets import QLineEdit, QWidget

from ...ui.run_controls import RunControls
from ...ui.skill_form import SkillForm

# Liveview defaults (skill applies these when omitted; form shows them explicitly).
_CALIBRATE_MASK_MODE_DISPLAY = "f - from file"
_CALIBRATE_CALIBRANT_DEFAULT = "AgBh"
_CALIBRATE_WAVELENGTH_A_DEFAULT = "1.445"


def liveview_skill_form() -> SkillForm:
    return SkillForm(show_copy_inputs=False, show_use_cache=False, show_config_path=False)


def liveview_run_controls() -> RunControls:
    return RunControls(show_copy_cli=False)


def force_fixed_output(form: SkillForm, *, outdir: str) -> None:
    try:
        out = form._opt_fields.get("output_dir")  # type: ignore[attr-defined]
        if out is not None:
            out.set_text(outdir)
            _hide_form_field(form, out)
    except Exception:
        pass


def prepare_liveview_calibrate_form(form: SkillForm, *, outdir: str) -> None:
    """Hide non-interactive / rarely used options; set friendly labels and defaults."""
    force_fixed_output(form, outdir=outdir)
    _hide_option_by_name(form, "dist_guess")

    _set_option_label(form, "mask_mode", "mask mode")
    _set_option_label(form, "calibrant", "calibrant")
    _set_option_label(form, "wavelength", "wavelength, A")

    _set_line_default(form, "mask_mode", _CALIBRATE_MASK_MODE_DISPLAY)
    _set_line_default(form, "calibrant", _CALIBRATE_CALIBRANT_DEFAULT)
    _set_line_default(form, "wavelength", _CALIBRATE_WAVELENGTH_A_DEFAULT)


_SUBTRACT_HIDDEN_OPTIONS = frozenset(
    {
        "method",
        "sample_form",
        "buffer_form",
        "point_match_factor",
        "scaling_factor",
    }
)


def prepare_liveview_subtract_form(form: SkillForm, *, outdir: str) -> None:
    """Hide advanced subtract knobs; keep buffer path + q-match window visible."""
    force_fixed_output(form, outdir=outdir)
    for name in _SUBTRACT_HIDDEN_OPTIONS:
        _hide_option_by_name(form, name)
    _set_option_label(form, "q_min", "q min")
    _set_option_label(form, "q_max", "q max")


def normalize_calibrate_mask_mode(raw: Optional[str]) -> Optional[str]:
    """Map friendly form text like ``f - from file`` to a skill-accepted token."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    key = s.split("-", 1)[0].strip().lower()
    aliases = {
        "f": "f",
        "from_file": "f",
        "a": "a",
        "auto": "a",
        "c": "c",
        "combined": "c",
    }
    if key in aliases:
        return aliases[key]
    low = s.lower()
    if "from_file" in low or low.startswith("f"):
        return "f"
    if "combined" in low or low.startswith("c"):
        return "c"
    if "auto" in low or low.startswith("a"):
        return "a"
    return s


def _hide_option_by_name(form: SkillForm, name: str) -> None:
    w = form._opt_fields.get(name)  # type: ignore[attr-defined]
    if w is not None:
        _hide_form_field(form, w)


def _hide_form_field(form: SkillForm, widget: QWidget) -> None:
    widget.setVisible(False)
    widget.setEnabled(False)
    try:
        lbl = form._opt_layout.labelForField(widget)  # type: ignore[attr-defined]
        if lbl is not None:
            lbl.setVisible(False)
    except Exception:
        pass


def _set_option_label(form: SkillForm, name: str, label: str) -> None:
    w = form._opt_fields.get(name)  # type: ignore[attr-defined]
    if w is None:
        return
    try:
        lbl = form._opt_layout.labelForField(w)  # type: ignore[attr-defined]
        if lbl is not None:
            lbl.setText(label)
    except Exception:
        pass


def _set_line_default(form: SkillForm, name: str, value: str) -> None:
    w = form._opt_fields.get(name)  # type: ignore[attr-defined]
    if isinstance(w, QLineEdit) and not w.text().strip():
        w.setText(value)
