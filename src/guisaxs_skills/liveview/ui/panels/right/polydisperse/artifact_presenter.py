"""Map skill artifacts into polydisperse window panes (data-driven plots / diagnostics)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .....session.output_paths import (
    analysis_output_root,
    fit_sizes_dir,
    guinier_poly_dir,
    mixture_dir,
    tiff_output_root,
)
from .....session.state import LiveviewSessionState, LiveviewWatchMode, PolydisperseMixtureMode
from .....services.artifacts import (
    merge_fit_sizes_quality_fields,
    norm_artifact_path,
    resolve_artifact_path,
)
from ..monodisperse.format_display import format_display_number, scalar_value
from .format_display import format_sizes_passport_text
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
        self._last_sizes_result: dict = {}

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
        self._last_sizes_result = {}

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
        p = self._state.preferred_profile_path()
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

    def _ingest_sizes(self, result: dict) -> None:
        result = merge_fit_sizes_quality_fields(dict(result or {}), watchdir=self._state.watchdir)
        self._last_sizes_result = dict(result)
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
        prof = self._effective_profile_path()
        if gnom_out and os.path.isfile(gnom_out):
            self._window.sizes_pane.show_sizes(prof, gnom_out)
        else:
            self._window.sizes_pane.clear_view()
        self._window.sizes_pane.set_diagnostics(quality=result)
        self._last_sizes_summary = format_sizes_passport_text(result).replace("\n", "; ")
        self._window.mixture_pane.set_rerun_enabled(self.can_rerun_mixture())
        if gnom_out and os.path.isfile(gnom_out):
            self._update_sizes_params_from_result(result, gnom_out_path=gnom_out)

    def _update_sizes_params_from_result(self, result: dict, *, gnom_out_path: str) -> None:
        """Seed working sizes params; refresh sizes_auto after full auto (not refine) runs."""
        from autosaxs.core.gnom import parse_gnom_out

        snap: dict = {}
        alpha = None
        try:
            parsed = parse_gnom_out(Path(gnom_out_path).read_text(errors="replace"))
            alpha = parsed.get("current_alpha")
            ar = parsed.get("angular_range")
            if isinstance(ar, (tuple, list)) and len(ar) == 2:
                try:
                    q0, q1 = float(ar[0]), float(ar[1])
                    if q0 > 0 and q1 > q0:
                        snap["q_min"] = q0
                        snap["q_max"] = q1
                except (TypeError, ValueError):
                    pass
            if snap.get("rmax_nm") is None and parsed.get("real_space_rmax") is not None:
                snap["rmax_nm"] = float(parsed["real_space_rmax"])
        except Exception:
            parsed = {}
        if "q_min" not in snap:
            q_min = scalar_value(result.get("q_min_fit_nm"))
            if q_min is not None and q_min not in ("", None):
                try:
                    snap["q_min"] = float(q_min)
                except (TypeError, ValueError):
                    pass
        if "q_min" not in snap:
            first = result.get("selected_first")
            if first is None:
                first = result.get("first")
            last = result.get("selected_last")
            if last is None:
                last = result.get("last")
            if first is not None:
                try:
                    snap["first"] = int(scalar_value(first))
                except (TypeError, ValueError):
                    pass
            if last is not None:
                try:
                    snap["last"] = int(scalar_value(last))
                except (TypeError, ValueError):
                    pass
        rmax = scalar_value(result.get("dmax_nm"))
        if rmax is None:
            rmax = scalar_value(result.get("rmax_nm"))
        if rmax is not None and rmax not in ("", None):
            try:
                snap["rmax_nm"] = float(rmax)
            except (TypeError, ValueError):
                pass
        if alpha is not None:
            try:
                snap["alpha"] = float(alpha)
            except (TypeError, ValueError):
                pass
        # Auto runs refresh sizes_auto; refined runs keep the user's boundary conditions.
        is_refined = str(result.get("refined") or "").strip().lower() in ("true", "1", "yes")
        wp = dict(self._state.polydisperse_window_params or {})
        if not is_refined:
            snap["force_zero_rmin"] = "Y"
            snap["force_zero_rmax"] = "Y"
        else:
            for k in ("force_zero_rmin", "force_zero_rmax"):
                if wp.get(k) is not None:
                    snap[k] = wp[k]
                else:
                    snap[k] = "Y"

        for k, v in snap.items():
            wp[k] = v
        if snap.get("q_min") is not None:
            wp.pop("first", None)
        if snap.get("q_max") is not None:
            wp.pop("last", None)
        if not is_refined:
            wp["sizes_auto"] = {
                k: snap[k]
                for k in (
                    "q_min",
                    "q_max",
                    "first",
                    "last",
                    "rmin_nm",
                    "rmax_nm",
                    "alpha",
                    "force_zero_rmin",
                    "force_zero_rmax",
                )
                if k in snap
            }
        self._state.polydisperse_window_params = wp
        try:
            from .config_sync import PolydisperseConfigSync

            PolydisperseConfigSync(state=self._state, window=self._window).persist_confs()
        except Exception:
            pass

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
        from .....services.history.right_artifacts import discover_polydisperse_artifacts

        bundle = discover_polydisperse_artifacts(
            watchdir=watchdir,
            stem=stem,
            tiff_path=tiff_path,
            watch_mode=watch_mode,
            mixture_mode=self._state.polydisperse_mixture_mode,
        )
        self.apply_bundle(bundle)

    def apply_bundle(self, bundle: Any) -> None:
        root = bundle.output_root
        if root is None:
            root = self._state.watchdir.expanduser().resolve()
        self.set_context(
            profile_path=bundle.profile_path or self._profile_path,
            output_root=root,
        )
        if bundle.guinier:
            self._ingest_guinier(bundle.guinier)
        if bundle.sizes:
            self._ingest_sizes(bundle.sizes)
        if bundle.mixture:
            self._ingest_mixture(bundle.mixture)

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

    @property
    def last_gnom_out(self) -> str:
        return self._last_gnom_out

    @property
    def last_sizes_result(self) -> dict:
        return dict(self._last_sizes_result or {})
