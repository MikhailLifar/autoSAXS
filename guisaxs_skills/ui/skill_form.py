from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QCheckBox, QFormLayout, QGroupBox, QLabel, QLineEdit, QWidget, QVBoxLayout

from ..core.models import RunRequest, SkillMeta
from ..logic.path_normalize import normalize_pathish
from ..logic.session_state import SessionPathHints
from ..logic.smart_defaults import (
    ANALYSIS_SKILLS_WITH_PROFILE,
    anchor_dir_for_path_expression,
    anchor_dir_from_resolved_path_list,
    anchor_for_calibrate_config,
    anchor_for_model_mixture_config,
    browse_start_dir_for_resolved_paths,
    common_parent_dir_if_all_files,
    find_config_conf_near,
    find_integrator_dir_near,
    find_mask_near,
    find_single_buffer_dat,
    path_exists,
    path_expression_paths_fully_exist,
    path_value_from_saved_state,
    resolve_under_workdir,
    session_anchor_dir_str_from_resolved_paths,
    session_hint_for_positional_path,
    session_hint_option_config_path,
    session_hint_option_mask,
)
from .path_field import PathField
from .style import COLOR_REQUIRED_STAR


class SkillForm(QWidget):
    submit_requested = pyqtSignal()

    @staticmethod
    def _required_label(name: str) -> QLabel:
        """
        Render a form label with a red '*' suffix (required-field marker).
        Use RichText so only the star is colored.
        """
        lab = QLabel(f"{name} <span style='color:{COLOR_REQUIRED_STAR}; font-weight:700'>*</span>")
        lab.setTextFormat(1)  # Qt.RichText (avoid importing Qt just for this)
        return lab

    @staticmethod
    def _label(name: str) -> QLabel:
        lab = QLabel(str(name))
        lab.setTextFormat(0)  # Qt.PlainText
        return lab

    def __init__(self) -> None:
        super().__init__()
        self._meta: Optional[SkillMeta] = None
        self._workdir: Path = Path.cwd()
        self._hints: Optional[SessionPathHints] = None
        self._pos_widgets: List[QWidget] = []
        self._opt_fields: Dict[str, QWidget] = {}

        self._copy_inputs = QCheckBox("Copy inputs into working directory")

        self._pos_group = QGroupBox("Inputs")
        self._pos_layout = QFormLayout(self._pos_group)

        self._opt_group = QGroupBox("Options")
        self._opt_layout = QFormLayout(self._opt_group)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._pos_group)
        lay.addWidget(self._opt_group)
        lay.addWidget(self._copy_inputs)

    def copy_inputs_enabled(self) -> bool:
        return self._copy_inputs.isChecked()

    def _sync_two_d_tif_dir_from_paths(self, paths: List[str]) -> None:
        if self._hints is None:
            return
        s = session_anchor_dir_str_from_resolved_paths(paths, self._workdir)
        if s:
            self._hints.two_d_tif_dir = s

    def _sync_one_d_profile_dir_from_paths(self, paths: List[str]) -> None:
        if self._hints is None:
            return
        s = session_anchor_dir_str_from_resolved_paths(paths, self._workdir)
        if s:
            self._hints.one_d_profile_dir = s

    def set_skill(
        self,
        meta: SkillMeta,
        *,
        workdir: Path,
        default_output_dir: str,
        hints: SessionPathHints,
        saved_state: Optional[dict] = None,
    ) -> None:
        self._meta = meta
        self._workdir = workdir
        self._clear_layout(self._pos_layout)
        self._clear_layout(self._opt_layout)
        self._pos_widgets = []
        self._opt_fields = {}

        for p in meta.positional_params:
            label = self._required_label(p.name)
            if self._is_path_expression_annotation(p.annotation):
                f = PathField(
                    mode="any",
                    allow_multiple=not self._is_singleton_path_expression_annotation(p.annotation),
                    show_get_default=self._is_config_path_expression_annotation(p.annotation),
                    expected_exts=self._expected_exts_for_annotation(p.annotation),
                )
                f.set_workdir(workdir)
                self._pos_widgets.append(f)
                self._pos_layout.addRow(label, f)
            else:
                le = QLineEdit()
                if p.default is not None:
                    le.setText(str(p.default))
                self._pos_widgets.append(le)
                self._pos_layout.addRow(label, le)
                le.returnPressed.connect(self.submit_requested.emit)

        output = PathField(mode="dir")
        output.set_workdir(workdir)
        output.set_text(default_output_dir)
        self._opt_fields["output_dir"] = output
        self._opt_layout.addRow(self._label("output_dir"), output)
        for le in output.findChildren(QLineEdit):
            le.returnPressed.connect(self.submit_requested.emit)

        use_cache = QCheckBox("")
        use_cache.setChecked(False)
        self._opt_fields["use_cache"] = use_cache
        self._opt_layout.addRow(self._label("Use cache"), use_cache)

        for opt in meta.option_params:
            if opt.name in ("output_dir", "use_cache"):
                continue
            is_required = opt.kind == "required_kwonly"
            label = self._required_label(opt.name) if is_required else self._label(opt.name)
            if self._is_path_expression_annotation(opt.annotation):
                f = PathField(
                    mode="any",
                    allow_multiple=not self._is_singleton_path_expression_annotation(opt.annotation),
                    show_get_default=self._is_config_path_expression_annotation(opt.annotation),
                    expected_exts=self._expected_exts_for_annotation(opt.annotation),
                )
                f.set_workdir(workdir)
                if opt.default is not None:
                    f.set_text(str(opt.default))
                self._opt_fields[opt.name] = f
                self._opt_layout.addRow(label, f)
                for le in f.findChildren(QLineEdit):
                    le.returnPressed.connect(self.submit_requested.emit)
                continue
            if isinstance(opt.default, bool):
                cb = QCheckBox(opt.name)
                cb.setChecked(bool(opt.default))
                self._opt_fields[opt.name] = cb
                self._opt_layout.addRow(label, cb)
            else:
                le = QLineEdit()
                if opt.default is not None:
                    le.setText(str(opt.default))
                self._opt_fields[opt.name] = le
                self._opt_layout.addRow(label, le)
                le.returnPressed.connect(self.submit_requested.emit)

        saved = saved_state or {}
        self._copy_inputs.setChecked(bool(saved.get("copy_inputs", False)))
        self._hints = hints
        self._apply_smart_coalesce(meta, workdir, hints, saved)
        self._apply_all_browse_starts(meta, workdir, hints)
        self._wire_primary_path_live_updates(meta)
        self._wire_shared_option_path_live_updates(meta)

    def _apply_smart_coalesce(self, meta: SkillMeta, workdir: Path, hints: SessionPathHints, saved: dict) -> None:
        saved_pos: List[Any] = list(saved.get("positional") or [])
        saved_opt: Dict[str, Any] = dict(saved.get("options") or {})

        resolved_path_texts: List[str] = []

        for i, p in enumerate(meta.positional_params):
            w = self._pos_widgets[i]
            if isinstance(w, PathField):
                multiple = not self._is_singleton_path_expression_annotation(p.annotation)
                val: Optional[str] = None

                val = session_hint_for_positional_path(meta.name, p.name, hints, workdir)

                if val is None and i < len(saved_pos) and isinstance(saved_pos[i], dict):
                    val = path_value_from_saved_state(workdir, saved_pos[i], multiple=multiple)

                if val is None:
                    val = self._filesystem_positional_default(meta.name, p.name, resolved_path_texts, saved_pos, workdir)

                if val is None and p.default is not None and self._is_path_expression_annotation(p.annotation):
                    d = str(p.default)
                    if path_exists(d, workdir):
                        val = str(resolve_under_workdir(d, workdir))

                if val:
                    w.set_text(val)
                resolved_path_texts.append(w.text())
            else:
                assert isinstance(w, QLineEdit)
                raw: Optional[str] = None
                if i < len(saved_pos) and isinstance(saved_pos[i], str) and saved_pos[i].strip():
                    raw = saved_pos[i].strip()
                elif p.default is not None:
                    raw = str(p.default)
                if raw is not None:
                    w.setText(raw)
                resolved_path_texts.append(w.text())

        if "output_dir" in self._opt_fields:
            out_w = self._opt_fields["output_dir"]
            assert isinstance(out_w, PathField)
            od_saved = saved_opt.get("output_dir")
            if isinstance(od_saved, dict):
                v = path_value_from_saved_state(workdir, od_saved, multiple=False)
                if v and Path(v).is_dir():
                    out_w.set_text(v)

        if "use_cache" in self._opt_fields and "use_cache" in saved_opt:
            uc = self._opt_fields["use_cache"]
            if isinstance(uc, QCheckBox):
                uc.setChecked(bool(saved_opt["use_cache"]))

        for opt in meta.option_params:
            if opt.name in ("output_dir", "use_cache"):
                continue
            ow = self._opt_fields.get(opt.name)
            if ow is None:
                continue
            if isinstance(ow, PathField):
                multiple = not self._is_singleton_path_expression_annotation(opt.annotation)
                val: Optional[str] = None
                if opt.name == "mask":
                    val = session_hint_option_mask(hints, workdir)
                elif opt.name == "config_path":
                    val = session_hint_option_config_path(hints, workdir)
                ent = saved_opt.get(opt.name)
                if val is None and isinstance(ent, dict):
                    val = path_value_from_saved_state(workdir, ent, multiple=multiple)
                if val is None and opt.name == "mask":
                    if meta.name == "calibrate":
                        calib_w = self._pos_widgets[0] if meta.positional_params else None
                        calib_txt = calib_w.text() if isinstance(calib_w, PathField) else ""
                        ad = anchor_for_calibrate_config(saved_pos, calib_txt, workdir)
                        if ad:
                            mpath = find_mask_near(ad)
                            if mpath:
                                val = str(mpath.resolve())
                    elif meta.name == "integrate_proxy":
                        img_w = self._pos_widgets[0] if meta.positional_params else None
                        if isinstance(img_w, PathField):
                            ipaths = [normalize_pathish(p) for p in img_w.paths() if normalize_pathish(p)]
                            ad = anchor_dir_from_resolved_path_list(ipaths, workdir)
                            if ad:
                                mpath = find_mask_near(ad)
                                if mpath:
                                    val = str(mpath.resolve())
                if val is None and opt.name == "config_path" and meta.name == "calibrate":
                    calib_w = self._pos_widgets[0] if meta.positional_params else None
                    calib_txt = calib_w.text() if isinstance(calib_w, PathField) else ""
                    ad = anchor_for_calibrate_config(saved_pos, calib_txt, workdir)
                    if ad:
                        cfg = find_config_conf_near(ad)
                        if cfg:
                            val = str(cfg.resolve())
                if val is None and opt.name == "config_path" and meta.name == "model_mixture":
                    prof_w = self._pos_widgets[0] if meta.positional_params else None
                    profile_txt = prof_w.text() if isinstance(prof_w, PathField) else ""
                    saved_prof = saved_pos[0] if saved_pos else None
                    ad = anchor_for_model_mixture_config(profile_txt, saved_prof, workdir)
                    if ad:
                        cfg = find_config_conf_near(ad)
                        if cfg:
                            val = str(cfg)
                if val is None and opt.default is not None:
                    d = str(opt.default)
                    if path_exists(d, workdir):
                        val = str(resolve_under_workdir(d, workdir))
                if val:
                    ow.set_text(val)
            elif isinstance(ow, QLineEdit):
                if opt.name in saved_opt:
                    v = saved_opt[opt.name]
                    if v is not None and str(v).strip() != "":
                        ow.setText(str(v))
                elif opt.default is not None:
                    ow.setText(str(opt.default))
            elif isinstance(ow, QCheckBox) and opt.name in saved_opt:
                ow.setChecked(bool(saved_opt[opt.name]))

    def _filesystem_positional_default(
        self,
        skill_name: str,
        param_name: str,
        resolved_prefix: List[str],
        saved_pos: List[Any],
        workdir: Path,
    ) -> Optional[str]:
        if skill_name == "integrate" and param_name == "integrator_dir":
            anchor_txt = resolved_prefix[0] if resolved_prefix else ""
            ad = anchor_dir_for_path_expression(anchor_txt, workdir)
            if ad:
                found = find_integrator_dir_near(ad)
                if found:
                    return str(found)
            return None
        if skill_name == "subtract" and param_name == "buffer_1d":
            sample_txt = resolved_prefix[0] if resolved_prefix else ""
            buf = find_single_buffer_dat(sample_txt, workdir)
            if buf:
                return str(buf)
            return None
        return None

    def _wire_primary_path_live_updates(self, meta: SkillMeta) -> None:
        if not meta.positional_params:
            return
        w0 = self._pos_widgets[0]
        if not isinstance(w0, PathField):
            return
        w0.path_changed.connect(self._on_primary_path_expression_changed)

    def _wire_shared_option_path_live_updates(self, meta: SkillMeta) -> None:
        for opt in meta.option_params:
            if opt.name not in ("mask", "config_path"):
                continue
            w = self._opt_fields.get(opt.name)
            if isinstance(w, PathField):
                w.path_changed.connect(partial(self._on_shared_option_path_changed, opt.name))

    def _on_shared_option_path_changed(self, option_name: str) -> None:
        if self._hints is None or self._meta is None:
            return
        w = self._opt_fields.get(option_name)
        if not isinstance(w, PathField):
            return
        paths = [normalize_pathish(p) for p in w.paths() if normalize_pathish(p)]
        if len(paths) != 1:
            return
        p = resolve_under_workdir(paths[0], self._workdir)
        if not p.is_file():
            return
        s = str(p.resolve())
        if option_name == "mask":
            self._hints.mask_file_path = s
        elif option_name == "config_path":
            self._hints.config_file_path = s
        self._apply_all_browse_starts(self._meta, self._workdir, self._hints)

    def _on_primary_path_expression_changed(self) -> None:
        meta = self._meta
        hints = self._hints
        if not meta or hints is None:
            return
        w0 = self._pos_widgets[0]
        if not isinstance(w0, PathField):
            return
        paths = [normalize_pathish(p) for p in w0.paths() if normalize_pathish(p)]
        if not path_expression_paths_fully_exist(paths, self._workdir):
            return
        name = meta.name
        if name == "integrate":
            self._live_refresh_integrate(paths)
        elif name == "calibrate":
            self._live_refresh_calibrate(paths)
        elif name in ("integrate_proxy", "plot_2d"):
            self._live_refresh_two_d_shared(paths)
        elif name == "subtract":
            self._live_refresh_subtract(paths)
        elif name == "model_mixture":
            self._live_refresh_model_mixture(paths)
        elif name in ANALYSIS_SKILLS_WITH_PROFILE:
            self._live_refresh_analysis_profile(paths)

    def _live_refresh_integrate(self, paths: List[str]) -> None:
        assert self._hints is not None
        meta = self._meta
        if not meta:
            return
        workdir = self._workdir
        ad = anchor_dir_from_resolved_path_list(paths, workdir)
        if ad is not None:
            self._hints.integrate_output_dir = str(ad.resolve())
            self._hints.two_d_tif_dir = str(ad.resolve())
        w_int = self._pos_widgets[1]
        if not isinstance(w_int, PathField):
            return
        session_val = session_hint_for_positional_path("integrate", "integrator_dir", self._hints, workdir)
        if session_val:
            w_int.set_text(session_val)
        elif ad is not None:
            found = find_integrator_dir_near(ad)
            if found:
                self._hints.integrator_dir = str(found.resolve())
                w_int.set_text(str(found.resolve()))
        self._apply_all_browse_starts(meta, workdir, self._hints)

    def _live_refresh_two_d_shared(self, paths: List[str]) -> None:
        meta = self._meta
        if not meta or self._hints is None:
            return
        workdir = self._workdir
        self._sync_two_d_tif_dir_from_paths(paths)
        ad = anchor_dir_from_resolved_path_list(paths, workdir)
        if meta.name == "integrate_proxy" and ad is not None:
            mpath = find_mask_near(ad)
            if mpath is not None:
                self._hints.mask_file_path = str(mpath.resolve())
                w_mask = self._opt_fields.get("mask")
                if isinstance(w_mask, PathField):
                    w_mask.set_text(str(mpath.resolve()))
        self._apply_all_browse_starts(meta, workdir, self._hints)

    def _live_refresh_calibrate(self, paths: List[str]) -> None:
        assert self._hints is not None
        meta = self._meta
        if not meta:
            return
        workdir = self._workdir
        ad = anchor_dir_from_resolved_path_list(paths, workdir)
        if ad is not None:
            self._hints.two_d_tif_dir = str(ad.resolve())
        if ad is None:
            self._apply_all_browse_starts(meta, workdir, self._hints)
            return
        cfg = find_config_conf_near(ad)
        if cfg is not None:
            self._hints.config_file_path = str(cfg.resolve())
            w_cfg = self._opt_fields.get("config_path")
            if isinstance(w_cfg, PathField):
                w_cfg.set_text(str(cfg.resolve()))
        mpath = find_mask_near(ad)
        if mpath is not None:
            self._hints.mask_file_path = str(mpath.resolve())
            w_mask = self._opt_fields.get("mask")
            if isinstance(w_mask, PathField):
                w_mask.set_text(str(mpath.resolve()))
        self._apply_all_browse_starts(meta, workdir, self._hints)

    def _live_refresh_subtract(self, paths: List[str]) -> None:
        meta = self._meta
        if not meta or self._hints is None:
            return
        workdir = self._workdir
        self._sync_one_d_profile_dir_from_paths(paths)
        sample_expr = ", ".join(paths)
        buf = find_single_buffer_dat(sample_expr, workdir)
        if buf is not None:
            w_buf = self._pos_widgets[1]
            if isinstance(w_buf, PathField):
                w_buf.set_text(str(buf.resolve()))
        self._apply_all_browse_starts(meta, workdir, self._hints)

    def _live_refresh_model_mixture(self, paths: List[str]) -> None:
        meta = self._meta
        if not meta or self._hints is None:
            return
        workdir = self._workdir
        self._sync_one_d_profile_dir_from_paths(paths)
        ad = anchor_dir_from_resolved_path_list(paths, workdir)
        if ad is not None:
            cfg = find_config_conf_near(ad)
            if cfg is not None:
                self._hints.config_file_path = str(cfg.resolve())
                w_cfg = self._opt_fields.get("config_path")
                if isinstance(w_cfg, PathField):
                    w_cfg.set_text(str(cfg.resolve()))
        self._apply_all_browse_starts(meta, workdir, self._hints)

    def _live_refresh_analysis_profile(self, paths: List[str]) -> None:
        assert self._hints is not None
        meta = self._meta
        if not meta:
            return
        workdir = self._workdir
        if len(paths) == 1:
            p = resolve_under_workdir(normalize_pathish(paths[0]), workdir)
            if p.is_dir():
                d = str(p.resolve())
                self._hints.subtract_output_dir = d
                self._hints.one_d_profile_dir = d
                self._apply_all_browse_starts(meta, workdir, self._hints)
                return
            if p.is_file():
                d = str(p.parent.resolve())
                self._hints.subtract_output_dir = d
                self._hints.one_d_profile_dir = d
                self._apply_all_browse_starts(meta, workdir, self._hints)
                return
        common = common_parent_dir_if_all_files(paths, workdir)
        if common is None or not common.is_dir():
            return
        d = str(common)
        self._hints.subtract_output_dir = d
        self._hints.one_d_profile_dir = d
        self._apply_all_browse_starts(meta, workdir, self._hints)

    @staticmethod
    def _path_field_is_empty(w: PathField) -> bool:
        return not w.paths() and not w.text().strip()

    def _apply_all_browse_starts(self, meta: SkillMeta, workdir: Path, hints: SessionPathHints) -> None:
        for w in self._pos_widgets:
            if isinstance(w, PathField):
                w.set_browse_start_dir(None)
        for w in self._opt_fields.values():
            if isinstance(w, PathField):
                w.set_browse_start_dir(None)

        for w in list(self._pos_widgets) + list(self._opt_fields.values()):
            if not isinstance(w, PathField):
                continue
            paths = [normalize_pathish(p) for p in w.paths() if normalize_pathish(p)]
            u = browse_start_dir_for_resolved_paths(paths, workdir)
            if u:
                w.set_browse_start_dir(u)

        if meta.name == "subtract":
            start = None
            iod = hints.integrate_output_dir
            if iod and path_exists(iod, workdir):
                start = str(resolve_under_workdir(iod, workdir))
            if start is None:
                opd = hints.one_d_profile_dir
                if opd and path_exists(opd, workdir):
                    start = str(resolve_under_workdir(opd, workdir))
            buf_start = None
            lip = hints.last_integrated_dat_path
            if lip and path_exists(lip, workdir):
                p = resolve_under_workdir(str(lip).strip(), workdir)
                if p.is_file():
                    buf_start = str(p.parent.resolve())
            for i, p in enumerate(meta.positional_params):
                if p.name not in ("sample_1d", "buffer_1d"):
                    continue
                w = self._pos_widgets[i]
                if not isinstance(w, PathField) or not self._path_field_is_empty(w):
                    continue
                if p.name == "buffer_1d" and buf_start:
                    w.set_browse_start_dir(buf_start)
                elif start:
                    w.set_browse_start_dir(start)
        if meta.name in ANALYSIS_SKILLS_WITH_PROFILE:
            start = None
            opd = hints.one_d_profile_dir
            if opd and path_exists(opd, workdir):
                start = str(resolve_under_workdir(opd, workdir))
            if start is None:
                sod = hints.subtract_output_dir
                if sod and path_exists(sod, workdir):
                    start = str(resolve_under_workdir(sod, workdir))
            if start:
                for i, p in enumerate(meta.positional_params):
                    if p.name != "profile":
                        continue
                    w = self._pos_widgets[i]
                    if isinstance(w, PathField) and self._path_field_is_empty(w):
                        w.set_browse_start_dir(start)

    def state(self) -> dict:
        return {
            "skill_name": self._meta.name if self._meta else None,
            "copy_inputs": self._copy_inputs.isChecked(),
            "positional": [self._widget_state(w) for w in self._pos_widgets],
            "options": {k: self._widget_state(v) for k, v in self._opt_fields.items()},
        }

    def set_state(self, state: dict) -> None:
        if not state:
            return
        self._copy_inputs.setChecked(bool(state.get("copy_inputs", False)))
        pos_states = state.get("positional") or []
        for w, s in zip(self._pos_widgets, pos_states):
            self._set_widget_state(w, s)
        opt_states = state.get("options") or {}
        if isinstance(opt_states, dict):
            for k, v in opt_states.items():
                w = self._opt_fields.get(k)
                if w is not None:
                    self._set_widget_state(w, v)

    def build_request(self) -> RunRequest:
        reqs = self.build_requests()
        if len(reqs) != 1:
            raise ValueError("This input represents multiple files; use Run to execute as a batch.")
        return reqs[0]

    def build_requests(self) -> List[RunRequest]:
        if not self._meta:
            raise ValueError("No skill selected")

        options: Dict[str, Any] = {}
        for k, w in self._opt_fields.items():
            if isinstance(w, PathField):
                raw = normalize_pathish(w.text())
                if not str(raw).strip():
                    continue
                options[k] = raw
            elif isinstance(w, QCheckBox):
                options[k] = w.isChecked()
            elif isinstance(w, QLineEdit):
                raw = w.text().strip()
                if raw == "":
                    continue
                options[k] = raw

        for opt in self._meta.option_params:
            if opt.kind != "required_kwonly":
                continue
            val = options.get(opt.name)
            if val is None or str(val).strip() == "":
                raise ValueError(f"{opt.name} is required")

        positional: List[str] = []
        for w in self._pos_widgets:
            if isinstance(w, PathField):
                parts = [normalize_pathish(p) for p in w.paths() if normalize_pathish(p)]
                if not parts:
                    raise ValueError("All positional inputs must be provided")
                positional.append(", ".join(parts))
            elif isinstance(w, QLineEdit):
                raw = w.text().strip()
                if raw == "":
                    raise ValueError("All positional inputs must be provided")
                positional.append(raw)
            else:
                raise TypeError(f"Unsupported positional widget: {type(w)}")

        return [RunRequest(skill_name=self._meta.name, positional=positional, options=options)]

    @staticmethod
    def _is_path_expression_annotation(annotation: Optional[str]) -> bool:
        a = (annotation or "").strip()
        return "PathExpression" in a

    @staticmethod
    def _is_singleton_path_expression_annotation(annotation: Optional[str]) -> bool:
        a = (annotation or "").strip()
        return (
            ("SingletonPathExpression" in a)
            or ("ConfigPathExpression" in a)
            or ("SingletonTiffPathExpression" in a)
            or ("SingletonDatPathExpression" in a)
            or ("SingletonMaskPathExpression" in a)
        )

    @staticmethod
    def _is_config_path_expression_annotation(annotation: Optional[str]) -> bool:
        a = (annotation or "").strip()
        return "ConfigPathExpression" in a

    @staticmethod
    def _expected_exts_for_annotation(annotation: Optional[str]) -> Optional[tuple[str, ...]]:
        a = (annotation or "").strip()
        if "ConfigPathExpression" in a:
            return (".conf", ".yml", ".yaml")
        if "TiffPathExpression" in a or "SingletonTiffPathExpression" in a:
            return (".tif", ".tiff")
        if "DatPathExpression" in a or "SingletonDatPathExpression" in a:
            return (".dat",)
        if "SingletonMaskPathExpression" in a:
            return (".txt", ".npy", ".msk")
        return None

    @staticmethod
    def _clear_layout(layout: QFormLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                break
            w = item.widget()
            if w is not None:
                w.setParent(None)

    @staticmethod
    def _widget_state(w: QWidget):
        if isinstance(w, PathField):
            return w.state()
        if isinstance(w, QCheckBox):
            return bool(w.isChecked())
        if isinstance(w, QLineEdit):
            return w.text()
        return None

    @staticmethod
    def _set_widget_state(w: QWidget, value) -> None:
        if isinstance(w, PathField) and isinstance(value, dict):
            w.set_state(value)
        elif isinstance(w, QCheckBox):
            w.setChecked(bool(value))
        elif isinstance(w, QLineEdit):
            w.setText("" if value is None else str(value))
