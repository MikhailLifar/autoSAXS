"""Map skill artifacts into monodisperse wizard panes (plots / 3D / diagnostics)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .....session.output_paths import (
    dammif_dir,
    fit_bodies_dir,
    fit_distances_dir,
    guinier_mono_dir,
    tiff_output_root,
)
from .....session.state import LiveviewSessionState, LiveviewWatchMode, MonodisperseShapeMode
from .....services.artifacts import (
    best_dammif_cif,
    bodies_best_fit,
    discover_gnom_out_path,
    merge_fit_distances_quality_fields,
    norm_artifact_path,
    resolve_artifact_path,
)
from .format_display import format_display_number, scalar_value
from autosaxs.skill.gnom_fit_common import failure_message_from_result, is_atsas_fit_ok


class MonodisperseArtifactPresenter:
    def __init__(self, *, state: LiveviewSessionState, wizard: Any) -> None:
        self._state = state
        self._wizard = wizard
        self._profile_path: str = ""
        self._output_root: Optional[Path] = None
        self._last_guinier_region: str = ""
        self._last_gnom_out: str = ""
        self._last_fit_distances_subdir: str = ""

    def set_context(
        self,
        *,
        profile_path: str,
        output_root: Path,
        tiff_path: str = "",
        watch_mode: LiveviewWatchMode = LiveviewWatchMode.FLAT,
    ) -> None:
        self._profile_path = (profile_path or "").strip()
        if output_root is not None:
            self._output_root = output_root.expanduser().resolve()
        else:
            self._output_root = tiff_output_root(
                watchdir=self._state.watchdir,
                tiff_path=tiff_path,
                mode=watch_mode,
            )

    def can_rerun_shape(self) -> bool:
        if self._wizard.shape_pane.shape_mode() == "none":
            return False
        if self._wizard.shape_pane.shape_mode() == "dammif":
            return bool(self.gnom_out_for_dammif())
        return bool(self._profile_path and os.path.isfile(self._profile_path))

    def clear_views(self) -> None:
        self._wizard.guinier_pane.clear_view()
        self._wizard.gnom_pane.clear_view()
        self._wizard.shape_pane.clear_view()
        self._last_guinier_region = ""
        self._last_gnom_out = ""
        self._last_fit_distances_subdir = ""

    def _artifact_bases(self) -> list[Path]:
        bases: list[Path] = []
        if self._output_root is not None:
            bases.append(self._output_root.expanduser().resolve())
        bases.append(self._state.watchdir.expanduser().resolve())
        seen: set[str] = set()
        out: list[Path] = []
        for b in bases:
            key = str(b)
            if key in seen:
                continue
            seen.add(key)
            out.append(b)
        return out

    def _effective_profile_path(self) -> str:
        prof = (self._profile_path or "").strip()
        if prof and os.path.isfile(prof):
            return prof
        p = self._state.default_fit_distances_profile_path()
        if p is not None and p.is_file():
            return str(p.resolve())
        return ""

    def _resolve_result_path(self, val: object) -> str:
        return resolve_artifact_path(val, bases=self._artifact_bases())

    def _sync_output_root_from_result(self, result: dict) -> None:
        sub = norm_artifact_path(result.get("output_subdir"))
        if not sub:
            return
        try:
            od = Path(sub).expanduser().resolve()
            if od.is_dir():
                # .../fit_distances/<stem> or .../guinier/<stem>
                self._output_root = od.parent.parent
        except OSError:
            pass

    def ingest_skill_result(self, result: dict, *, skill_name: str = "") -> None:
        if not isinstance(result, dict):
            return
        self._sync_output_root_from_result(result)
        prof = self._effective_profile_path()
        if not self._profile_path and prof:
            self._profile_path = prof
        sn = (skill_name or result.get("skill_name") or "").strip()
        if sn == "fit_guinier" or result.get("guinier_region_path"):
            self._ingest_guinier(result)
        elif sn == "fit_distances" or self._looks_like_fit_distances(result):
            self._ingest_gnom(result)
        elif sn in ("fit_dammif", "fit_bodies") or result.get("output_subdir"):
            self._ingest_shape(result, skill_name=sn)

    def _looks_like_fit_distances(self, result: dict) -> bool:
        return bool(norm_artifact_path(result.get("best_gnom_out_path")))

    def _ingest_guinier(self, result: dict) -> None:
        prof = self._effective_profile_path()
        grp = self._resolve_result_path(result.get("guinier_region_path"))
        if grp:
            self._last_guinier_region = grp
        if prof and grp:
            self._wizard.guinier_pane.show_guinier(prof, grp)
        try:
            if grp and os.path.isfile(grp):
                data = yaml.safe_load(Path(grp).read_text(encoding="utf-8", errors="replace"))
                if isinstance(data, dict):
                    rg = data.get("rg")
                    interval = data.get("interval_r2")
                    if interval is None:
                        interval = data.get("fit_quality")
                    if isinstance(interval, (int, float)) and not isinstance(interval, bool):
                        interval = format_display_number(interval)
                    self._wizard.guinier_pane.set_diagnostics(
                        quality_class=str(data.get("quality_class") or ""),
                        classification=str(data.get("classification") or ""),
                        rg_nm=f"{format_display_number(rg)} nm" if rg is not None else "",
                        interval_r2=str(interval or ""),
                    )
                    fp = data.get("first_point_1based")
                    lp = data.get("last_point_1based")
                    if fp is None or lp is None:
                        try:
                            from autosaxs.skill.fit_guinier.guinier import guinier_point_range_1based

                            fp, lp = guinier_point_range_1based(data)
                        except Exception:
                            fp, lp = None, None
                    if fp is not None and lp is not None:
                        self._wizard.guinier_pane.set_range(int(fp), int(lp))
                        wp = dict(self._state.monodisperse_wizard_params or {})
                        wp["guinier_first"] = int(fp)
                        wp["guinier_last"] = int(lp)
                        self._state.monodisperse_wizard_params = wp
        except Exception:
            pass

    def _format_gnom_diagnostics(self, result: dict) -> str:
        from autosaxs.core.gnom_quality import PrQualityThresholds

        lines: list[str] = []
        te = scalar_value(result.get("total_estimate"))
        if te is None and isinstance(result.get("best_summary_path"), str):
            try:
                summ = yaml.safe_load(Path(result["best_summary_path"]).read_text(encoding="utf-8"))
                if isinstance(summ, dict):
                    sel = summ.get("selected")
                    if isinstance(sel, dict) and sel.get("total_estimate") is not None:
                        te = sel["total_estimate"]
            except Exception:
                pass
        if te is not None:
            lines.append(f"Total est. = {format_display_number(te)}")

        s_min = scalar_value(result.get("shannon_s_min"))
        s_class = scalar_value(result.get("shannon_class")) or "unknown"
        s_status = scalar_value(result.get("overall_status")) or ""
        if s_status in ("", None) and result.get("shannon_ok") is not None:
            s_status = "ok" if scalar_value(result.get("shannon_ok")) else "fail"
        q_min = scalar_value(result.get("q_min_fit_nm"))
        dmax = scalar_value(result.get("dmax_nm"))
        if s_min is not None:
            if q_min is not None and dmax is not None:
                lines.append(
                    f"s_min = (q_min · Dmax) / π = {format_display_number(s_min)} "
                    f"(class {s_class}, status {s_status or '—'})"
                )
            else:
                lines.append(f"s_min = {format_display_number(s_min)} (class {s_class}, status {s_status or '—'})")

        handoff = self.last_guinier_handoff
        rg_g = scalar_value(result.get("rg_guinier_nm"))
        if rg_g is None:
            rg_g = handoff.get("rg")
        i0_g = handoff.get("i0")
        rg_parts: list[str] = []
        if rg_g is not None and scalar_value(rg_g) not in ("", None):
            rg_parts.append(f"Rg_guinier = {format_display_number(rg_g)}")
        if i0_g is not None and scalar_value(i0_g) not in ("", None):
            rg_parts.append(f"I(0)_guinier = {format_display_number(i0_g)}")
        if rg_parts:
            lines.append(", ".join(rg_parts))

        pr_parts: list[str] = []
        rg_pr = result.get("rg_pr_nm")
        if rg_pr is not None and scalar_value(rg_pr) not in ("", None):
            pr_parts.append(f"Rg_P(r) = {format_display_number(rg_pr)}")
        i0_pr = result.get("i0_pr")
        if i0_pr is not None and scalar_value(i0_pr) not in ("", None):
            pr_parts.append(f"I0_P(r) = {format_display_number(i0_pr)}")
        if pr_parts:
            lines.append("; ".join(pr_parts))

        drg = scalar_value(result.get("delta_rg_pct"))
        if drg is not None and drg not in ("", None):
            t = PrQualityThresholds()
            try:
                drg_f = float(drg)
                if drg_f > t.delta_rg_pct_acceptable:
                    drg_status = "failed"
                elif drg_f > t.delta_rg_pct_max:
                    drg_status = "marginal"
                else:
                    drg_status = "ok"
            except (TypeError, ValueError):
                drg_status = scalar_value(result.get("pr_quality_class")) or "—"
            lines.append(f"ΔRg = {format_display_number(drg)}% (status {drg_status})")

        if dmax is not None:
            lines.append(f"Dmax = {format_display_number(dmax)}")

        return "\n".join(lines)

    def _ingest_gnom(self, result: dict) -> None:
        result = merge_fit_distances_quality_fields(dict(result or {}), watchdir=self._state.watchdir)
        sub = norm_artifact_path(result.get("output_subdir"))
        if sub:
            self._last_fit_distances_subdir = self._resolve_result_path(sub) or sub
        if not is_atsas_fit_ok(result):
            msg = failure_message_from_result(result, skill_id="fit_distances")
            self._wizard.gnom_pane.set_diagnostics(text=msg)
            self._wizard.gnom_pane.clear_view()
            return
        gnom_out = self._resolve_result_path(result.get("best_gnom_out_path"))
        if not gnom_out and self._output_root is not None:
            prof = self._effective_profile_path()
            if prof:
                gnom_out = discover_gnom_out_path(
                    profile_abs=prof,
                    output_root=self._output_root,
                    watchdir=self._state.watchdir,
                    hint=self._last_gnom_out,
                )
        if gnom_out:
            self._last_gnom_out = gnom_out
        if not gnom_out or not os.path.isfile(gnom_out):
            self._wizard.gnom_pane.set_diagnostics(
                text=f"GNOM output not found: {gnom_out or '(missing path)'}"
            )
            self._wizard.gnom_pane.clear_view()
            return
        prof = self._effective_profile_path()
        self._wizard.gnom_pane.show_gnom(prof, gnom_out)
        if result.get("selected_first") is not None:
            self._wizard.gnom_pane.set_params({"first": result["selected_first"]})
        if result.get("selected_last") is not None:
            self._wizard.gnom_pane.set_params({"last": result["selected_last"]})
        self._wizard.gnom_pane.set_diagnostics(text=self._format_gnom_diagnostics(result))
        self._wizard.shape_pane.set_rerun_enabled(self.can_rerun_shape())

    def _ingest_shape(self, result: dict, *, skill_name: str) -> None:
        sub = result.get("output_subdir")
        if not isinstance(sub, str) or not sub.strip():
            return
        sd = Path(sub.strip()).expanduser()
        if not sd.is_absolute():
            sd = (self._state.watchdir / sd).resolve()
        if (sd / "bodies_fits.yml").is_file() or skill_name == "fit_bodies":
            self._ingest_bodies(sd)
        elif any(sd.glob("dammif-*.cif")) or (sd / "dammif_fits.yml").is_file() or skill_name == "fit_dammif":
            self._ingest_dammif(sd)

    def _ingest_bodies(self, sd: Path) -> None:
        best_shape, best_params, csv_p = bodies_best_fit(sd)
        if best_shape and csv_p and os.path.isfile(csv_p):
            try:
                import pandas as pd

                df = pd.read_csv(csv_p)
                if "q" in df.columns and "exp" in df.columns and best_shape in df.columns:
                    fir_cands = list(sd.glob(f"{best_shape}*.fir")) + list(sd.glob("*.fir"))
                    fir = str(fir_cands[0]) if fir_cands else ""
                    if fir and os.path.isfile(fir):
                        self._wizard.shape_pane.show_fir(fir, label=best_shape)
                    self._wizard.shape_pane.viewer.set_bodies_analytical(
                        best_shape, best_params or {}, folder=sd
                    )
                    self._wizard.shape_pane.set_status(f"Best: {best_shape}")
                    return
            except Exception:
                pass
        fir_cands = sorted(sd.glob("*.fir"), key=lambda p: p.stat().st_mtime, reverse=True)
        if fir_cands:
            self._wizard.shape_pane.show_fir(str(fir_cands[0]), label="bodies")
        if best_shape:
            self._wizard.shape_pane.viewer.set_bodies_analytical(
                best_shape, best_params or {}, folder=sd
            )
            self._wizard.shape_pane.set_status(f"Best: {best_shape}")
        else:
            self._wizard.shape_pane.set_status("No BODIES fit")

    def _ingest_dammif(self, sd: Path) -> None:
        cif = best_dammif_cif(sd)
        if cif:
            self._wizard.shape_pane.viewer.set_model_path(cif)
        fir_cands = sorted(sd.glob("*.fir"), key=lambda p: p.stat().st_mtime, reverse=True)
        if fir_cands:
            self._wizard.shape_pane.show_fir(str(fir_cands[0]), label="DAMMIF")
        self._wizard.shape_pane.set_status(str(sd.name))

    def load_from_disk(
        self,
        *,
        watchdir: Path,
        stem: str,
        tiff_path: str = "",
        watch_mode: LiveviewWatchMode = LiveviewWatchMode.FLAT,
    ) -> None:
        root = tiff_output_root(watchdir=watchdir, tiff_path=tiff_path, mode=watch_mode)
        self.set_context(profile_path=self._profile_path, output_root=root, tiff_path=tiff_path, watch_mode=watch_mode)
        gstem = guinier_mono_dir(root) / stem
        if gstem.is_dir():
            for yml in sorted(gstem.glob("*_guinier_region.yml"), key=lambda p: p.stat().st_mtime, reverse=True):
                self._ingest_guinier({"guinier_region_path": str(yml)})
                break
        fd = fit_distances_dir(root) / stem
        gnom_out = fd / f"{stem}.out"
        if not gnom_out.is_file():
            outs = sorted(fd.glob("*.out"), key=lambda p: p.stat().st_mtime, reverse=True)
            gnom_out = outs[0] if outs else gnom_out
        if gnom_out.is_file():
            self._ingest_gnom({"best_gnom_out_path": str(gnom_out), "atsas_fit_ok": True, "output_subdir": str(fd)})
        mode = self._state.monodisperse_shape_mode
        if mode == MonodisperseShapeMode.NONE:
            dam = dammif_dir(root) / stem
            fb = fit_bodies_dir(root) / stem
            has_dam = dam.is_dir() and (
                any(dam.glob("dammif-*.cif")) or (dam / "dammif_fits.yml").is_file()
            )
            has_bod = fb.is_dir() and (
                (fb / "bodies_fits.yml").is_file() or any(fb.glob("*.fir"))
            )
            if has_dam and not has_bod:
                mode = MonodisperseShapeMode.DAMMIF
                self._state.monodisperse_shape_mode = mode
                self._wizard.shape_pane.set_shape_mode("dammif")
            elif has_bod and not has_dam:
                mode = MonodisperseShapeMode.BODIES
                self._state.monodisperse_shape_mode = mode
                self._wizard.shape_pane.set_shape_mode("bodies")
        if mode == MonodisperseShapeMode.DAMMIF:
            dam = dammif_dir(root) / stem
            if dam.is_dir():
                self._ingest_dammif(dam)
        elif mode == MonodisperseShapeMode.BODIES:
            fb = fit_bodies_dir(root) / stem
            if fb.is_dir():
                self._ingest_bodies(fb)

    @property
    def last_guinier_handoff(self) -> Dict[str, Any]:
        if not self._last_guinier_region or not os.path.isfile(self._last_guinier_region):
            return {}
        try:
            data = yaml.safe_load(Path(self._last_guinier_region).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @property
    def last_gnom_out(self) -> str:
        return self._last_gnom_out

    def gnom_out_for_dammif(self) -> str:
        """Resolved DATGNOM .out path for manual DAMMIF (discovered under fit_distances/<stem>/)."""
        prof = self._effective_profile_path()
        root = self._output_root or self._state.watchdir
        if not prof:
            return ""
        return discover_gnom_out_path(
            profile_abs=prof,
            output_root=root,
            watchdir=self._state.watchdir,
            hint=self._last_gnom_out or self._last_fit_distances_subdir,
        )

    @property
    def profile_path(self) -> str:
        return self._profile_path

    @property
    def output_root(self) -> Optional[Path]:
        return self._output_root
