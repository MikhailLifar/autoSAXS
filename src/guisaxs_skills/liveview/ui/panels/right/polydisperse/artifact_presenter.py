"""Map skill artifacts into polydisperse window panes (data-driven plots / diagnostics)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .....session.output_paths import fit_sizes_dir, guinier_poly_dir, mixture_dir, tiff_output_root
from .....session.state import LiveviewSessionState, LiveviewWatchMode, PolydisperseMixtureMode
from .....services.artifacts import (
    merge_fit_sizes_quality_fields,
    norm_artifact_path,
    resolve_artifact_path,
)
from ..monodisperse.format_display import format_display_number, is_passport_quality_poor, scalar_value
from autosaxs.skill.gnom_fit_common import failure_message_from_result, is_atsas_fit_ok


class PolydisperseArtifactPresenter:
    def __init__(self, *, state: LiveviewSessionState, window: Any) -> None:
        self._state = state
        self._window = window
        self._profile_path: str = ""
        self._output_root: Optional[Path] = None
        self._last_guinier_results: str = ""
        self._last_gnom_out: str = ""
        self._last_sizes_subdir: str = ""
        self._last_mixture_subdir: str = ""
        self._last_sizes_summary: str = ""
        self._last_mixture_summary: str = ""

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

    def can_rerun_mixture(self) -> bool:
        if self._window.mixture_pane.mixture_mode() == "none":
            return False
        return bool(self._profile_path and os.path.isfile(self._profile_path))

    def clear_views(self) -> None:
        self._window.guinier_pane.clear_view()
        self._window.sizes_pane.clear_view()
        self._window.mixture_pane.clear_view()
        self._last_guinier_results = ""
        self._last_gnom_out = ""
        self._last_sizes_subdir = ""
        self._last_mixture_subdir = ""
        self._last_sizes_summary = ""
        self._last_mixture_summary = ""

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
                # .../fit_sizes/<stem> or .../guinier/<stem> or .../mixture/<stem>
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
        if sn == "fit_guinier" or self._looks_like_fit_guinier(result):
            self._ingest_guinier(result)
        elif sn == "fit_sizes" or self._looks_like_fit_sizes(result):
            self._ingest_sizes(result)
        elif sn == "model_mixture" or result.get("results_csv_path") or result.get("best_label"):
            self._ingest_mixture(result)

    def _looks_like_fit_guinier(self, result: dict) -> bool:
        rp = norm_artifact_path(result.get("results_path"))
        if not rp:
            return False
        name = Path(rp).name.lower()
        return name.endswith("_results.txt") and "kratky" not in name and bool(
            norm_artifact_path(result.get("guinier_plot_path"))
            or "guinier" in str(result.get("output_dir") or "").replace("\\", "/").lower()
            or "guinier" in rp.replace("\\", "/").lower()
        )

    def _looks_like_fit_sizes(self, result: dict) -> bool:
        return bool(
            norm_artifact_path(result.get("best_gnom_out_path"))
            or norm_artifact_path(result.get("dr_csv_path"))
            or result.get("sizes_quality_class")
        )

    def _ingest_guinier(self, result: dict) -> None:
        from autosaxs.core.guinier import parse_guinier_results_txt

        prof = self._effective_profile_path()
        results_path = self._resolve_result_path(result.get("results_path"))
        if results_path:
            self._last_guinier_results = results_path
        data = dict(result) if isinstance(result, dict) else {}
        if results_path and os.path.isfile(results_path):
            parsed = parse_guinier_results_txt(results_path)
            for k, v in parsed.items():
                if k == "methods":
                    continue
                if v is not None and data.get(k) is None:
                    data[k] = v
        if prof and results_path:
            self._window.guinier_pane.show_guinier(prof, results_path)
        try:
            rg = data.get("rg")
            interval = data.get("interval_r2")
            if interval is None:
                interval = data.get("fit_quality")
            if isinstance(interval, (int, float)) and not isinstance(interval, bool):
                interval = format_display_number(interval)
            self._window.guinier_pane.set_diagnostics(
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
                self._window.guinier_pane.set_range(int(fp), int(lp))
                wp = dict(self._state.polydisperse_window_params or {})
                wp["guinier_first"] = int(fp)
                wp["guinier_last"] = int(lp)
                self._state.polydisperse_window_params = wp
        except Exception:
            pass

    def _format_sizes_diagnostics(self, result: dict) -> str:
        """Compact D(R) quality preview — one value per metric, short labels."""
        lines: list[str] = []
        te = scalar_value(result.get("total_estimate"))
        status = scalar_value(result.get("overall_status")) or ""
        head: list[str] = []
        if te is not None:
            head.append(f"TE = {format_display_number(te)}")
        if status:
            head.append(str(status))
        if head:
            lines.append(" · ".join(head))

        s_min = scalar_value(result.get("shannon_s_min"))
        s_class = scalar_value(result.get("shannon_class")) or ""
        if s_min is not None:
            s_line = f"s_min = {format_display_number(s_min)}"
            if s_class and s_class != "unknown":
                s_line += f" ({s_class})"
            lines.append(s_line)

        size_parts: list[str] = []
        d_avg = scalar_value(result.get("d_avg_nm"))
        d_std = scalar_value(result.get("d_std_nm"))
        pdi = scalar_value(result.get("pdi"))
        modality = scalar_value(result.get("modality_class"))
        if d_avg is not None:
            if d_std is not None:
                size_parts.append(f"⟨R⟩ = {format_display_number(d_avg)} ± {format_display_number(d_std)}")
            else:
                size_parts.append(f"⟨R⟩ = {format_display_number(d_avg)}")
        if pdi is not None:
            size_parts.append(f"PDI = {format_display_number(pdi)}")
        if modality:
            size_parts.append(str(modality))
        if size_parts:
            lines.append(" · ".join(size_parts))
        return "\n".join(lines) if lines else "—"

    def _ingest_sizes(self, result: dict) -> None:
        result = merge_fit_sizes_quality_fields(dict(result or {}), watchdir=self._state.watchdir)
        sub = norm_artifact_path(result.get("output_subdir"))
        if sub:
            self._last_sizes_subdir = self._resolve_result_path(sub) or sub
        if not is_atsas_fit_ok(result):
            msg = failure_message_from_result(result, skill_id="fit_sizes")
            self._window.sizes_pane.clear_view()
            self._window.sizes_pane.set_diagnostics(text=msg, poor=True)
            self._last_sizes_summary = msg
            return
        gnom_out = self._resolve_result_path(result.get("best_gnom_out_path"))
        if gnom_out:
            self._last_gnom_out = gnom_out
        if result.get("selected_first") is not None:
            self._window.sizes_pane.set_params({"first": result["selected_first"]})
        if result.get("selected_last") is not None:
            self._window.sizes_pane.set_params({"last": result["selected_last"]})
        prof = self._effective_profile_path()
        if gnom_out and os.path.isfile(gnom_out):
            self._window.sizes_pane.show_sizes(prof, gnom_out)
        else:
            self._window.sizes_pane.clear_view()
        diag = self._format_sizes_diagnostics(result)
        poor = is_passport_quality_poor(
            overall_status=str(scalar_value(result.get("overall_status")) or ""),
            quality_class=str(scalar_value(result.get("sizes_quality_class")) or ""),
            stability_class=str(scalar_value(result.get("stability_class")) or ""),
        )
        self._window.sizes_pane.set_diagnostics(text=diag, poor=poor)
        self._last_sizes_summary = diag.replace("\n", "; ")
        self._window.mixture_pane.set_rerun_enabled(self.can_rerun_mixture())

    def _ingest_mixture(self, result: dict) -> None:
        if self._state.polydisperse_mixture_mode == PolydisperseMixtureMode.NONE:
            return
        sub = self._resolve_result_path(result.get("output_subdir"))
        if sub:
            self._last_mixture_subdir = sub
        csv_path = self._resolve_result_path(result.get("results_csv_path"))
        if not csv_path and sub and os.path.isdir(sub):
            cands = sorted(Path(sub).glob("mixture_results.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
            csv_path = str(cands[0]) if cands else ""
        if not csv_path or not os.path.isfile(csv_path):
            self._window.mixture_pane.clear_view()
            self._window.mixture_pane.set_status("No mixture results CSV", poor=True)
            return
        try:
            import pandas as pd

            df = pd.read_csv(csv_path)
        except Exception:
            self._window.mixture_pane.set_status("Failed to read mixture_results.csv", poor=True)
            return
        if "label" not in df.columns or df.empty:
            self._window.mixture_pane.set_status("Empty mixture results", poor=True)
            return
        best = str(result.get("best_label") or "").strip()
        if not best and "BIC_log" in df.columns:
            try:
                idx = df["BIC_log"].astype(float).idxmin()
                best = str(df.loc[idx, "label"])
            except Exception:
                best = str(df.iloc[0]["label"])
        labels: list[str] = []
        rows: dict[str, dict[str, Any]] = {}
        fit_paths: dict[str, str] = {}
        base = Path(sub) if sub and os.path.isdir(sub) else Path(csv_path).parent
        for _, row in df.iterrows():
            lab = str(row.get("label") or "").strip()
            if not lab:
                continue
            labels.append(lab)
            rows[lab] = {str(k): row[k] for k in df.columns}
            work = base / lab
            if work.is_dir():
                fits = sorted(work.glob("*.fit"), key=lambda p: p.stat().st_mtime, reverse=True)
                if fits:
                    fit_paths[lab] = str(fits[0].resolve())
        # Mirror fit_sizes first/last: fill auto r_max / poly_max from resolved skill values.
        resolved: dict[str, Any] = {}
        r_max = scalar_value(result.get("r_max_nm"))
        poly_max = scalar_value(result.get("poly_max_nm"))
        if r_max is not None:
            try:
                resolved["r_max"] = float(r_max)
            except (TypeError, ValueError):
                pass
        if poly_max is not None:
            try:
                resolved["poly_max"] = float(poly_max)
            except (TypeError, ValueError):
                pass
        if resolved:
            self._window.mixture_pane.set_mixture_params(resolved)
        self._window.mixture_pane.set_fit_models(
            labels=labels,
            rows_by_label=rows,
            fit_paths=fit_paths,
            best_label=best,
        )
        self._last_mixture_summary = f"best={best}" if best else f"models={len(labels)}"

    def load_from_disk(
        self,
        *,
        watchdir: Path,
        stem: str,
        tiff_path: str = "",
        watch_mode: LiveviewWatchMode = LiveviewWatchMode.FLAT,
    ) -> None:
        root = tiff_output_root(watchdir=watchdir, tiff_path=tiff_path, mode=watch_mode)
        self.set_context(
            profile_path=self._profile_path,
            output_root=root,
            tiff_path=tiff_path,
            watch_mode=watch_mode,
        )
        gstem = guinier_poly_dir(root) / stem
        if gstem.is_dir():
            for txt in sorted(gstem.glob("*_results.txt"), key=lambda p: p.stat().st_mtime, reverse=True):
                if "kratky" in txt.name.lower():
                    continue
                self._ingest_guinier({"results_path": str(txt)})
                break
        fs = fit_sizes_dir(root) / stem
        if fs.is_dir():
            gnom = ""
            outs = sorted(fs.glob("*.out"), key=lambda p: p.stat().st_mtime, reverse=True)
            if outs:
                gnom = str(outs[0])
            payload: dict[str, Any] = {
                "atsas_fit_ok": True,
                "output_subdir": str(fs),
                "best_gnom_out_path": gnom,
            }
            best_yml = list(fs.glob("*_fit_sizes_best.yml"))
            if best_yml:
                payload["best_summary_path"] = str(best_yml[0])
            q_yml = list(fs.glob("*_fit_sizes_quality.yml"))
            if q_yml:
                payload["quality_passport_path"] = str(q_yml[0])
            self._ingest_sizes(payload)
        if self._state.polydisperse_mixture_mode == PolydisperseMixtureMode.MIXTURE:
            mx = mixture_dir(root) / stem
            if mx.is_dir():
                csvs = list(mx.glob("mixture_results.csv"))
                payload = {
                    "output_subdir": str(mx),
                    "results_csv_path": str(csvs[0]) if csvs else "",
                }
                self._ingest_mixture(payload)

    def summary_text(self) -> tuple[str, str]:
        hint = "Open Polydisperse analysis for Guinier, D(R), and optional mixture."
        parts: list[str] = []
        if self._last_sizes_summary:
            parts.append(f"d(r): {self._last_sizes_summary}")
        if self._state.polydisperse_mixture_mode == PolydisperseMixtureMode.MIXTURE:
            parts.append(f"Mixture: {self._last_mixture_summary or 'enabled'}")
        else:
            parts.append("Mixture: off")
        return hint, " | ".join(parts) if parts else "—"

    @property
    def profile_path(self) -> str:
        return self._profile_path

    @property
    def output_root(self) -> Optional[Path]:
        return self._output_root
