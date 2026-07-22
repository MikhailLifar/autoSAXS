from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from ....core.models import RunRequest
from ....logic.skill_catalog import discover_skills
from ....logic.session_state import SessionPathHints
from ....ui.path_field import PathField
from ....ui.run_controls import RunControls
from ....ui.skill_form import SkillForm

try:
    from autosaxs.skill.model_bodies import BODIES_SHAPES_LIST
except Exception:
    BODIES_SHAPES_LIST = [
        "cylinder",
        "dumbbell",
        "ellipsoid",
        "elliptic-cylinder",
        "hollow-cylinder",
        "hollow-sphere",
        "parallelepiped",
        "rotation-ellipsoid",
    ]


def _force_no_cache_and_fixed_output(form: SkillForm, *, outdir: str) -> None:
    try:
        cb = form._opt_fields.get("use_cache")  # type: ignore[attr-defined]
        if cb is not None:
            cb.setChecked(False)
            cb.setEnabled(False)
    except Exception:
        pass
    try:
        out = form._opt_fields.get("output_dir")  # type: ignore[attr-defined]
        if out is not None:
            out.set_text(outdir)
            out.setEnabled(False)
    except Exception:
        pass


def _remove_skill_option_field(form: SkillForm, opt_name: str) -> None:
    """Drop an option row from ``SkillForm`` (e.g. hide ``config_path`` handled elsewhere)."""
    w = form._opt_fields.pop(opt_name, None)
    if w is not None:
        form._opt_layout.removeRow(w)


def _clear_profile_positional_field(form: SkillForm, meta) -> None:
    """Clear first positional `profile` PathField (options-only wizards for liveview)."""
    if meta is None:
        return
    for i, p in enumerate(meta.positional_params):
        if p.name != "profile":
            continue
        widgets = getattr(form, "_pos_widgets", [])
        if i < len(widgets):
            w = widgets[i]
            if isinstance(w, PathField):
                w.set_text("")
        break


class FitDistancesWizardDialog(QDialog):
    def __init__(
        self,
        *,
        watchdir: Path,
        hints: SessionPathHints,
        saved_form_state: Optional[dict[str, Any]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set fit_distances")
        self.setMinimumWidth(560)
        self.resize(720, 640)
        self._watchdir = watchdir

        skills = {m.name: m for m in discover_skills()}
        meta = skills.get("fit_distances")
        self._form = SkillForm()
        self._controls = RunControls()
        self._controls.run_button.setText("Run/Apply")
        self._meta = meta

        lay = QVBoxLayout(self)
        if meta is not None:
            out = watchdir / "fit_distances"
            out.mkdir(parents=True, exist_ok=True)
            self._form.set_skill(
                meta,
                workdir=watchdir,
                default_output_dir=str(out),
                hints=hints,
                saved_state=saved_form_state,
            )
            _force_no_cache_and_fixed_output(self._form, outdir=str(out))
            _clear_profile_positional_field(self._form, meta)
            lay.addWidget(
                QLabel(
                    "Run/Apply writes fit_distances/fit_distances.conf. If analysis mode is Off, it switches to "
                    "Monodisperse p(r). With an existing profile .dat, fit_distances runs immediately; otherwise "
                    "only parameters are stored and the queue uses the integrated or subtracted curve (BD / CD)."
                )
            )
            lay.addWidget(self._form, 1)
            lay.addWidget(self._controls)
        else:
            lay.addWidget(QLabel("fit_distances skill is not available."))

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        lay.addWidget(buttons)

        self._controls.run_button.clicked.connect(self._on_run_clicked)
        self._controls.cancel_button.clicked.connect(self._on_cancel_clicked)
        self._controls.copy_cli_button.clicked.connect(self._on_copy_cli)

    def rebuild(self, hints: SessionPathHints) -> None:
        if self._meta is None:
            return
        saved = None
        par = self.parent()
        if par is not None:
            saved = getattr(par, "_fit_distances_saved_form", None)  # noqa: SLF001
        out = self._watchdir / "fit_distances"
        out.mkdir(parents=True, exist_ok=True)
        self._form.set_skill(
            self._meta,
            workdir=self._watchdir,
            default_output_dir=str(out),
            hints=hints,
            saved_state=saved,
        )
        _force_no_cache_and_fixed_output(self._form, outdir=str(out))
        _clear_profile_positional_field(self._form, self._meta)
        self._controls.run_button.setText("Run/Apply")

    def build_fit_request(self) -> RunRequest:
        if self._meta is None:
            raise RuntimeError("fit_distances skill is not available")
        return self._form.build_request()

    def set_running(self, running: bool) -> None:
        if self._meta is not None:
            self._controls.set_running(bool(running))

    def _on_run_clicked(self) -> None:
        if self._meta is None:
            return
        parent = self.parent()
        if parent is not None and hasattr(parent, "fit_distances_run_requested"):
            getattr(parent, "fit_distances_run_requested").emit()

    def _on_cancel_clicked(self) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "fit_distances_cancel_requested"):
            getattr(parent, "fit_distances_cancel_requested").emit()

    def _on_copy_cli(self) -> None:
        if self._meta is None:
            return
        try:
            req = self._form.build_request()
        except Exception as e:
            QMessageBox.critical(self, "Cannot build request", str(e))
            return
        text = "autosaxs " + " ".join(req.cli_argv())
        QGuiApplication.clipboard().setText(text)


class FitBodiesWizardDialog(QDialog):
    """Choose ATSAS BODIES shapes for liveview primitives mode (saved to model_bodies/model_bodies.conf)."""

    def __init__(
        self,
        *,
        watchdir: Path,
        saved_shapes: Optional[list[str]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set model_bodies (shapes)")
        self.setMinimumWidth(480)
        self.resize(520, 420)
        self._watchdir = watchdir

        self._list = QListWidget()
        self._list.setMinimumHeight(220)
        for name in BODIES_SHAPES_LIST:
            it = QListWidgetItem(name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            self._list.addItem(it)
        self._apply_saved_shapes(saved_shapes)

        self._controls = RunControls()
        self._controls.run_button.setText("Run/Apply")
        self._controls.copy_cli_button.setVisible(False)

        lay = QVBoxLayout(self)
        lay.addWidget(
            QLabel(
                "Select body models to fit (default: ellipsoid). Run/Apply writes model_bodies/model_bodies.conf. "
                "The live queue runs model_bodies only; --first comes from in-process Guinier (fit_guinier)."
            )
        )
        lay.addWidget(self._list, 1)
        lay.addWidget(self._controls)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        lay.addWidget(buttons)

        self._controls.run_button.clicked.connect(self._on_run_clicked)
        self._controls.cancel_button.clicked.connect(self._on_cancel_clicked)

    def _apply_saved_shapes(self, saved_shapes: Optional[list[str]]) -> None:
        want = set(saved_shapes) if saved_shapes else set()
        default_ellipsoid_only = not want
        for i in range(self._list.count()):
            it = self._list.item(i)
            name = it.text()
            if default_ellipsoid_only:
                it.setCheckState(Qt.Checked if name == "ellipsoid" else Qt.Unchecked)
            else:
                it.setCheckState(Qt.Checked if name in want else Qt.Unchecked)

    def rebuild(self, *, saved_shapes: Optional[list[str]] = None) -> None:
        self._apply_saved_shapes(saved_shapes)

    def selected_shapes(self) -> list[str]:
        names: list[str] = []
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it.checkState() == Qt.Checked:
                names.append(it.text())
        return names

    def set_running(self, running: bool) -> None:
        self._controls.set_running(bool(running))

    def _on_run_clicked(self) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "model_bodies_run_requested"):
            getattr(parent, "model_bodies_run_requested").emit()

    def _on_cancel_clicked(self) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "fit_distances_cancel_requested"):
            getattr(parent, "fit_distances_cancel_requested").emit()


class FitSizesWizardDialog(QDialog):
    def __init__(
        self,
        *,
        watchdir: Path,
        hints: SessionPathHints,
        saved_form_state: Optional[dict[str, Any]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set fit_sizes")
        self.setMinimumWidth(560)
        self.resize(720, 640)
        self._watchdir = watchdir

        skills = {m.name: m for m in discover_skills()}
        meta = skills.get("fit_sizes")
        self._form = SkillForm()
        self._controls = RunControls()
        self._controls.run_button.setText("Run/Apply")
        self._meta = meta

        lay = QVBoxLayout(self)
        if meta is not None:
            out = watchdir / "fit_sizes"
            out.mkdir(parents=True, exist_ok=True)
            self._form.set_skill(
                meta,
                workdir=watchdir,
                default_output_dir=str(out),
                hints=hints,
                saved_state=saved_form_state,
            )
            _force_no_cache_and_fixed_output(self._form, outdir=str(out))
            _clear_profile_positional_field(self._form, meta)
            lay.addWidget(
                QLabel(
                    "Run/Apply writes fit_sizes/fit_sizes.conf (options only). The live queue passes the "
                    "integrated or subtracted .dat automatically when Polydisperse d(r) mode is selected."
                )
            )
            lay.addWidget(self._form, 1)
            lay.addWidget(self._controls)
        else:
            lay.addWidget(QLabel("fit_sizes skill is not available."))

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        lay.addWidget(buttons)

        self._controls.run_button.clicked.connect(self._on_run_clicked)
        self._controls.cancel_button.clicked.connect(self._on_cancel_clicked)
        self._controls.copy_cli_button.clicked.connect(self._on_copy_cli)

    def rebuild(self, hints: SessionPathHints) -> None:
        if self._meta is None:
            return
        saved = None
        par = self.parent()
        if par is not None:
            saved = getattr(par, "_fit_sizes_saved_form", None)  # noqa: SLF001
        out = self._watchdir / "fit_sizes"
        out.mkdir(parents=True, exist_ok=True)
        self._form.set_skill(
            self._meta,
            workdir=self._watchdir,
            default_output_dir=str(out),
            hints=hints,
            saved_state=saved,
        )
        _force_no_cache_and_fixed_output(self._form, outdir=str(out))
        _clear_profile_positional_field(self._form, self._meta)

    def build_fit_sizes_request(self) -> RunRequest:
        if self._meta is None:
            raise RuntimeError("fit_sizes skill is not available")
        return self._form.build_request()

    def set_running(self, running: bool) -> None:
        if self._meta is not None:
            self._controls.set_running(bool(running))

    def _on_run_clicked(self) -> None:
        if self._meta is None:
            return
        parent = self.parent()
        if parent is not None and hasattr(parent, "fit_sizes_run_requested"):
            getattr(parent, "fit_sizes_run_requested").emit()

    def _on_cancel_clicked(self) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "fit_distances_cancel_requested"):
            getattr(parent, "fit_distances_cancel_requested").emit()

    def _on_copy_cli(self) -> None:
        if self._meta is None:
            return
        try:
            req = self._form.build_request()
        except Exception as e:
            QMessageBox.critical(self, "Cannot build request", str(e))
            return
        text = "autosaxs " + " ".join(req.cli_argv())
        QGuiApplication.clipboard().setText(text)


# Written under watchdir/mixture/ for persistence (``model_mixture:`` section; queue uses CLI options).
LIVEVIEW_MIXTURE_YML_NAME = "liveview_mixture.yml"


def _mixture_skill_options_from_form(form: SkillForm) -> dict[str, Any]:
    """q range and other skill-form options (excludes output_dir / use_cache / config_path)."""
    try:
        st = form.state()
    except Exception:
        return {}
    opts = (st.get("options") or {}).copy()
    out: dict[str, Any] = {}
    skip = frozenset({"output_dir", "use_cache", "config_path"})
    for key, value in opts.items():
        if key in skip or value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        out[str(key)] = value
    return out


def model_mixture_run_options_from_wizard(wizard: FitMixtureWizardDialog) -> dict[str, Any]:
    """MIXTURE spinbox params plus q-range (and related) fields from the skill form."""
    merged = dict(wizard.mixture_params())
    merged.update(_mixture_skill_options_from_form(wizard._form))  # type: ignore[attr-defined]
    return merged


class FitMixtureWizardDialog(QDialog):
    """MIXTURE model parameters + model_mixture options (q range); profile and config path are implicit."""

    def __init__(
        self,
        *,
        watchdir: Path,
        hints: SessionPathHints,
        saved_form_state: Optional[dict[str, Any]] = None,
        saved_mixture_params: Optional[dict[str, Any]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set model_mixture (MIXTURE)")
        self.setMinimumWidth(560)
        self.resize(720, 640)
        self._watchdir = watchdir

        skills = {m.name: m for m in discover_skills()}
        meta = skills.get("model_mixture")
        self._form = SkillForm()
        self._controls = RunControls()
        self._controls.run_button.setText("Run/Apply")
        self._meta = meta

        self._mix_group = QGroupBox("MIXTURE parameters")
        mf = QFormLayout(self._mix_group)
        self._sp_max_nph = QSpinBox()
        self._sp_max_nph.setRange(1, 10)
        self._sp_max_nph.setValue(3)
        self._sp_maxit = QSpinBox()
        self._sp_maxit.setRange(1, 100000)
        self._sp_maxit.setValue(100)
        self._sp_r_min = QDoubleSpinBox()
        self._sp_r_min.setRange(0.01, 1e6)
        self._sp_r_min.setDecimals(4)
        self._sp_r_min.setValue(0.1)
        self._sp_r_max = QDoubleSpinBox()
        self._sp_r_max.setRange(0.01, 1e6)
        self._sp_r_max.setDecimals(4)
        self._sp_r_max.setValue(12.0)
        self._sp_poly_min = QDoubleSpinBox()
        self._sp_poly_min.setRange(0.001, 1e6)
        self._sp_poly_min.setDecimals(4)
        self._sp_poly_min.setValue(0.05)
        self._sp_poly_max = QDoubleSpinBox()
        self._sp_poly_max.setRange(0.001, 1e6)
        self._sp_poly_max.setDecimals(4)
        self._sp_poly_max.setValue(6.0)
        mf.addRow("max_nph (phases)", self._sp_max_nph)
        mf.addRow("maxit", self._sp_maxit)
        mf.addRow("r_min (nm)", self._sp_r_min)
        mf.addRow("r_max (nm)", self._sp_r_max)
        mf.addRow("poly_min (nm)", self._sp_poly_min)
        mf.addRow("poly_max (nm)", self._sp_poly_max)

        lay = QVBoxLayout(self)
        if meta is not None:
            out = watchdir / "mixture"
            out.mkdir(parents=True, exist_ok=True)
            self._form.set_skill(
                meta,
                workdir=watchdir,
                default_output_dir=str(out),
                hints=hints,
                saved_state=saved_form_state,
            )
            _force_no_cache_and_fixed_output(self._form, outdir=str(out))
            _clear_profile_positional_field(self._form, meta)
            _remove_skill_option_field(self._form, "config_path")
            lay.addWidget(
                QLabel(
                    "Set q_min_nm / q_max_nm below to limit the fit range (recommended). Run/Apply saves options "
                    "for the live queue; mixture/liveview_mixture.yml is optional persistence. Config file is "
                    "not required — bundled MIXTURE defaults apply when spinboxes are left at defaults."
                )
            )
            lay.addWidget(self._mix_group)
            lay.addWidget(self._form, 1)
            lay.addWidget(self._controls)
            if saved_mixture_params:
                self.set_mixture_params(saved_mixture_params)
        else:
            lay.addWidget(QLabel("model_mixture skill is not available."))

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        lay.addWidget(buttons)

        if meta is not None:
            self._controls.run_button.clicked.connect(self._on_run_clicked)
            self._controls.cancel_button.clicked.connect(self._on_cancel_clicked)
            self._controls.copy_cli_button.clicked.connect(self._on_copy_cli)

    def mixture_params(self) -> dict[str, Any]:
        return {
            "max_nph": int(self._sp_max_nph.value()),
            "maxit": int(self._sp_maxit.value()),
            "r_min": float(self._sp_r_min.value()),
            "r_max": float(self._sp_r_max.value()),
            "poly_min": float(self._sp_poly_min.value()),
            "poly_max": float(self._sp_poly_max.value()),
        }

    def set_mixture_params(self, d: dict[str, Any]) -> None:
        if not isinstance(d, dict):
            return
        try:
            if "max_nph" in d:
                self._sp_max_nph.setValue(int(d["max_nph"]))
            if "maxit" in d:
                self._sp_maxit.setValue(int(d["maxit"]))
            if "r_min" in d:
                self._sp_r_min.setValue(float(d["r_min"]))
            if "r_max" in d:
                self._sp_r_max.setValue(float(d["r_max"]))
            if "poly_min" in d:
                self._sp_poly_min.setValue(float(d["poly_min"]))
            if "poly_max" in d:
                self._sp_poly_max.setValue(float(d["poly_max"]))
        except (TypeError, ValueError):
            pass

    def mixture_config_yaml_path(self) -> Path:
        d = self._watchdir / "mixture"
        d.mkdir(parents=True, exist_ok=True)
        return (d / LIVEVIEW_MIXTURE_YML_NAME).resolve()

    def write_mixture_config_yaml(self) -> Path:
        path = self.mixture_config_yaml_path()
        doc = {"model_mixture": model_mixture_run_options_from_wizard(self)}
        path.write_text(yaml.safe_dump(doc, sort_keys=True, allow_unicode=True), encoding="utf-8")
        return path

    def rebuild(self, hints: SessionPathHints) -> None:
        if self._meta is None:
            return
        par = self.parent()
        saved = None
        mix_saved: Optional[dict[str, Any]] = None
        if par is not None:
            saved = getattr(par, "_model_mixture_saved_form", None)  # noqa: SLF001
            mix_saved = getattr(par, "_model_mixture_saved_mixture_params", None)  # noqa: SLF001
        out = self._watchdir / "mixture"
        out.mkdir(parents=True, exist_ok=True)
        self._form.set_skill(
            self._meta,
            workdir=self._watchdir,
            default_output_dir=str(out),
            hints=hints,
            saved_state=saved,
        )
        _force_no_cache_and_fixed_output(self._form, outdir=str(out))
        _clear_profile_positional_field(self._form, self._meta)
        _remove_skill_option_field(self._form, "config_path")
        self._controls.run_button.setText("Run/Apply")
        if isinstance(mix_saved, dict) and mix_saved:
            self.set_mixture_params(mix_saved)

    def build_model_mixture_request(self) -> RunRequest:
        if self._meta is None:
            raise RuntimeError("model_mixture skill is not available")
        req = self._form.build_request()
        opts = model_mixture_run_options_from_wizard(self)
        opts.pop("output_dir", None)
        opts.pop("use_cache", None)
        opts.pop("config_path", None)
        opts["use_cache"] = False
        out = (req.options or {}).get("output_dir")
        if isinstance(out, str) and out.strip():
            opts["output_dir"] = out.strip()
        return RunRequest(skill_name=req.skill_name, positional=list(req.positional), options=opts)

    def set_running(self, running: bool) -> None:
        if self._meta is not None:
            self._controls.set_running(bool(running))

    def _on_run_clicked(self) -> None:
        if self._meta is None:
            return
        parent = self.parent()
        if parent is not None and hasattr(parent, "model_mixture_run_requested"):
            getattr(parent, "model_mixture_run_requested").emit()

    def _on_cancel_clicked(self) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "fit_distances_cancel_requested"):
            getattr(parent, "fit_distances_cancel_requested").emit()

    def _on_copy_cli(self) -> None:
        if self._meta is None:
            return
        try:
            self.write_mixture_config_yaml()
            req = self.build_model_mixture_request()
        except Exception as e:
            QMessageBox.critical(self, "Cannot build request", str(e))
            return
        text = "autosaxs " + " ".join(req.cli_argv())
        QGuiApplication.clipboard().setText(text)
