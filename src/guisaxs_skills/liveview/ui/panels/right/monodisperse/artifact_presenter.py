"""Map skill artifacts into monodisperse wizard panes (plots / 3D / diagnostics)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .....session.output_paths import (
    dammif_dir,
    denss_dir,
    model_bodies_dir,
    fit_distances_dir,
    guinier_mono_dir,
    tiff_output_root,
)
from .....session.state import LiveviewSessionState, LiveviewWatchMode, MonodisperseShapeMode
from .....pipeline.monodisperse_pipeline import profile_sample_stem
from .....services.artifacts import (
    best_dammif_cif,
    bodies_best_fit,
    discover_gnom_out_path,
    merge_fit_distances_quality_fields,
    norm_artifact_path,
    resolve_artifact_path,
)
from .....services.dam_models import build_dam_model_catalog
from .....services.denss_models import build_denss_model_catalog
from .format_display import format_display_number, scalar_value
from autosaxs.skill.gnom_fit_common import failure_message_from_result, is_atsas_fit_ok


class MonodisperseArtifactPresenter:
    def __init__(self, *, state: LiveviewSessionState, wizard: Any) -> None:
        self._state = state
        self._wizard = wizard
        self._profile_path: str = ""
        self._output_root: Optional[Path] = None
        self._last_guinier_results: str = ""
        self._last_gnom_out: str = ""
        self._last_fit_distances_subdir: str = ""
        self._last_gnom_result: dict = {}

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
        self._last_guinier_results = ""
        self._last_gnom_out = ""
        self._last_fit_distances_subdir = ""
        self._last_gnom_result = {}

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
        if sn == "fit_guinier" or self._looks_like_fit_guinier(result):
            self._ingest_guinier(result)
        elif sn == "fit_distances" or self._looks_like_fit_distances(result):
            self._ingest_gnom(result)
        elif sn in ("model_dam", "model_bodies", "model_density") or result.get("output_subdir"):
            self._ingest_shape(result, skill_name=sn)

    def _looks_like_fit_guinier(self, result: dict) -> bool:
        rp = norm_artifact_path(result.get("results_path"))
        if not rp:
            return False
        name = Path(rp).name.lower()
        # fit_guinier writes ``{stem}_results.txt``; kratky uses ``*_kratky_results.txt``.
        return name.endswith("_results.txt") and "kratky" not in name and bool(
            norm_artifact_path(result.get("guinier_plot_path"))
            or "guinier" in str(result.get("output_dir") or "").replace("\\", "/").lower()
            or "guinier" in rp.replace("\\", "/").lower()
        )

    def _looks_like_fit_distances(self, result: dict) -> bool:
        return bool(norm_artifact_path(result.get("best_gnom_out_path")))

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
            self._wizard.guinier_pane.show_guinier(prof, results_path)
        try:
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

    def _ingest_gnom(self, result: dict) -> None:
        result = merge_fit_distances_quality_fields(dict(result or {}), watchdir=self._state.watchdir)
        self._last_gnom_result = dict(result)
        # Enrich Total Estimate from log when skill return omits it.
        if result.get("total_estimate") in (None, "") and isinstance(result.get("fit_distances_log_path"), str):
            try:
                summ = yaml.safe_load(Path(result["fit_distances_log_path"]).read_text(encoding="utf-8"))
                if isinstance(summ, dict):
                    sel = summ.get("selected")
                    if isinstance(sel, dict) and sel.get("total_estimate") is not None:
                        result["total_estimate"] = sel["total_estimate"]
            except Exception:
                pass
        sub = norm_artifact_path(result.get("output_subdir"))
        if sub:
            self._last_fit_distances_subdir = self._resolve_result_path(sub) or sub
        if not is_atsas_fit_ok(result):
            msg = failure_message_from_result(result, skill_id="fit_distances")
            self._wizard.gnom_pane.clear_view()
            self._wizard.gnom_pane.set_diagnostics(text=msg, poor=True)
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
            self._wizard.gnom_pane.clear_view()
            self._wizard.gnom_pane.set_diagnostics(
                text=f"GNOM output not found: {gnom_out or '(missing path)'}",
                poor=True,
            )
            return
        prof = self._effective_profile_path()
        self._wizard.gnom_pane.show_gnom(prof, gnom_out)
        self._wizard.gnom_pane.set_diagnostics(
            quality=result,
            guinier_handoff=self.last_guinier_handoff,
        )
        self._wizard.shape_pane.set_rerun_enabled(self.can_rerun_shape())
        self._update_gnom_params_from_result(result, gnom_out_path=gnom_out)

    def _update_gnom_params_from_result(self, result: dict, *, gnom_out_path: str) -> None:
        """Seed working GNOM params; refresh auto snapshot after DATGNOM (not refine) runs."""
        from autosaxs.core.gnom import parse_gnom_out

        snap: dict = {}
        first = result.get("selected_first")
        last = result.get("selected_last")
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
        dmax = scalar_value(result.get("dmax_nm"))
        if dmax is not None and dmax not in ("", None):
            try:
                snap["dmax_nm"] = float(dmax)
            except (TypeError, ValueError):
                pass
        alpha = None
        try:
            parsed = parse_gnom_out(Path(gnom_out_path).read_text(errors="replace"))
            alpha = parsed.get("current_alpha")
            if snap.get("dmax_nm") is None and parsed.get("real_space_rmax") is not None:
                snap["dmax_nm"] = float(parsed["real_space_rmax"])
        except Exception:
            parsed = {}
        if alpha is not None:
            try:
                snap["alpha"] = float(alpha)
            except (TypeError, ValueError):
                pass
        snap["force_zero_rmin"] = "Y"
        snap["force_zero_rmax"] = "Y"
        rg = scalar_value(result.get("rg_guinier_nm"))
        if rg is not None and rg not in ("", None):
            try:
                snap["rg_nm"] = float(rg)
            except (TypeError, ValueError):
                pass

        wp = dict(self._state.monodisperse_wizard_params or {})
        for k, v in snap.items():
            wp[k] = v
        is_datgnom = "datgnom" in os.path.basename(gnom_out_path).lower()
        if is_datgnom:
            wp["gnom_auto"] = {
                k: snap[k]
                for k in ("first", "last", "dmax_nm", "alpha", "force_zero_rmin", "force_zero_rmax", "rg_nm")
                if k in snap
            }
        self._state.monodisperse_wizard_params = wp

    def _ingest_shape(self, result: dict, *, skill_name: str) -> None:
        sub = result.get("output_subdir")
        if not isinstance(sub, str) or not sub.strip():
            return
        sd = Path(sub.strip()).expanduser()
        if not sd.is_absolute():
            sd = (self._state.watchdir / sd).resolve()
        mode = self._wizard.shape_pane.shape_mode()
        if (sd / "bodies_fits.yml").is_file() or skill_name == "model_bodies":
            if mode != "bodies":
                return
            self._ingest_bodies(sd)
        elif any(sd.glob("dammif-*.cif")) or (sd / "dammif_fits.yml").is_file() or skill_name == "model_dam":
            if mode != "dammif":
                return
            self._ingest_dammif(sd)
        elif (
            skill_name == "model_density"
            or result.get("density_map_path")
            or any(sd.glob("*_avg.mrc"))
            or any(sd.glob("*_refined.mrc"))
            or any(sd.glob("*_denss_input.dat"))
        ):
            if mode != "denss":
                return
            self._ingest_denss(sd, result=result)

    def _load_shape_artifacts_for_mode(self, *, root: Path, stem: str, mode: str) -> bool:
        """Load on-disk shape artifacts for ``mode`` into the shared viewer. Returns True if loaded."""
        m = (mode or "").strip().lower()
        if m == MonodisperseShapeMode.DAMMIF.value or m == "dammif":
            dam = dammif_dir(root) / stem
            if dam.is_dir() and (
                any(dam.glob("dammif-*.cif")) or (dam / "dammif_fits.yml").is_file()
            ):
                self._ingest_dammif(dam)
                return True
        elif m == MonodisperseShapeMode.BODIES.value or m == "bodies":
            fb = model_bodies_dir(root) / stem
            if fb.is_dir() and (
                (fb / "bodies_fits.yml").is_file() or any(fb.glob("*.fir"))
            ):
                self._ingest_bodies(fb)
                return True
        elif m == MonodisperseShapeMode.DENSS.value or m == "denss":
            dens = denss_dir(root) / stem
            if dens.is_dir() and (
                any(dens.glob("*.mrc"))
                or any(dens.glob("*_denss_input.dat"))
                or any(
                    p.is_dir() and any(p.glob("*_avg.mrc"))
                    for p in dens.iterdir()
                    if p.is_dir()
                )
            ):
                self._ingest_denss(dens)
                return True
        return False

    def refresh_shape_view_for_current_mode(self) -> None:
        """Clear shared shape previews, then reload disk artifacts for the active mode (if any)."""
        pane = self._wizard.shape_pane
        mode = pane.shape_mode()
        pane.clear_view()
        if mode == "none":
            return
        root = self._output_root
        if root is None:
            root = self._state.watchdir.expanduser().resolve()
        prof = self._effective_profile_path()
        loaded = False
        if prof:
            stem = profile_sample_stem(prof)
            if stem:
                loaded = self._load_shape_artifacts_for_mode(root=root, stem=stem, mode=mode)
        if not loaded:
            # clear_view wiped status; restore mode placeholder.
            pane._update_mode_ui()

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
            self._wizard.shape_pane.set_status("No BODIES fit", poor=True)

    def _ingest_dammif(self, sd: Path) -> None:
        catalog = build_dam_model_catalog(sd)
        best = catalog.best()
        if best is not None:
            self._wizard.shape_pane.viewer.set_dam_catalog(catalog)
            if best.fir_path and os.path.isfile(best.fir_path):
                self._wizard.shape_pane.show_fir(best.fir_path, label="DAMMIF (most probable)")
            else:
                fir_cands = sorted(sd.glob("*.fir"), key=lambda p: p.stat().st_mtime, reverse=True)
                if fir_cands:
                    self._wizard.shape_pane.show_fir(str(fir_cands[0]), label="DAMMIF")
            n_models = sum(1 for e in catalog.entries if e.kind == "dam" and e.key.startswith("run-"))
            has_occ = any(e.kind == "occupancy" for e in catalog.entries)
            bits = [sd.name, best.label]
            if n_models:
                bits.append(f"{n_models} run(s)")
            if has_occ:
                bits.append("occupancy map")
            self._wizard.shape_pane.set_status(" · ".join(bits))
        else:
            cif = best_dammif_cif(sd)
            if cif:
                self._wizard.shape_pane.viewer.set_model_path(cif)
            fir_cands = sorted(sd.glob("*.fir"), key=lambda p: p.stat().st_mtime, reverse=True)
            if fir_cands:
                self._wizard.shape_pane.show_fir(str(fir_cands[0]), label="DAMMIF")
            self._wizard.shape_pane.set_status(str(sd.name))

    def _ingest_denss(self, sd: Path, *, result: Optional[dict] = None) -> None:
        catalog = build_denss_model_catalog(sd)
        best = catalog.best()
        if best is None and result:
            # Fallback: skill returned absolute paths before nesting settled.
            dens = (result or {}).get("density_map_path") or ""
            if isinstance(dens, str) and dens.strip() and Path(dens).is_file():
                from .....services.denss_models import DenssModelCatalog, DenssModelEntry

                sig = (result or {}).get("sigma_map_path") or ""
                fit = (result or {}).get("map_fit_path") or ""
                catalog = DenssModelCatalog(
                    entries=[
                        DenssModelEntry(
                            key="primary",
                            label=Path(dens).name,
                            mrc_path=str(Path(dens).resolve()),
                            kind="density",
                            fit_path=str(Path(fit).resolve()) if fit and Path(fit).is_file() else None,
                            sigma_path=str(Path(sig).resolve()) if sig and Path(sig).is_file() else None,
                            is_primary=True,
                        )
                    ],
                    best_key="primary",
                    output_subdir=str(sd.resolve()),
                )
                best = catalog.best()
        if best is not None:
            self._wizard.shape_pane.viewer.set_denss_catalog(catalog)
            fit = best.fit_path
            if fit and os.path.isfile(fit):
                self._wizard.shape_pane.show_map_fit(fit, label="DENSS")
            bits = [sd.name, best.label]
            if best.sigma_path:
                bits.append("σ map")
            self._wizard.shape_pane.set_status(" · ".join(bits))
        else:
            self._wizard.shape_pane.set_status(f"No DENSS density in {sd.name}", poor=True)

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
            for txt in sorted(gstem.glob("*_results.txt"), key=lambda p: p.stat().st_mtime, reverse=True):
                if "kratky" in txt.name.lower():
                    continue
                self._ingest_guinier({"results_path": str(txt)})
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
            fb = model_bodies_dir(root) / stem
            dens = denss_dir(root) / stem
            has_dam = dam.is_dir() and (
                any(dam.glob("dammif-*.cif")) or (dam / "dammif_fits.yml").is_file()
            )
            has_bod = fb.is_dir() and (
                (fb / "bodies_fits.yml").is_file() or any(fb.glob("*.fir"))
            )
            has_denss = dens.is_dir() and (
                any(dens.glob("*.mrc"))
                or any(dens.glob("*_denss_input.dat"))
                or any(p.is_dir() and any(p.glob("*_avg.mrc")) for p in dens.iterdir() if p.is_dir())
            )
            if has_denss and not has_dam and not has_bod:
                mode = MonodisperseShapeMode.DENSS
                self._state.monodisperse_shape_mode = mode
                self._wizard.shape_pane.set_shape_mode("denss")
            elif has_dam and not has_bod and not has_denss:
                mode = MonodisperseShapeMode.DAMMIF
                self._state.monodisperse_shape_mode = mode
                self._wizard.shape_pane.set_shape_mode("dammif")
            elif has_bod and not has_dam and not has_denss:
                mode = MonodisperseShapeMode.BODIES
                self._state.monodisperse_shape_mode = mode
                self._wizard.shape_pane.set_shape_mode("bodies")
        if mode == MonodisperseShapeMode.DAMMIF:
            self._load_shape_artifacts_for_mode(root=root, stem=stem, mode="dammif")
        elif mode == MonodisperseShapeMode.BODIES:
            self._load_shape_artifacts_for_mode(root=root, stem=stem, mode="bodies")
        elif mode == MonodisperseShapeMode.DENSS:
            self._load_shape_artifacts_for_mode(root=root, stem=stem, mode="denss")

    @property
    def last_guinier_handoff(self) -> Dict[str, Any]:
        from autosaxs.core.guinier import parse_guinier_results_txt

        if not self._last_guinier_results or not os.path.isfile(self._last_guinier_results):
            return {}
        try:
            data = parse_guinier_results_txt(self._last_guinier_results)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @property
    def last_gnom_out(self) -> str:
        return self._last_gnom_out

    @property
    def last_gnom_result(self) -> dict:
        return dict(self._last_gnom_result or {})

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
