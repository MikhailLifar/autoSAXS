"""Shape modeling main window (BODIES / DAMMIF / DENSS)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import QFileSystemWatcher, Qt
from PyQt5.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...liveview.services.artifacts import bodies_best_fit
from ...liveview.ui.panels.right.monodisperse.plots import PrPlot, ShapeFitPlot
from ...liveview.ui.widgets.viewer_3d import LiveviewViewer3D
from ...ui.passport_table import PassportTableWidget
from ...ui.path_field import PathField
from ...ui.run_status_bar import RunStatusBar
from ..catalogs.dam_models import build_dam_model_catalog
from ..catalogs.denss_models import build_denss_model_catalog
from ..context import ModelingContext
from ..freeze_ui import (
    enter_context_freeze,
    install_context_freeze,
    notify_ready_for_context_if_deferred,
)
from ..ipc import ModelingIpcChild
from ..plots import FitResidualsPlot, ModelingPlotClickRouter
from ..run_params import (
    analysis_root_from_modeling_path,
    conventional_sample_modeling_dir,
    infer_n_runs_from_disk,
    infer_shape_mode_from_disk,
    read_run_params,
    resolve_sample_modeling_dir,
    skill_batch_output_dir,
)
from ..progress_parse import ProgressStderrBuffer
from ..runtime import ModelingRuntime

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

_DEFAULT_SHAPES = ("ellipsoid", "cylinder", "dumbbell")


class ShapeModelingWindow(QMainWindow):
    def __init__(
        self,
        ctx: ModelingContext,
        *,
        ipc: Optional[ModelingIpcChild] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("GUISAXS Shape Modeling")
        # Wide default so the middle 3D column can stay essentially square.
        self.resize(1680, 900)
        self._ipc = ipc
        self._ctx = ModelingContext()
        self._runtime = ModelingRuntime(workdir=Path.cwd(), parent=self)
        self._runtime.started.connect(self._on_started)
        self._runtime.finished.connect(self._on_finished)
        self._runtime.stderr.connect(self._on_stderr)
        self._progress_buf = ProgressStderrBuffer()
        self._last_fir = ""
        self._last_model_pr = ""
        self._analysis_root: Optional[Path] = None
        self._plot_clicks = ModelingPlotClickRouter(self)
        self._gnom_watcher = QFileSystemWatcher(self)
        self._gnom_watcher.fileChanged.connect(self._on_gnom_file_changed)
        self._build_ui()
        install_context_freeze(self)
        self.apply_context(ctx)

    def apply_context(self, ctx: ModelingContext) -> None:
        if self._runtime.is_running():
            # Keep paths honest for the active job; liveview will re-push after finish.
            enter_context_freeze(self)
            return
        self._ctx = ctx if isinstance(ctx, ModelingContext) else ModelingContext.from_dict({})
        # Always apply path fields from context (including empty) so a sample without
        # GNOM/profile does not keep the previous sample's misleading defaults.
        self._pf_profile.set_text(str(self._ctx.profile_path or ""))
        gnom = str(self._ctx.gnom_path or "").strip()
        if gnom and not os.path.isfile(gnom):
            gnom = ""
        self._pf_gnom.set_text(gnom)
        if self._ctx.output_dir:
            resolved = resolve_sample_modeling_dir(
                self._ctx.output_dir, profile_path=self._ctx.profile_path or ""
            )
            self._pf_outdir.set_text(str(resolved))
            root = analysis_root_from_modeling_path(resolved)
            if root is not None:
                self._analysis_root = root
        else:
            self._pf_outdir.set_text("")
        mode = (self._ctx.mode or "").lower()
        if mode not in ("bodies", "dammif", "denss"):
            mode = "dammif"
        self._set_mode_radio(mode)
        # Ensure PathField matches the mode's conventional family (ctx may be stale).
        self._sync_outdir_to_mode(mode)
        opts = self._ctx.options or {}
        if "n_runs" in opts:
            try:
                self._n_runs.setValue(int(opts["n_runs"]))
            except (TypeError, ValueError):
                pass
        if "mode" in opts:
            idx = self._denss_protocol.findData(str(opts["mode"]))
            if idx >= 0:
                self._denss_protocol.setCurrentIndex(idx)
        if "denss_mode" in opts:
            idx = self._denss_mode.findData(str(opts["denss_mode"]))
            if idx >= 0:
                self._denss_mode.setCurrentIndex(idx)
        if "n_maps" in opts:
            try:
                self._denss_n_maps.setValue(int(opts["n_maps"]))
            except (TypeError, ValueError):
                pass
        shapes = opts.get("shapes")
        if isinstance(shapes, list) and shapes:
            wanted = {str(s) for s in shapes}
            for i in range(self._shapes.count()):
                item = self._shapes.item(i)
                item.setCheckState(Qt.Checked if item.text() in wanted else Qt.Unchecked)
        self._update_controls_visibility()
        self._update_confirm_enabled()
        self._watch_gnom_path(self._gnom())
        self._apply_run_params_from_disk()
        self._try_load_existing_artifacts()

    def _apply_run_params_from_disk(self) -> None:
        """Restore Confirm controls from skill ``*_run_params.yml`` (and legacy CIF count)."""
        out = self._outdir()
        if not out:
            return
        if out != (self._pf_outdir.text() or "").strip():
            self._pf_outdir.set_text(out)
        if not os.path.isdir(out):
            return
        params = read_run_params(out, profile_path=self._profile())
        inferred = infer_shape_mode_from_disk(out, profile_path=self._profile())
        ctx_mode = (self._ctx.mode or "").lower()
        if (
            inferred
            and inferred in ("bodies", "dammif", "denss")
            and ctx_mode not in ("bodies", "dammif", "denss")
        ):
            self._set_mode_radio(inferred)
            self._update_controls_visibility()
        if "n_runs" in params:
            try:
                self._n_runs.setValue(max(1, int(params["n_runs"])))
            except (TypeError, ValueError):
                pass
        else:
            n_legacy = infer_n_runs_from_disk(out, profile_path=self._profile())
            if n_legacy is not None:
                self._n_runs.setValue(n_legacy)
        if "mode" in params:
            idx = self._denss_protocol.findData(str(params["mode"]))
            if idx >= 0:
                self._denss_protocol.setCurrentIndex(idx)
        if "denss_mode" in params:
            tok = str(params["denss_mode"]).strip().lower()
            if tok in ("fast", "slow", "membrane"):
                idx = self._denss_mode.findData(tok)
                if idx >= 0:
                    self._denss_mode.setCurrentIndex(idx)
        if "n_maps" in params:
            try:
                self._denss_n_maps.setValue(max(2, int(params["n_maps"])))
            except (TypeError, ValueError):
                pass
        shapes = params.get("shapes")
        if isinstance(shapes, list) and shapes:
            wanted = {str(s) for s in shapes}
            for i in range(self._shapes.count()):
                item = self._shapes.item(i)
                item.setCheckState(Qt.Checked if item.text() in wanted else Qt.Unchecked)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setSpacing(10)

        # Left: I(q), delta, P(r)
        left = QVBoxLayout()
        self._iq = ShapeFitPlot()
        self._delta = FitResidualsPlot()
        self._pr = PrPlot()
        for p, title in (
            (self._iq, "Model I(q) fit (DAM / DENSS / BODIES)"),
            (self._delta, "Model ΔI"),
            (self._pr, "P(r) comparison (GNOM vs model)"),
        ):
            box = QGroupBox(title)
            lay = QVBoxLayout(box)
            lay.addWidget(p, 1)
            left.addWidget(box, 1)
        self._iq.mpl_connect("button_press_event", lambda ev: self._enlarge_iq(ev))
        self._delta.mpl_connect("button_press_event", lambda ev: self._enlarge_delta(ev))
        self._pr.mpl_connect("button_press_event", lambda ev: self._enlarge_pr(ev))

        # Middle: 3D (dominant column — stretch so it reads as ~square)
        mid_box = QGroupBox("3D")
        mid_box.setMinimumWidth(720)
        mid_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        mid_lay = QVBoxLayout(mid_box)
        self._viewer = LiveviewViewer3D()
        mid_lay.addWidget(self._viewer, 1)

        # Right: controls + passport
        right = QVBoxLayout()
        ctrl_box = QGroupBox("Controls")
        ctrl = QVBoxLayout(ctrl_box)
        mode_row = QHBoxLayout()
        self._grp = QButtonGroup(self)
        self._rb_bodies = QRadioButton("BODIES")
        self._rb_dammif = QRadioButton("DAMMIF")
        self._rb_denss = QRadioButton("DENSS")
        self._rb_dammif.setChecked(True)
        for rb in (self._rb_bodies, self._rb_dammif, self._rb_denss):
            self._grp.addButton(rb)
            mode_row.addWidget(rb)
        mode_row.addStretch(1)
        ctrl.addLayout(mode_row)

        ctrl.addWidget(QLabel("I(q) profile"))
        self._pf_profile = PathField(mode="file", expected_exts=(".dat",))
        ctrl.addWidget(self._pf_profile)
        ctrl.addWidget(QLabel("GNOM .out (required for DAMMIF from liveview)"))
        self._pf_gnom = PathField(mode="file", expected_exts=(".out",))
        ctrl.addWidget(self._pf_gnom)
        ctrl.addWidget(QLabel("Output directory"))
        self._pf_outdir = PathField(mode="dir")
        ctrl.addWidget(self._pf_outdir)

        self._lbl_shapes = QLabel("Body models")
        ctrl.addWidget(self._lbl_shapes)
        self._shapes = QListWidget()
        defaults = set(_DEFAULT_SHAPES)
        for name in BODIES_SHAPES_LIST:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in defaults else Qt.Unchecked)
            self._shapes.addItem(item)
        ctrl.addWidget(self._shapes, 1)

        self._lbl_n_runs = QLabel("n_runs")
        ctrl.addWidget(self._lbl_n_runs)
        self._n_runs = QSpinBox()
        self._n_runs.setRange(1, 20)
        self._n_runs.setValue(1)
        ctrl.addWidget(self._n_runs)

        self._lbl_denss_protocol = QLabel("protocol")
        ctrl.addWidget(self._lbl_denss_protocol)
        self._denss_protocol = QComboBox()
        for lab, data in (("pilot", "pilot"), ("average", "average"), ("refined", "refined")):
            self._denss_protocol.addItem(lab, data)
        ctrl.addWidget(self._denss_protocol)
        self._lbl_denss_mode = QLabel("denss_mode")
        ctrl.addWidget(self._lbl_denss_mode)
        self._denss_mode = QComboBox()
        for lab, data in (("fast", "fast"), ("slow", "slow"), ("membrane", "membrane")):
            self._denss_mode.addItem(lab, data)
        ctrl.addWidget(self._denss_mode)
        self._lbl_denss_n_maps = QLabel("n_maps")
        ctrl.addWidget(self._lbl_denss_n_maps)
        self._denss_n_maps = QSpinBox()
        self._denss_n_maps.setRange(2, 100)
        self._denss_n_maps.setValue(20)
        ctrl.addWidget(self._denss_n_maps)

        self._status = RunStatusBar()
        ctrl.addWidget(self._status)
        self._confirm = QPushButton("Confirm")
        self._confirm.clicked.connect(lambda: self._on_confirm(quiet=False))
        ctrl.addWidget(self._confirm)

        pass_box = QGroupBox("Quality passport")
        pass_lay = QVBoxLayout(pass_box)
        self._passport = PassportTableWidget()
        pass_lay.addWidget(self._passport, 1)

        right.addWidget(ctrl_box, 2)
        right.addWidget(pass_box, 1)

        root.addLayout(left, 2)
        root.addWidget(mid_box, 5)
        root.addLayout(right, 2)

        for rb in (self._rb_bodies, self._rb_dammif, self._rb_denss):
            rb.toggled.connect(lambda *_: self._on_mode_changed())
        self._pf_profile.path_changed.connect(self._on_profile_path_changed)
        self._pf_gnom.path_changed.connect(self._update_confirm_enabled)
        self._pf_gnom.path_changed.connect(self._on_gnom_path_edited)
        self._pf_outdir.path_changed.connect(self._on_outdir_path_changed)
        self._update_controls_visibility()

    def _mode(self) -> str:
        if self._rb_bodies.isChecked():
            return "bodies"
        if self._rb_denss.isChecked():
            return "denss"
        return "dammif"

    def _set_mode_radio(self, mode: str) -> None:
        m = (mode or "dammif").lower()
        mapping = {
            "bodies": self._rb_bodies,
            "dammif": self._rb_dammif,
            "denss": self._rb_denss,
        }
        rb = mapping.get(m, self._rb_dammif)
        rb.setChecked(True)

    def _on_mode_changed(self) -> None:
        self._sync_outdir_to_mode(self._mode())
        self._update_controls_visibility()
        self._update_confirm_enabled()
        # Retargeted family dir may have no artifacts — clear stale views from the previous mode.
        self._try_load_existing_artifacts()

    def _on_profile_path_changed(self, *_a) -> None:
        self._sync_outdir_to_mode(self._mode())
        self._update_confirm_enabled()

    def _on_outdir_path_changed(self, *_a) -> None:
        raw = (self._pf_outdir.text() or "").strip()
        if raw:
            root = analysis_root_from_modeling_path(raw)
            if root is not None:
                self._analysis_root = root
        self._update_confirm_enabled()

    def _sync_outdir_to_mode(self, mode: str) -> None:
        """Point PathField at ``<analysis_root>/<family>/<stem>`` for the active engine."""
        m = (mode or "dammif").lower()
        if m not in ("bodies", "dammif", "denss"):
            return
        suggested = conventional_sample_modeling_dir(
            mode=m,
            current_outdir=(self._pf_outdir.text() or "").strip(),
            profile_path=self._profile(),
            analysis_root=self._analysis_root,
        )
        if suggested is None:
            return
        text = str(suggested)
        if (self._pf_outdir.text() or "").strip() != text:
            self._pf_outdir.set_text(text)

    def _update_controls_visibility(self) -> None:
        mode = self._mode()
        bodies = mode == "bodies"
        dam = mode == "dammif"
        denss = mode == "denss"
        for w in (self._lbl_shapes, self._shapes):
            w.setVisible(bodies)
        for w in (self._lbl_n_runs, self._n_runs):
            w.setVisible(dam)
        for w in (
            self._lbl_denss_protocol,
            self._denss_protocol,
            self._lbl_denss_mode,
            self._denss_mode,
            self._lbl_denss_n_maps,
            self._denss_n_maps,
        ):
            w.setVisible(denss)

    def _selected_shapes(self) -> List[str]:
        out: List[str] = []
        for i in range(self._shapes.count()):
            item = self._shapes.item(i)
            if item.checkState() == Qt.Checked:
                out.append(item.text())
        return out

    def _profile(self) -> str:
        return (self._pf_profile.text() or "").strip()

    def _gnom(self) -> str:
        return (self._pf_gnom.text() or "").strip()

    def _outdir(self) -> str:
        raw = (self._pf_outdir.text() or "").strip()
        if not raw:
            return ""
        return str(resolve_sample_modeling_dir(raw, profile_path=self._profile()))

    def _update_confirm_enabled(self) -> None:
        if self._runtime.is_running():
            self._confirm.setEnabled(False)
            return
        mode = self._mode()
        out = self._outdir()
        if not out:
            self._confirm.setEnabled(False)
            return
        if mode == "dammif":
            gnom = self._gnom()
            ok = bool(gnom) and os.path.isfile(gnom)
            if self._ctx.require_gnom_for_dam and not ok:
                self._confirm.setEnabled(False)
                self._status.set_status(
                    "DAMMIF requires a GNOM .out (run P(r) in liveview first).",
                    running=False,
                )
                return
            if not ok:
                # Standalone may omit gnom only if profile exists (skill fallback).
                ok = bool(self._profile()) and os.path.isfile(self._profile())
            self._confirm.setEnabled(ok)
            return
        # bodies / denss
        prof = self._profile()
        self._confirm.setEnabled(bool(prof) and os.path.isfile(prof))

    def _views_frozen(self) -> bool:
        """True while a Confirm job runs or context freeze is deferred — do not retarget plots."""
        return bool(self._runtime.is_running()) or bool(
            getattr(self, "_context_push_deferred", False)
        )

    def _on_gnom_path_edited(self, *_args) -> None:
        if self._views_frozen():
            return
        self._watch_gnom_path(self._gnom())
        self._refresh_pr_comparison()

    def _on_gnom_file_changed(self, path: str) -> None:
        if self._views_frozen():
            return
        # Editors often replace the file; re-add so further updates are seen.
        p = (path or "").strip()
        if p and os.path.isfile(p):
            watched = set(self._gnom_watcher.files())
            if p not in watched:
                self._gnom_watcher.addPath(p)
        self._refresh_pr_comparison()

    def _watch_gnom_path(self, gnom: str) -> None:
        for old in list(self._gnom_watcher.files()):
            self._gnom_watcher.removePath(old)
        g = (gnom or "").strip()
        if g and os.path.isfile(g):
            self._gnom_watcher.addPath(g)

    def _resolve_model_pr_dat(self, subdir: str | Path | None = None) -> str:
        """Best-effort ``*_pr.dat`` next to DAMMIF/BODIES artifacts."""
        sd_s = str(subdir or self._outdir() or "").strip()
        if not sd_s or not os.path.isdir(sd_s):
            return self._last_model_pr if self._last_model_pr and os.path.isfile(self._last_model_pr) else ""
        sd = Path(sd_s)
        mode = self._mode()
        if mode == "dammif":
            cat = build_dam_model_catalog(sd)
            best = cat.best()
            if best is not None and best.cif_path:
                name = Path(best.cif_path).name
                m = re.match(r"(dammif-\d+)-1\.cif$", name, flags=re.I)
                if m:
                    cand = sd / f"{m.group(1)}_pr.dat"
                    if cand.is_file():
                        return str(cand.resolve())
            cands = sorted(sd.glob("*_pr.dat"), key=lambda p: p.stat().st_mtime, reverse=True)
            if cands:
                return str(cands[0].resolve())
        elif mode == "bodies":
            best_shape, _params, _csv = bodies_best_fit(sd)
            if best_shape:
                cand = sd / f"{best_shape}_pr.dat"
                if cand.is_file():
                    return str(cand.resolve())
            cands = sorted(sd.glob("*_pr.dat"), key=lambda p: p.stat().st_mtime, reverse=True)
            if cands:
                return str(cands[0].resolve())
        if self._last_model_pr and os.path.isfile(self._last_model_pr):
            return self._last_model_pr
        return ""

    def _refresh_pr_comparison(self) -> None:
        """Overlay GNOM P(r) with model-dam/bodies ``*_pr.dat`` when present."""
        if self._views_frozen():
            return
        gnom = self._gnom()
        model_pr = self._resolve_model_pr_dat()
        if model_pr:
            self._last_model_pr = model_pr
        has_gnom = bool(gnom) and os.path.isfile(gnom)
        has_model = bool(model_pr) and os.path.isfile(model_pr)
        if not has_gnom and not has_model:
            self._pr.clear_plot()
            return
        label = "model"
        if has_model:
            stem = Path(model_pr).name
            if stem.endswith("_pr.dat"):
                label = stem[: -len("_pr.dat")]
        self._pr.plot_gnom_and_model(
            gnom if has_gnom else None,
            model_pr if has_model else None,
            gnom_label="GNOM",
            model_label=label,
        )

    def _options_for_confirm(self) -> Dict[str, Any]:
        mode = self._mode()
        out: Dict[str, Any] = {}
        if mode == "bodies":
            out["shapes"] = self._selected_shapes() or list(_DEFAULT_SHAPES)
        elif mode == "dammif":
            out["n_runs"] = int(self._n_runs.value())
        elif mode == "denss":
            out["mode"] = str(self._denss_protocol.currentData() or "pilot")
            out["denss_mode"] = str(self._denss_mode.currentData() or "fast")
            out["n_maps"] = int(self._denss_n_maps.value())
            out["n_jobs"] = 1
        return out

    def _on_confirm_ipc(self) -> None:
        self._on_confirm(quiet=True)

    def _on_confirm(self, *, quiet: bool = False) -> None:
        if self._runtime.is_running():
            # Busy: ignore Confirm (freeze only on deferred context pushes).
            return
        mode = self._mode()
        outdir = self._outdir()
        if not outdir:
            if not quiet:
                QMessageBox.warning(self, "Confirm", "Set an output directory.")
            return
        # apply_batch appends profile stem — pass the family dir, keep UI on sample dir.
        prof_for_batch = self._profile()
        batch_outdir = skill_batch_output_dir(outdir, profile_path=prof_for_batch)
        Path(outdir).mkdir(parents=True, exist_ok=True)
        Path(batch_outdir).mkdir(parents=True, exist_ok=True)
        try:
            self._runtime.set_workdir(Path(batch_outdir))
        except RuntimeError as exc:
            if not quiet:
                QMessageBox.warning(self, "Confirm", str(exc))
            return

        opts = self._options_for_confirm()
        opts["output_dir"] = str(Path(batch_outdir).resolve())
        opts["use_cache"] = False
        skill = {"bodies": "model_bodies", "dammif": "model_dam", "denss": "model_density"}[mode]
        positional: List[str] = []
        if mode == "dammif":
            gnom = self._gnom()
            if self._ctx.require_gnom_for_dam or (gnom and os.path.isfile(gnom)):
                if not gnom or not os.path.isfile(gnom):
                    if not quiet:
                        QMessageBox.warning(self, "Confirm", "DAMMIF requires a GNOM .out path.")
                    return
                opts["gnom_path"] = str(Path(gnom).resolve())
            # model_dam still requires a profile positional for batch stem / I(q) plots
            prof = self._profile()
            if not prof or not os.path.isfile(prof):
                if not quiet:
                    QMessageBox.warning(
                        self, "Confirm", "DAMMIF also needs the I(q) profile path for the skill."
                    )
                return
            positional = [str(Path(prof).resolve())]
        else:
            prof = self._profile()
            if not prof or not os.path.isfile(prof):
                if not quiet:
                    QMessageBox.warning(self, "Confirm", "Set a valid I(q) profile.")
                return
            positional = [str(Path(prof).resolve())]
            gnom = self._gnom()
            if mode == "denss" and gnom and os.path.isfile(gnom):
                opts["gnom_path"] = str(Path(gnom).resolve())

        if self._ipc is not None:
            self._ipc.send_confirmed(
                mode=mode,
                options=dict(opts),
                paths={
                    "profile_path": self._profile(),
                    "gnom_path": self._gnom(),
                    "output_dir": outdir,
                },
            )
            self._ipc.send_busy(True)

        self._confirm.setEnabled(False)
        self._progress_buf = ProgressStderrBuffer()
        self._status.set_running(True, text=f"Running {skill}…")
        self._runtime.start(skill, positional, opts)

    def _on_started(self, skill: str) -> None:
        self._status.set_running(True, text=f"Running {skill}…")
        self._passport.set_message(f"Running {skill}…")

    def _on_stderr(self, chunk: str) -> None:
        for status in self._progress_buf.feed(chunk):
            self._status.set_running(True, text=status)
            self._passport.set_message(status)

    def _on_finished(self, outcome: object) -> None:
        if self._ipc is not None:
            self._ipc.send_busy(False)
        success = bool(getattr(outcome, "success", False))
        result = getattr(outcome, "result", {}) or {}
        if not isinstance(result, dict):
            result = {}
        if self._ipc is not None:
            self._ipc.send_finished(success=success, result=result)
        if not success:
            self._status.set_running(
                False, text=f"Failed (exit {getattr(outcome, 'exit_code', '?')})"
            )
            self._passport.set_message(self._status.text(), poor=True)
            self._update_confirm_enabled()
            notify_ready_for_context_if_deferred(self, self._ipc)
            return
        self._status.set_running(False, text="Done.")
        self._ingest_result(result)
        self._update_confirm_enabled()
        notify_ready_for_context_if_deferred(self, self._ipc)

    def _ingest_result(self, result: Dict[str, Any]) -> None:
        subdir = result.get("output_subdir") or self._outdir()
        if isinstance(subdir, str) and subdir.strip():
            resolved = resolve_sample_modeling_dir(subdir, profile_path=self._profile())
            self._pf_outdir.set_text(str(resolved))
            subdir = str(resolved)
        fir = ""
        for key in ("best_fir_path", "fir_path", "fit_path", "best_fit_path"):
            v = result.get(key)
            if isinstance(v, str) and v.strip() and os.path.isfile(v):
                fir = v
                break
        if not fir and isinstance(subdir, str) and subdir:
            # Heuristic: newest .fir / .fit under subdir
            sd = Path(subdir)
            cands = sorted(sd.rglob("*.fir"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not cands:
                cands = sorted(sd.rglob("*.fit"), key=lambda p: p.stat().st_mtime, reverse=True)
            if cands:
                fir = str(cands[0])
        if fir:
            self._last_fir = fir
            self._iq.plot_from_fir(fir)
            self._delta.plot_from_fir(fir)
        model_pr = self._resolve_model_pr_dat(subdir if isinstance(subdir, str) else None)
        if model_pr:
            self._last_model_pr = model_pr
        self._refresh_pr_comparison()
        mode = self._mode()
        self._passport.set_rows(self._passport_rows_for_result(result, mode=mode, subdir=subdir))
        if isinstance(subdir, str) and subdir and os.path.isdir(subdir):
            if mode == "dammif":
                cat = build_dam_model_catalog(Path(subdir))
                self._viewer.set_dam_catalog(cat)
            elif mode == "denss":
                cat = build_denss_model_catalog(Path(subdir))
                self._viewer.set_denss_catalog(cat)
            elif mode == "bodies":
                # Prefer CIF / bodies viewer APIs if present in result
                cif = result.get("best_cif_path") or result.get("cif_path")
                if isinstance(cif, str) and os.path.isfile(cif):
                    self._viewer.set_model_path(cif)

    def _passport_rows_for_result(
        self,
        result: Dict[str, Any],
        *,
        mode: str,
        subdir: object,
    ) -> list[tuple[str, str, str]]:
        """Quality passport: real metrics only (filenames / mode are not quality)."""
        rows: list[tuple[str, str, str]] = []
        if mode == "dammif":
            sd = Path(str(subdir)) if subdir else Path(self._outdir() or ".")
            if sd.is_dir():
                best = build_dam_model_catalog(sd).best()
                if best is not None and best.chi2 is not None:
                    try:
                        rows.append(("χ²", f"{float(best.chi2):.3g}", "ok"))
                    except (TypeError, ValueError):
                        pass
            return rows
        if mode == "bodies":
            sd = Path(str(subdir)) if subdir else Path(self._outdir() or ".")
            best_shape, _params, _csv = bodies_best_fit(sd)
            chi2 = None
            yml = sd / "bodies_fits.yml"
            if best_shape and yml.is_file():
                try:
                    import yaml

                    data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
                    if isinstance(data, dict) and isinstance(data.get(best_shape), dict):
                        chi2 = data[best_shape].get("chi2")
                except Exception:
                    chi2 = None
            if best_shape:
                rows.append(("best shape", str(best_shape), "ok"))
            if chi2 is not None:
                try:
                    rows.append(("χ²", f"{float(chi2):.3g}", "ok"))
                except (TypeError, ValueError):
                    pass
            return rows
        if mode == "denss":
            res_a = result.get("fsc_resolution_A")
            if res_a is not None and res_a != "":
                try:
                    # Skill reports Å; convert to nm for display consistency.
                    rows.append(("FSC resolution", f"{float(res_a) * 0.1:.3g} nm", "ok"))
                except (TypeError, ValueError):
                    rows.append(("FSC resolution", str(res_a), "ok"))
            return rows
        return rows

    def _clear_result_views(self) -> None:
        """Wipe I(q)/Δ/3D/passport (and model P(r)) so a sample without results is not misleading."""
        self._last_fir = ""
        self._last_model_pr = ""
        try:
            self._iq.clear_plot()
        except Exception:
            pass
        try:
            self._delta.clear_plot()
        except Exception:
            pass
        try:
            self._viewer.clear()
        except Exception:
            pass
        try:
            self._passport.set_message("—")
        except Exception:
            pass
        # GNOM-only comparison is fine if PathField still has a .out; no stale model overlay.
        try:
            self._refresh_pr_comparison()
        except Exception:
            pass

    def _try_load_existing_artifacts(self) -> None:
        self._clear_result_views()
        out = self._outdir()
        if not out:
            return
        if out != (self._pf_outdir.text() or "").strip():
            self._pf_outdir.set_text(out)
        if not os.path.isdir(out):
            return
        sd = Path(out)
        mode = self._mode()
        inferred = infer_shape_mode_from_disk(sd, profile_path=self._profile())
        if inferred and inferred in ("bodies", "dammif", "denss"):
            self._set_mode_radio(inferred)
            self._update_controls_visibility()
            mode = inferred
        fir = ""
        loaded_3d = False
        if mode == "dammif":
            cat = build_dam_model_catalog(sd)
            if cat.entries:
                self._viewer.set_dam_catalog(cat)
                loaded_3d = True
                best = cat.best()
                if best and best.fir_path and os.path.isfile(best.fir_path):
                    fir = best.fir_path
                if not fir:
                    cands = sorted(sd.glob("*.fir"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if cands:
                        fir = str(cands[0])
        elif mode == "denss":
            cat = build_denss_model_catalog(sd)
            if cat.entries:
                self._viewer.set_denss_catalog(cat)
                loaded_3d = True
                best = cat.best()
                if best and best.fit_path and os.path.isfile(best.fit_path):
                    fir = best.fit_path
        elif mode == "bodies":
            best_shape, best_params, _csv = bodies_best_fit(sd)
            if best_shape:
                fir_cands = list(sd.glob(f"{best_shape}*.fir")) + list(sd.glob("bodies_fit-*.fir"))
                if fir_cands:
                    fir = str(fir_cands[0])
                try:
                    self._viewer.set_bodies_analytical(best_shape, best_params or {}, folder=sd)
                    loaded_3d = True
                except Exception:
                    cif_cands = sorted(sd.glob("*.cif"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if cif_cands:
                        self._viewer.set_model_path(str(cif_cands[0]))
                        loaded_3d = True
            else:
                fir_cands = sorted(sd.glob("*.fir"), key=lambda p: p.stat().st_mtime, reverse=True)
                if fir_cands:
                    fir = str(fir_cands[0])
        if fir and os.path.isfile(fir):
            self._last_fir = fir
            self._iq.plot_from_fir(fir)
            self._delta.plot_from_fir(fir)
        model_pr = self._resolve_model_pr_dat(sd)
        if model_pr:
            self._last_model_pr = model_pr
        self._refresh_pr_comparison()
        if fir or loaded_3d or model_pr:
            self._passport.set_rows(
                self._passport_rows_for_result({}, mode=mode, subdir=sd)
            )

    def _enlarge_iq(self, ev: object) -> None:
        if getattr(ev, "inaxes", None) is None or int(getattr(ev, "button", 0)) != 1:
            return
        path = self._iq.click_path
        if not path:
            return

        def _populate(plot: ShapeFitPlot) -> None:
            plot.plot_from_fir(path)

        self._plot_clicks.open(
            key="iq",
            title=f"Model I(q) fit — {Path(path).name}",
            make_plot=lambda: ShapeFitPlot(figsize=(8, 6)),
            populate=_populate,
        )

    def _enlarge_delta(self, ev: object) -> None:
        if getattr(ev, "inaxes", None) is None or int(getattr(ev, "button", 0)) != 1:
            return
        path = self._delta.click_path
        if not path:
            return

        def _populate(plot: FitResidualsPlot) -> None:
            plot.plot_from_fir(path)

        self._plot_clicks.open(
            key="delta",
            title=f"Model ΔI — {Path(path).name}",
            make_plot=lambda: FitResidualsPlot(figsize=(8, 4)),
            populate=_populate,
        )

    def _enlarge_pr(self, ev: object) -> None:
        if getattr(ev, "inaxes", None) is None or int(getattr(ev, "button", 0)) != 1:
            return
        gnom = self._gnom()
        model_pr = self._last_model_pr or self._resolve_model_pr_dat()
        if not ((gnom and os.path.isfile(gnom)) or (model_pr and os.path.isfile(model_pr))):
            return
        label = "model"
        if model_pr and os.path.isfile(model_pr):
            stem = Path(model_pr).name
            if stem.endswith("_pr.dat"):
                label = stem[: -len("_pr.dat")]
        gnom_ok = gnom if gnom and os.path.isfile(gnom) else None
        model_ok = model_pr if model_pr and os.path.isfile(model_pr) else None

        def _populate(plot: PrPlot) -> None:
            plot.plot_gnom_and_model(
                gnom_ok,
                model_ok,
                gnom_label="GNOM",
                model_label=label,
            )

        short = Path(gnom_ok or model_ok or "pr").name
        self._plot_clicks.open(
            key="pr",
            title=f"P(r) comparison — {short}",
            make_plot=lambda: PrPlot(figsize=(8, 6)),
            populate=_populate,
        )
