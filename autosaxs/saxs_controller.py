import json
import logging
import os
import queue
import re
import shutil
import sys
import time
import warnings
import yaml

from .processor import *
from .guinier import run_guinier_analysis
from .cli_interface import PipelineInterrupt
from .viewer import *
from .context import Context
from .event_bus import EventBus, EventType
from .utils import (
    LATEST_STEPS_PATH,
    ATSAS_BIN_PREFIX,
    read_saxs,
    calc_chi2,
    read_bodies_cif,
    compute_dammif_descriptors,
    load_saxs_1d_any,
    ensure_q_nm,
    find_porod_region,
    write_saxs_atsas_format,
)
from . import cli_interface

import numpy as np
import pandas as pd
import matplotlib as mpl
import matplotlib.pyplot as plt
import seaborn as sns

from ase.io import read

# from aiAssistantFramework import lib as ai_lib
# from aiAssistantFramework.lib import llm 
from .foreign.aiAssistantFramework.lib import llm
# from aiAssistantFramework.lib import telegram
# import controller as ai_controller
from .skill.calibrate import calibrate
from .skill.fit_bodies import fit_bodies
from .skill.fit_dammif import fit_dammif
from .skill.fit_mixture import fit_mixture
from .skill.integrate import integrate
from .skill.plot import plot
from .skill.subtract import subtract

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'prompts')
DEBUG = True


def _canon_path(p):
    """Canonical path for deduplication (same file => same string)."""
    if not isinstance(p, str) or not p:
        return p
    try:
        return os.path.realpath(p) if os.path.exists(p) else os.path.normpath(os.path.abspath(p))
    except OSError:
        return os.path.normpath(os.path.abspath(p))


def _dedupe_sort_paths(paths):
    """Return path list deduplicated by canonical path and sorted by basename."""
    seen = {}
    for p in paths:
        canon = _canon_path(p)
        if canon not in seen:
            seen[canon] = p
    return sorted(seen.values(), key=lambda p: os.path.basename(p) if isinstance(p, str) else str(p))


def _dedupe_sort_pairs(pairs):
    """Return list of (sample_path, buffer_path) deduplicated by canonical paths and sorted by sample basename."""
    seen = set()
    unique = []
    for s, b in pairs:
        key = (_canon_path(s), _canon_path(b))
        if key not in seen:
            seen.add(key)
            unique.append((s, b))
    return sorted(unique, key=lambda sb: os.path.basename(sb[0]) if isinstance(sb[0], str) else str(sb[0]))


def _path_debug(label, paths=None, pairs=None):
    """Debug: print path counts (total, unique by string, unique by canonical path)."""
    if pairs is not None:
        n = len(pairs)
        n_unique_str = len(set((s, b) for s, b in pairs))
        n_unique_canon = len(set((_canon_path(s), _canon_path(b)) for s, b in pairs))
        print(f"[path_debug] {label}: pairs total={n} unique_str={n_unique_str} unique_canon={n_unique_canon}", flush=True)
    else:
        paths = paths or []
        n = len(paths)
        n_unique_str = len(set(paths))
        n_unique_canon = len(set(_canon_path(p) for p in paths if isinstance(p, str)))
        print(f"[path_debug] {label}: total={n} unique_str={n_unique_str} unique_canon={n_unique_canon}", flush=True)


BODIES_SHAPES = {
    # radius r, height h
    'cylinder': {
        'r': 'radius',
        'h': 'height',
    },
    # radius of the first ball r1, radius of the second ball r2, their center-to-center distance d
    'dumbbell': {
        'r1': 'radius-1',
        'r2': 'radius-2',
        'd': 'center-to-center distance',
    },
    # semiaxes a, b, c
    'ellipsoid': {
        'a': 'semiaxis a',
        'b': 'semiaxis b',
        'c': 'semiaxis c',
    },
    # radii semiaxes a, c, height h
    'elliptic-cylinder': {
        'a': 'semiaxis a',
        'c': 'semiaxis c',
        'h': 'height',
    },
    # outer radius ro, inner radius ri, height h
    'hollow-cylinder': {
        'ro': 'outer radius',
        'ri': 'inner radius',
        'h': 'height',
    },
    # outer radius ro, inner radius ri
    'hollow-sphere': {
        'ro': 'outer radius',
        'ri': 'inner radius',
    },
    # sides a, b, c
    'parallelepiped': {
        'a': 'side a',
        'b': 'side b',
        'c': 'side c',
    },
    # semiaxes a, c
    'rotation-ellipsoid': {
        'a': 'semiaxis a',
        'c': 'semiaxis c',
    },
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='calibration_app.log',
    filemode='w'
)
logging.captureWarnings(True)


def json_type_caster(s):
    try:
        return json.loads(s)
    except:
        raise ValueError('Incorrect JSON passed')


def save_latest_steps(pipeline_choice, steps):
    """
    Persist the last selected steps so they can be offered as a default later.
    """
    os.makedirs(os.path.dirname(LATEST_STEPS_PATH), exist_ok=True)
    with open(LATEST_STEPS_PATH, 'w') as f:
        yaml.safe_dump({'pipeline': pipeline_choice, 'steps': list(steps)}, f)


class Controller:
    """
    Combines EventBus I/O, processor, and viewer. Holds EventBus and viewer only (no Interface).
    All pipeline I/O goes via EventBus (spec §3).
    """

    def __init__(self, event_bus: EventBus, viewer: Viewer):
        self._event_bus = event_bus
        self.viewer = viewer

    def _send_message(self, text: str) -> None:
        """Publish MESSAGE for the connected Interface to display."""
        self._event_bus.publish(EventType.MESSAGE, {"text": text})

    def _response_queue_get(self):
        """Block until next response; on PROGRAM_INTERRUPTED raise PipelineInterrupt."""
        evt, data = self._response_queue.get()
        if evt == EventType.PROGRAM_INTERRUPTED:
            reason = (data or {}).get("reason", "program interrupted")
            raise PipelineInterrupt(reason)
        return evt, data

    def _request_directory(self, query: str) -> str:
        self._event_bus.publish(EventType.DIRECTORY_REQUESTED, {"query": query})
        evt, data = self._response_queue_get()
        if evt == EventType.DIRECTORY_SPECIFIED:
            return (data or {}).get("path", "")
        raise PipelineInterrupt("directory selection canceled")

    def _request_file(self, directory, query, filepattern="*", obligatory=False,
                      skip_if_exists=True, except_prev_paths=False, allow_same_time=(1, float("inf"))):
        self._event_bus.publish(EventType.FILE_REQUESTED, {
            "directory": directory,
            "query": query,
            "filepattern": filepattern,
            "obligatory": obligatory,
            "skip_if_exists": skip_if_exists,
            "except_prev_paths": except_prev_paths,
            "allow_same_time": allow_same_time,
        })
        evt, data = self._response_queue_get()
        if evt == EventType.FILE_UPLOADED:
            return (data or {}).get("paths", [])
        if evt == EventType.FILE_UPLOAD_CANCELED and not obligatory:
            return []
        raise PipelineInterrupt("file upload canceled or missing")

    def _request_choice(self, query: str, options: dict, default_op: str = "no default") -> str:
        self._event_bus.publish(EventType.CHOICE_REQUESTED, {
            "query": query,
            "options": options or {},
            "default_op": default_op,
        })
        evt, data = self._response_queue_get()
        if evt == EventType.OPTION_CHOSEN:
            return (data or {}).get("choice", "")
        raise PipelineInterrupt("choice canceled")

    def _request_pipeline_steps(self):
        self._event_bus.publish(EventType.PIPELINE_STEPS_REQUESTED, {})
        evt, data = self._response_queue_get()
        if evt == EventType.PIPELINE_STEPS_SPECIFIED:
            d = data or {}
            return d.get("pipeline_choice", "protein_v0"), d.get("steps", [])
        raise PipelineInterrupt("pipeline selection canceled")

    def _request_profile_selection(self, profiles_data: list) -> dict:
        self._event_bus.publish(EventType.PROFILE_SELECTION_REQUESTED, {"profiles_data": profiles_data})
        evt, data = self._response_queue_get()
        if evt == EventType.PROFILE_SELECTION_SPECIFIED:
            return (data or {}).get("selected_profiles", {})
        return {}

    def get_descriptors(self, context: Context, to_analyze_path,
                        dest_dir, fast_forward=False,
                        ):
        results_file, gnom_file = '', ''

        if to_analyze_path:
            os.makedirs(dest_dir, exist_ok=True)

            # ATSAS units (from official docs): Input .dat with q in nm^-1 -> AUTORG/DATGNOM return Rg, Dmax in nm.
            # AUTORG infers q unit from data; we feed plain 3-column (q, I, errors) with q in nm^-1.
            # DATGNOM -r expects Rg in same unit as input (nm). DATPOROD stdout: s_max, MW(Da), path (2nd col = MW in Daltons).

            root, basename = os.path.split(to_analyze_path)
            basename, _ = os.path.splitext(basename)
            results_file = os.path.join(dest_dir, f'{basename}_results.txt')
            gnom_file = os.path.join(dest_dir, f'{basename}.out')
            
            # Define temporary files for capturing tool outputs
            tmp_autorg = os.path.join(dest_dir, f'{basename}_autorg.tmp')
            tmp_datgnom = os.path.join(dest_dir, f'{basename}_datgnom.tmp')
            tmp_datporod = os.path.join(dest_dir, f'{basename}_datporod.tmp')
            tmp_datmw = os.path.join(dest_dir, f'{basename}_datmw.tmp')

            # Check if analysis results exist in debug mode
            if fast_forward and all(os.path.exists(pp) for pp in (results_file, gnom_file)):
                self._send_message(f'Fast-forward: Skipping analysis for {to_analyze_path} (results already exist)')
                return results_file, gnom_file
            
            # Helper to quote paths for shell commands
            def q(path):
                return f'"{path}"'

            # Initialize descriptor variables
            rg_val = None
            i0_val = None
            quality_val = None
            dmax_val = None
            porod_vol = None
            mw_rg = None
            mw_porod = None
            mw_datmw = None
            guinier_region = None
            porod_region = None
            rg_source = None  # 'guinier_fit' or 'autorg'
            atsas_dat_path = to_analyze_path  # fallback if we never write ATSAS format

            # --- Step 0: Load 1D data, ensure q in nm^-1, write ATSAS file, run AUTORG, then Guinier ---
            # Pipeline units: q in nm^-1, Rg in nm. ATSAS expects plain 3-column .dat (q, I, errors).
            try:
                q_arr, I_arr, sigma_arr = load_saxs_1d_any(to_analyze_path)
                q_arr, I_arr, sigma_arr = ensure_q_nm(q_arr, I_arr, sigma_arr)
                atsas_dat_path = os.path.join(dest_dir, f'{basename}_atsas.dat')
                write_saxs_atsas_format(atsas_dat_path, q_arr, I_arr, sigma_arr)
            except Exception as e:
                if DEBUG:
                    self._send_message(f'Load/write ATSAS failed: {e}')
                atsas_dat_path = to_analyze_path

            # --- Step 1: Guinier analysis (all methods + selection) via processor ---
            guinier_results = None
            guinier_region = None
            porod_region = None
            try:
                q_arr, I_arr, sigma_arr = load_saxs_1d_any(to_analyze_path)
                q_arr, I_arr, sigma_arr = ensure_q_nm(q_arr, I_arr, sigma_arr)
                guinier_results = run_guinier_analysis(
                    q_arr, I_arr, sigma_arr, atsas_dat_path=atsas_dat_path
                )
                chosen = guinier_results.get('chosen')
                if chosen is not None:
                    rg_val = guinier_results['chosen_Rg']
                    i0_val = guinier_results.get('chosen_I0')
                    quality_val = guinier_results.get('chosen_quality')
                    rg_source = chosen
                    ch_int = guinier_results.get('chosen_interval')
                    chosen_result = guinier_results.get(chosen) or {}
                    guinier_region = {
                        'rg': rg_val,
                        'i0': i0_val,
                        'q_min': ch_int[0] if ch_int else None,
                        'q_max': ch_int[1] if ch_int else None,
                        'r_squared': quality_val,
                        'n_points': guinier_results.get('chosen_n_points'),
                        'sigma_rg': chosen_result.get('sigma_rg'),
                        'sigma_i0': chosen_result.get('sigma_i0'),
                    }
                chosen_rg = rg_val if rg_val is not None else None
                porod_region = find_porod_region(q_arr, I_arr, Rg=chosen_rg)
            except Exception as e:
                if DEBUG:
                    self._send_message(f'Guinier/Porod region search failed: {e}')
                guinier_results = None
                guinier_region = None
                porod_region = None

            # --- Step 1.5: Calculate P(r) using DATGNOM ---
            # Requires Rg. Only run if Rg was successfully determined.
            if rg_val is not None:
                cmd_datgnom = f'datgnom {q(atsas_dat_path)} -r {rg_val} -o {q(gnom_file)}'
                os.system(f'{cmd_datgnom} > {q(tmp_datgnom)} 2>&1')

                # Parse Dmax: datgnom often writes nothing to stdout; get it from the .out file
                if os.path.exists(tmp_datgnom):
                    with open(tmp_datgnom, 'r') as f:
                        content = f.read()
                        match = re.search(r'Dmax\s+=\s+([\d\.]+)', content)
                        if match:
                            dmax_val = float(match.group(1))
                if dmax_val is None and os.path.exists(gnom_file):
                    with open(gnom_file, 'r') as f:
                        content = f.read()
                        match = re.search(r'Maximum characteristic size:\s+([\d\.]+)', content)
                        if match:
                            dmax_val = float(match.group(1))
            else:
                print("Warning: Rg not found, skipping DATGNOM.")

            # --- Step 2: Calculate Porod volume / MW using DATPOROD ---
            # Requires the .out file from GNOM. ATSAS manual: "prints s_max, the volume estimate (Da), and the file name".
            # The second column is molecular weight in Daltons (not volume); we convert to kDa and back-calc volume for display.
            if os.path.exists(gnom_file):
                cmd_datporod = f'datporod {q(gnom_file)}'
                os.system(f'{cmd_datporod} > {q(tmp_datporod)} 2>&1')

                if os.path.exists(tmp_datporod):
                    with open(tmp_datporod, 'r') as f:
                        content = f.read().strip()
                        parts = content.split()
                        if len(parts) >= 2:
                            try:
                                mw_da = float(parts[1])
                                mw_porod = mw_da / 1000.0  # Da -> kDa
                                # Back-calculate volume in nm^3 for report (MW_kDa ≈ V_nm3 * 0.824)
                                porod_vol = mw_porod / 0.824 if mw_porod > 0 else None
                            except ValueError:
                                pass

            # --- Step 3: Calculate Molecular Weight Estimates ---
            # Method 1: From Rg (Empirical relationship for globular proteins)
            # MW (kDa) = (Rg / 0.715)^3  (Rg in nm)
            if rg_val is not None:
                mw_rg = (rg_val / 0.715)**3

            # Method 2: From DATPOROD (already set above as mw_porod in kDa; porod_vol is back-calculated for display)

            # Method 3: Using DATMW (if on PATH). Output: one line with columns; 4th column (index 3) is MW in Daltons.
            # On error DATMW writes e.g. " path: error: rg/i0 required" - do not use in that case.
            if shutil.which('datmw'):
                cmd_datmw = f'datmw {q(atsas_dat_path)}'
                os.system(f'{cmd_datmw} > {q(tmp_datmw)} 2>&1')
                if os.path.exists(tmp_datmw):
                    with open(tmp_datmw, 'r') as f:
                        content = f.read()
                    if 'error' not in content.lower():
                        match = re.search(r'MW\s+:\s+([\d\.]+)', content)
                        if match:
                            mw_datmw = float(match.group(1))
                        else:
                            # Column format: e.g. "350.000  0.73E-02  0.0  372700.  0.905  path"
                            parts = content.split()
                            if len(parts) >= 4:
                                try:
                                    mw_datmw = float(parts[3]) / 1000.0  # Da -> kDa
                                except ValueError:
                                    pass

            # --- Step 4: Write results to file ---
            with open(results_file, 'w') as f:
                f.write("SAXS Analysis Results\n")
                f.write("====================\n")
                f.write(f"Input file: {to_analyze_path}\n")
                f.write(f"Analysis date: {time.ctime()}\n")
                f.write("\n")
                f.write("Guinier: first5, first10, autorg, adaptive (sliding window, selected by validation R² on [q_max/2, q_max]).\n")
                f.write("Chosen = adaptive when available. Classification in [0,q_max/2].\n")
                f.write("\n")
                f.write("Chosen Guinier result (used downstream):\n")
                if guinier_region is not None:
                    sr = guinier_region.get('sigma_rg')
                    si = guinier_region.get('sigma_i0')
                    f.write(f"  Source = {rg_source}\n")
                    f.write(f"  Rg = {guinier_region['rg']:.4f} nm\n")
                    if sr is not None:
                        f.write(f"  Rg StDev = {sr:.4g} nm\n")
                    if guinier_region.get('i0') is not None:
                        f.write(f"  I(0) = {guinier_region['i0']:.4g}\n")
                    if si is not None:
                        f.write(f"  I(0) StDev = {si:.4g}\n")
                    qmn, qmx = guinier_region.get('q_min'), guinier_region.get('q_max')
                    if qmn is not None and qmx is not None:
                        f.write(f"  q range = [{qmn:.5g}, {qmx:.5g}] nm^-1\n")
                    if guinier_region.get('n_points') is not None:
                        f.write(f"  n points = {guinier_region['n_points']}\n")
                    if guinier_region.get('r_squared') is not None:
                        f.write(f"  R^2 = {guinier_region['r_squared']:.4f}\n")
                    if guinier_results is not None:
                        val_r2 = guinier_results.get('chosen_validation_r2')
                        if val_r2 is not None:
                            f.write(f"  validation R^2 (on [q_max/2, q_max]) = {val_r2:.4f}\n")
                        cl = guinier_results.get('classification')
                        if cl is not None:
                            f.write(f"  classification ([0, q_max/2]) = {cl}\n")
                            if cl == 'linear':
                                f.write("    (good fit; narrow unimodal size distribution)\n")
                            elif cl == 'upturn':
                                f.write("    (intensity above fit; aggregation / large particle contamination likely)\n")
                            elif cl == 'downturn':
                                f.write("    (intensity below fit; repulsion / bad subtraction likely)\n")
                            elif cl == 'chaotic':
                                f.write("    (large non-systematic deviations; polydisperse or corrupted sample)\n")
                else:
                    f.write("  No valid Guinier result chosen.\n")
                f.write("\n")
                f.write("All Guinier methods (Rg, n_points, fit_quality, guinier_interval, validation_r2):\n")
                if guinier_results is not None:
                    for method in ('first5', 'first10', 'autorg', 'adaptive'):
                        r = guinier_results.get(method)
                        mark = " [CHOSEN]" if guinier_results.get('chosen') == method else ""
                        if r is not None:
                            rg = r.get('Rg')
                            np_ = r.get('n_points')
                            qq = r.get('fit_quality')
                            interval = r.get('guinier_interval')
                            val_r2 = r.get('validation_r2')
                            rg_s = f"{rg:.4f}" if rg is not None else "N/A"
                            np_s = str(np_) if np_ is not None else "N/A"
                            qq_s = f"{qq:.4f}" if qq is not None else "N/A"
                            int_s = f"[{interval[0]:.5g}, {interval[1]:.5g}]" if interval and interval[0] is not None and interval[1] is not None else "N/A"
                            val_s = f"{val_r2:.4f}" if val_r2 is not None else "N/A"
                            f.write(f"  {method}: Rg={rg_s} nm, n_points={np_s}, fit_quality={qq_s}, interval={int_s}, validation_r2={val_s}{mark}\n")
                        else:
                            f.write(f"  {method}: (no result)\n")
                else:
                    f.write("  (Guinier analysis not run or failed.)\n")
                f.write("\n")
                f.write("Porod region (theoretical high-q range, q >= 2/Rg):\n")
                if porod_region is not None:
                    if porod_region.get('theoretical_range_absent'):
                        f.write("  FLAG: Theoretical high-q range not in data. Porod analysis not run.\n")
                        f.write(f"  q_min_required (2/Rg) = {porod_region['q_min_required']:.5g} nm^-1\n")
                        f.write(f"  q_max in data = {porod_region['q_max_data']:.5g} nm^-1\n")
                        f.write(f"  Rg used = {porod_region['Rg']:.4f} nm\n")
                    elif 'slope' in porod_region:
                        f.write(f"  slope (log I vs log q) = {porod_region['slope']:.3f} (nominal -4)\n")
                        f.write(f"  q range = [{porod_region['q_min']:.5g}, {porod_region['q_max']:.5g}] nm^-1\n")
                        f.write(f"  n points = {porod_region['n_points']}\n")
                        if porod_region.get('theoretical_range_used'):
                            f.write(f"  q_min_required (2/Rg) = {porod_region.get('q_min_required', 'N/A')} nm^-1\n")
                        elif porod_region.get('theoretical_range_checked') is False:
                            f.write("  CAUTION: Rg was not available; theoretical high-q range was not applied.\n")
                    else:
                        f.write("  No valid Porod region found.\n")
                else:
                    f.write("  No valid Porod region found (or Rg not available for theoretical range).\n")
                f.write("\n")
                f.write("Descriptors (used downstream):\n")
                f.write(f"  Rg = {rg_val if rg_val else 'N/A'} nm\n")
                f.write(f"  I(0) = {i0_val if i0_val else 'N/A'}\n")
                f.write(f"  Quality = {quality_val if quality_val else 'N/A'}\n")
                f.write("\n")
                f.write("GNOM Results:\n")
                f.write(f"  Dmax = {dmax_val if dmax_val else 'N/A'} nm\n")
                f.write("\n")
                f.write("Porod volume (DATPOROD):\n")
                f.write(f"  Porod Volume = {porod_vol if porod_vol else 'N/A'} nm^3\n")
                f.write("\n")
                f.write("Molecular Weight Estimates:\n")
                if mw_rg:
                    f.write(f"  From Rg (globular): {mw_rg:.2f} kDa\n")
                if mw_porod:
                    f.write(f"  From Porod Volume: {mw_porod:.2f} kDa\n")
                if mw_datmw:
                    f.write(f"  From DATMW: {mw_datmw:.2f} kDa\n")
                f.write("\n")

            # # Remove temporary files after use
            # for tmp_path in (tmp_autorg, tmp_datgnom, tmp_datporod, tmp_datmw):
            #     if os.path.exists(tmp_path):
            #         try:
            #             os.remove(tmp_path)
            #         except OSError:
            #             pass

        return results_file, gnom_file

    def ai_analysis(self, atsas_analysis_path, plot_paths, dest_dir,
                    text_model, vision_model,
                    fast_forward=False):
        answer = ''
        llm_answer_path = ''

        if atsas_analysis_path:
            os.makedirs(dest_dir, exist_ok=True)

            sub_plot_path, guinier_plot_path, kratky_plot_path, loglog_plot_path = plot_paths
            p, basename = os.path.split(sub_plot_path)
            basename, _ = os.path.splitext(basename)
            context_path = os.path.join(dest_dir, f'{basename}_context.txt')
            llm_answer_path = os.path.join(dest_dir, f'{basename}_llm_answer.txt')

            if fast_forward and os.path.exists(context_path):
                with open(context_path, 'r') as fread:
                    sample_context = fread.read()
                self._send_message(f'Fast-forward: Skipping visual analysis for {basename} (results already exist)')
            
            else:
                sample_context = []
                with open(atsas_analysis_path, 'r') as fread:
                    sample_context.append(f'{basename} sample analysis results:\n{fread.read()}')

                with open(os.path.join(PROMPTS_DIR, 'visual', 'saxs_1d.txt'), 'r') as fread:
                    saxs_prompt = fread.read()
                with open(os.path.join(PROMPTS_DIR, 'visual', 'guinier_plot.txt'), 'r') as fread:
                    guinier_prompt = fread.read()
                with open(os.path.join(PROMPTS_DIR, 'visual', 'kratky_plot.txt'), 'r') as fread:
                    kratky_prompt = fread.read()
                with open(os.path.join(PROMPTS_DIR, 'visual', 'loglog_plot.txt'), 'r') as fread:
                    loglog_prompt = fread.read()
                
                messages = get_image_messages(sub_plot_path, saxs_prompt)
                sub_description, _ = llm.send_request_to_llm(model=vision_model, messages=messages)
                sample_context.append(f'The description of 1d raw SAXS curve:\n{sub_description}')

                messages = get_image_messages(guinier_plot_path, guinier_prompt)
                guinier_description, _ = llm.send_request_to_llm(model=vision_model, messages=messages)
                sample_context.append(f'The description of Guinier plot:\n{guinier_description}')

                messages = get_image_messages(kratky_plot_path, kratky_prompt)
                kratky_description, _ = llm.send_request_to_llm(model=vision_model, messages=messages)
                sample_context.append(f'The description of Kratky plot:\n{kratky_description}')

                messages = get_image_messages(loglog_plot_path, loglog_prompt)
                loglog_description, _ = llm.send_request_to_llm(model=vision_model, messages=messages)
                sample_context.append(f'The description of log-log plot:\n{loglog_description}')

                sample_context = '\n\n'.join(sample_context)
                with open(context_path, 'w') as fwrite:
                    fwrite.write(sample_context)

            if fast_forward and os.path.exists(llm_answer_path):
                with open(llm_answer_path, 'r') as fread:
                    answer = fread.read()
                self._send_message(f'Fast-forward: Skipping LLM analysis for {basename} (results already exist)')

            else:
                context = sample_context
                self._send_message('Now the results of your data processing are sent to LLM for the intelligent analysis.')
                user_query = self._request_choice('What is your query to LLM?', options={})
                answer, _ = llm.send_request_to_llm(
                    model=text_model, 
                    messages=[
                        {'role': 'user', 'content': [{'type': 'text', 'text': f'{context}\n\nUser query: {user_query}'}]}
                    ],
                )
                with open(llm_answer_path, 'w') as fwrite:
                    fwrite.write(answer)
                self._send_message(f'LLM asnwer:\n{answer}')
        
        return answer, llm_answer_path

    def subtract(self, context, sample_path, buffer_path, dest_dir=None, fast_forward=False):
        """
        Buffer subtraction using config section ``sub`` (q range, method, fit forms).
        Returns ``(subtracted_dat_path, sub_plot_path)``.
        """
        sub_cfg = (context.config or {}).get("sub", {}) if context and context.config else {}
        q_range_abs = sub_cfg.get("q_range_abs")
        q_sub_min = q_range_abs[0] if q_range_abs and len(q_range_abs) >= 2 else None
        q_sub_max = q_range_abs[1] if q_range_abs and len(q_range_abs) >= 2 else None
        out = subtract(
            sample_path,
            buffer_path,
            output_dir=dest_dir if dest_dir is not None else ".",
            q_min=q_sub_min,
            q_max=q_sub_max,
            method=sub_cfg.get("method", "point_match"),
            sample_form=sub_cfg.get("sample_form", "Porod-plus-linear"),
            buffer_form=sub_cfg.get("buffer_form", "linear"),
            point_match_factor=float(sub_cfg.get("point_match_factor", 0.995)),
            use_cache=fast_forward,
        )
        return out["subtracted_1d"], out["sub_plot_path"]

    def pipeline_interactive(self, fast_forward=False):
        # Wire response queue for request/response over EventBus (§3)
        self._response_queue = queue.Queue()

        def put_response(evt_type):
            def _handler(data):
                self._response_queue.put((evt_type, data))
            return _handler

        for evt in (
            EventType.DIRECTORY_SPECIFIED,
            EventType.FILE_UPLOADED,
            EventType.FILE_UPLOAD_CANCELED,
            EventType.OPTION_CHOSEN,
            EventType.OPTION_CHOICE_CANCELED,
            EventType.PROGRAM_INTERRUPTED,
            EventType.PIPELINE_STEPS_SPECIFIED,
            EventType.PROFILE_SELECTION_SPECIFIED,
        ):
            self._event_bus.subscribe(evt, put_response(evt))

        # model = 'GLM-4.6'
        # model = 'DeepSeek-V3.1'
        model = 'Llama-4-Maverick-17B-128E-Instruct-FP8'
        # vision_model = 'GLM-4.5V'
        vision_model = 'Llama-4-Maverick-17B-128E-Instruct-FP8'

        context = Context()

        pipeline_choice, steps = self._request_pipeline_steps()
        save_latest_steps(pipeline_choice, steps)
        directory = self._request_directory('Write a path to a directory for your data')
        context.set_directory(directory)
        config_paths = self._request_file(
            directory,
            query='Upload config file config.conf to your directory',
            filepattern='config.conf',
            obligatory=True,
            skip_if_exists=True,
            allow_same_time=(1, 1),
        )
        config_path, = config_paths
        context.set_config(config_path)
        config = context.config

        integrator_dir = None
        if 'calibration' in steps:
            calibrant_paths = self._request_file(
                directory,
                query='Upload raw/*_calib.tif file with calibration data',
                filepattern='raw/*_calib.tif',
                obligatory=True,
                skip_if_exists=True,
                allow_same_time=(1, 1),
            )
            calibrant_path = calibrant_paths[0] if calibrant_paths else None
            if calibrant_path:
                context.append_path('calib_2d', calibrant_path)
            mask_op = self._request_choice(
                "How do you want to set the mask for calibration?",
                options={'a': 'automask', 'f': 'from file', 'c': 'combine your mask with automatic mask'},
            )
            mapping = {'a': 'auto', 'f': 'from_file', 'c': 'combined'}
            context.update_config('mask_config', values={'mode': mapping[mask_op]})
            mask_path = None
            if context['mask_config', 'mode'] in ['from_file', 'combined']:
                mask_paths = self._request_file(
                    directory,
                    query='Upload mask* file with mask (supported extensions are .msk, .npy, .txt)',
                    filepattern='mask*',
                    obligatory=True,
                    skip_if_exists=True,
                    allow_same_time=(1, 1),
                )
                mask_path = mask_paths[0] if mask_paths else None
                if mask_path:
                    context.append_path('calib_mask', mask_path)
            if calibrant_path:
                out_cal = calibrate(
                    calibrant_path,
                    config_path,
                    directory,
                    mask=mask_path,
                    use_cache=fast_forward,
                )
                integrator_dir = out_cal['integrator_dir']
                with open(out_cal['refined_path'], 'r') as f:
                    refined = yaml.safe_load(f)
                context['refined'] = refined
                context.update_config('refined', values=refined)

        if 'integration' in steps and 'calibration' not in steps:
            ai_subdir = 'integrator_params'

            def exit_condition():
                return all(
                    os.path.exists(os.path.join(directory, ai_subdir, p))
                    for p in ['ai_params.json', 'detector_params.json', 'mask.npy']
                )

            while not exit_condition():
                self._send_message(
                    f'Integration requires calibrated geometry parameters and a mask.\n'
                    f'Provide them by uploading directory named "{ai_subdir}" which contains:\n'
                    f'ai_params.json\n'
                    f'detector_params.json\n'
                    f'mask.npy\n'
                )
                self._request_file(
                    directory,
                    query=f'Upload directory named "{ai_subdir}" to your working directory',
                    filepattern='integrator_params',
                    obligatory=True,
                    skip_if_exists=True,
                    allow_same_time=(1, 1),
                )
                time.sleep(2.0)
                if not exit_condition():
                    self._send_message(f'Wrong "{ai_subdir}" directory structure. Reupload')
            integrator_dir = os.path.join(directory, ai_subdir)

        run_process_cycle = True
        iteration_number = 0
        while run_process_cycle:
            buffer_paths_1d = sample_paths_1d = None
            basename_list = []
            if 'integration' in steps:
                fallback_delay = 10.0
                buffer_paths = []
                run_load_cycle = True
                while run_load_cycle:
                    if 'subtraction' in steps:
                        buffer_paths = self._request_file(
                            directory,
                            query='Upload buffer 2d data to "raw" subdirectory raw/*_buffer.tif',
                            filepattern='raw/*_buffer.tif',
                            skip_if_exists=True,
                            except_prev_paths=False,
                        )
                    sample_paths = self._request_file(
                        directory,
                        query='Upload sample 2d data to "raw" subdirectory raw/*_sample.tif',
                        filepattern='raw/*_sample.tif',
                        skip_if_exists=True,
                        except_prev_paths=context['paths', 'sample_2d'],
                    )
                    _path_debug("2D after _request_file buffer_paths", buffer_paths)
                    _path_debug("2D after _request_file sample_paths", sample_paths)
                    if buffer_paths:
                        buffer_paths = _dedupe_sort_paths(buffer_paths)
                    sample_paths = _dedupe_sort_paths(sample_paths)
                    _path_debug("2D after _dedupe_sort_paths buffer_paths", buffer_paths)
                    _path_debug("2D after _dedupe_sort_paths sample_paths", sample_paths)

                    run_load_cycle = False
                    if 'subtraction' in steps:
                        alignment_res = map_sample_files_to_buffer_files(sample_paths, buffer_paths)
                        _path_debug("2D alignment_res aligned_pairs", pairs=alignment_res['aligned_pairs'])
                        _path_debug("2D alignment_res overlapped", pairs=alignment_res['overlapped'])
                        run_load_cycle = alignment_res['overlapped'] or alignment_res['not_paired']
                        if alignment_res['overlapped']:
                            overlap_str = '\n'.join([', '.join(p) for p in alignment_res['overlapped']])
                            self._send_message(f"For some sample files more than one buffer files were found:\n{overlap_str[:2000]}\n\nAre you following name conventions?")
                        if alignment_res['not_paired']:
                            not_paired_str = '\n'.join(alignment_res['not_paired'])
                            self._send_message(f"Not for all sample files buffer files were found:\n{not_paired_str}\n\nAre you following name conventions?")
                        if run_load_cycle:
                            self._send_message(f"Make sure that you follow the name convention and that for each sample image there is exactly one buffer image. This error can also disappear buy itself for the next iteration")
                            time.sleep(fallback_delay)
                        else:
                            aligned_pairs_clean = _dedupe_sort_pairs(alignment_res['aligned_pairs'])
                            buffer_paths = [b_p for _, b_p in aligned_pairs_clean]
                
                if sample_paths:
                    basename_list = [
                        os.path.splitext(os.path.split(sample_path)[1])[0]
                        for sample_path in sample_paths
                    ]

                averaged_dir = os.path.join(directory, 'averaged')
                buffer_2d_to_1d = {}
                sample_2d_to_1d = {}
                if buffer_paths:
                    out_buf = integrate(
                        buffer_paths,
                        integrator_dir,
                        averaged_dir,
                        use_cache=fast_forward,
                    )
                    integrated_buf = out_buf['integrated_1d']
                    integrated_buf_list = integrated_buf if isinstance(integrated_buf, list) else [integrated_buf]
                    buffer_2d_to_1d = dict(zip(buffer_paths, integrated_buf_list))
                    _path_debug("integrate output buffer_2d_to_1d values (1d paths)", list(buffer_2d_to_1d.values()))
                if sample_paths:
                    out_sam = integrate(
                        sample_paths,
                        integrator_dir,
                        averaged_dir,
                        use_cache=fast_forward,
                    )
                    integrated_sam = out_sam['integrated_1d']
                    integrated_sam_list = integrated_sam if isinstance(integrated_sam, list) else [integrated_sam]
                    sample_2d_to_1d = dict(zip(sample_paths, integrated_sam_list))
                    _path_debug("integrate output sample_2d_to_1d values (1d paths)", list(sample_2d_to_1d.values()))
                if buffer_paths and sample_paths:
                    alignment_res = map_sample_files_to_buffer_files(sample_paths, buffer_paths)
                    aligned_pairs_2d = _dedupe_sort_pairs(alignment_res['aligned_pairs'])
                    _path_debug("after 2D alignment aligned_pairs_2d", pairs=aligned_pairs_2d)
                    sample_paths_1d = _dedupe_sort_paths([sample_2d_to_1d[s] for s, _ in aligned_pairs_2d])
                    buffer_paths_1d = _dedupe_sort_paths([buffer_2d_to_1d[b] for _, b in aligned_pairs_2d])
                    _path_debug("after build from aligned_pairs_2d sample_paths_1d", sample_paths_1d)
                    _path_debug("after build from aligned_pairs_2d buffer_paths_1d", buffer_paths_1d)
                elif sample_paths:
                    sample_paths_1d = _dedupe_sort_paths(list(sample_2d_to_1d.values()))
                    buffer_paths_1d = []
                else:
                    sample_paths_1d = []
                    buffer_paths_1d = []
                context.extend_paths('buffer_2d', buffer_paths)
                context.extend_paths('sample_2d', sample_paths)

            if 'subtraction' in steps and 'integration' not in steps:
                fallback_delay = 10.0
                buffer_paths = []
                run_load_cycle = True
                while run_load_cycle:
                    buffer_paths_1d = self._request_file(
                        directory,
                        query='Upload buffer 1d data to "averaged" subdirectory averaged/*_buffer.dat',
                        filepattern='averaged/*_buffer.dat',
                        skip_if_exists=True,
                        except_prev_paths=False,
                    )
                    sample_paths_1d = self._request_file(
                        directory,
                        query='Upload sample 1d data to "averaged" subdirectory averaged/*_sample.dat',
                        filepattern='averaged/*_sample.dat',
                        skip_if_exists=True,
                        except_prev_paths=context['paths', 'sample_1d'],
                    )
                    _path_debug("1D-only after _request_file buffer_paths_1d", buffer_paths_1d)
                    _path_debug("1D-only after _request_file sample_paths_1d", sample_paths_1d)
                    buffer_paths_1d = _dedupe_sort_paths(buffer_paths_1d)
                    sample_paths_1d = _dedupe_sort_paths(sample_paths_1d)
                    _path_debug("1D-only after _dedupe_sort_paths buffer_paths_1d", buffer_paths_1d)
                    _path_debug("1D-only after _dedupe_sort_paths sample_paths_1d", sample_paths_1d)

                    alignment_res = map_sample_files_to_buffer_files(sample_paths_1d, buffer_paths_1d)
                    run_load_cycle = alignment_res['overlapped'] or alignment_res['not_paired']
                    if alignment_res['overlapped']:
                        overlap_str = '\n'.join([', '.join(p) for p in alignment_res['overlapped']])
                        self._send_message(f"For some sample files more than one buffer files were found:\n{overlap_str[:2000]}\n\nAre you following name conventions?")
                    if alignment_res['not_paired']:
                        not_paired_str = '\n'.join(alignment_res['not_paired'])
                        self._send_message(f"Not for all sample files buffer files were found:\n{not_paired_str}\n\nAre you following name conventions?")
                    if run_load_cycle:
                        self._send_message(f"Make sure that you follow the name convention and that for each sample image there is exactly one buffer image. This error can also disappear by itself for the next iteration")
                        time.sleep(fallback_delay)
                    else:
                        aligned_pairs_clean = _dedupe_sort_pairs(alignment_res['aligned_pairs'])
                        _path_debug("1D-only after aligned_pairs_clean", pairs=aligned_pairs_clean)
                        buffer_paths_1d = [b_p for _, b_p in aligned_pairs_clean]
                        sample_paths_1d = [s_p for s_p, _ in aligned_pairs_clean]
                        _path_debug("1D-only rebuilt buffer_paths_1d", buffer_paths_1d)
                        _path_debug("1D-only rebuilt sample_paths_1d", sample_paths_1d)

                if sample_paths_1d:
                    basename_list = [
                        os.path.splitext(os.path.split(sample_path)[1])[0]
                        for sample_path in sample_paths_1d
                    ]            
            
            profile_paths = []
            profile_pic_paths = []
            diff_plot_paths = []  # from subtract skill (diff_*.png), same order as profile_pic_paths
            if 'subtraction' in steps:
                _path_debug("subtraction alignment input sample_paths_1d", sample_paths_1d)
                _path_debug("subtraction alignment input buffer_paths_1d", buffer_paths_1d)
                # map_sample_files_to_buffer_files() assumes each buffer path appears once.
                # If buffer_paths_1d was rebuilt from aligned pairs, a single buffer can be repeated
                # across multiple sample basenames; clean duplicates before matching.
                buffer_paths_1d = _dedupe_sort_paths(buffer_paths_1d) if buffer_paths_1d else []
                sample_paths_1d = _dedupe_sort_paths(sample_paths_1d) if sample_paths_1d else []
                alignment_res = map_sample_files_to_buffer_files(sample_paths_1d, buffer_paths_1d)
                _path_debug("subtraction alignment_res aligned_pairs", pairs=alignment_res['aligned_pairs'])
                _path_debug("subtraction alignment_res overlapped", pairs=alignment_res['overlapped'])
                aligned_pairs = _dedupe_sort_pairs(alignment_res['aligned_pairs'])
                alignment_check = not (alignment_res['overlapped'] or alignment_res['not_paired'])
                if not alignment_check:
                    overlap_str = '\n'.join([', '.join(p) for p in alignment_res['overlapped']])
                    not_paired_str = '\n'.join(alignment_res['not_paired'])
                    raise RuntimeError(f"Buffer-sample alignment failed!\n\nOverlapped:\n{overlap_str[:2000]}\n\nNot paired:\n{not_paired_str}")

                subtracted_dir = os.path.join(directory, 'subtracted')
                sub_cfg = context.config.get('sub', {}) if context.config else {}
                q_range_abs = sub_cfg.get('q_range_abs')
                q_sub_min = q_range_abs[0] if q_range_abs else None
                q_sub_max = q_range_abs[1] if q_range_abs else None
                for s_p, b_p in aligned_pairs:
                    out_sub = subtract(
                        s_p,
                        b_p,
                        subtracted_dir,
                        q_min=q_sub_min,
                        q_max=q_sub_max,
                        method=sub_cfg.get('method', 'point_match'),
                        sample_form=sub_cfg.get('sample_form', 'Porod-plus-linear'),
                        buffer_form=sub_cfg.get('buffer_form', 'linear'),
                        point_match_factor=float(sub_cfg.get('point_match_factor', 0.995)),
                        use_cache=fast_forward,
                    )
                    profile_paths.append(out_sub['subtracted_1d'])
                    sp = out_sub.get('sub_plot_path')
                    if sp:
                        profile_pic_paths.append(sp)
                    dp = out_sub.get('diff_plot_path')
                    if dp:
                        diff_plot_paths.append(dp)

                context.extend_paths('buffer_1d', buffer_paths_1d)
                context.extend_paths('sample_1d', sample_paths_1d)
            else:
                profile_paths = self._request_file(
                    directory,
                    query='Upload sample data to "subtracted" subdirectory subtracted/*.dat',
                    filepattern='subtracted/*.dat',
                    skip_if_exists=True,
                    except_prev_paths=context['paths', 'profile'],
                )
                if profile_paths:
                    for profile_path in profile_paths:
                        root, filename = os.path.split(profile_path)
                        basename, _ = os.path.splitext(filename)
                        profile_pic_path = os.path.join(root, f'{basename}.png')                
                        q, I, sigma, _ = read_saxs(profile_path)
                        self.viewer.view_curves(q, I, basename,
                                                sigmas=(sigma,),
                                                xlabel='q, (nm-1)', ylabel='I, (a.u.)',
                                                title=f'{basename} SAXS profile',
                                                show_duration=None, save=False,
                                                plotFilePath=profile_pic_path)
                        basename_list.append(basename)
                        profile_pic_paths.append(profile_pic_path)
                # print('DEBUG: profile loading and plotting finished')

            # Keep profile lists sorted alphabetically by basename across the pipeline
            if basename_list and profile_paths and len(profile_paths) == len(basename_list) and len(profile_pic_paths) == len(basename_list):
                # Align diff_plot_paths with basename_list (pad with None if shorter)
                while len(diff_plot_paths) < len(basename_list):
                    diff_plot_paths.append(None)
                if sample_paths_1d is not None and len(sample_paths_1d) == len(basename_list):
                    combined = list(zip(basename_list, profile_paths, profile_pic_paths, sample_paths_1d, diff_plot_paths))
                    combined.sort(key=lambda t: t[0])
                    basename_list, profile_paths, profile_pic_paths, sample_paths_1d, diff_plot_paths = [list(x) for x in zip(*combined)]
                else:
                    combined = list(zip(basename_list, profile_paths, profile_pic_paths, diff_plot_paths))
                    combined.sort(key=lambda t: t[0])
                    basename_list, profile_paths, profile_pic_paths, diff_plot_paths = [list(x) for x in zip(*combined)]

            # simple_analysis for all sample profiles (§10: try-except, report via MESSAGE)
            descriptors_by_basename = {}
            if 'simple_analysis' in steps and basename_list and profile_paths:
                descriptors_dir = os.path.join(directory, 'descriptors')
                for basename, profile_path in zip(basename_list, profile_paths):
                    try:
                        atsas_res_path, gnom_path = self.get_descriptors(
                            context, profile_path, dest_dir=descriptors_dir, fast_forward=fast_forward)
                        descriptors_by_basename[basename] = (atsas_res_path, gnom_path)
                    except Exception as e:
                        self._send_message(f"simple_analysis failed for {basename}: {e}")

            # plots for all sample profiles (§4 step 5, §10: try-except, report via MESSAGE)
            plots_by_basename = {}
            if 'plots' in steps and basename_list and profile_paths:
                plots_dir = os.path.join(directory, 'plots')
                try:
                    guinier_list = []
                    kratky_list = []
                    loglog_list = []
                    for p in profile_paths:
                        out_plot = plot(p, plots_dir, use_cache=fast_forward)
                        guinier_list.append(out_plot.get('guinier_plot_path'))
                        kratky_list.append(out_plot.get('kratky_plot_path'))
                        loglog_list.append(out_plot.get('loglog_plot_path'))
                    for idx, basename in enumerate(basename_list):
                        if idx < len(guinier_list) and idx < len(kratky_list) and idx < len(loglog_list):
                            plots_by_basename[basename] = [
                                guinier_list[idx],
                                kratky_list[idx],
                                loglog_list[idx],
                            ]
                except Exception as e:
                    self._send_message(f"plots failed: {e}")

            # First report pass: all sample profiles via report skills (§4 step 6)
            if basename_list and profile_paths:
                reports_dir = os.path.join(directory, 'reports')
                for basename in basename_list:
                    try:
                        skill.report_individual(
                            directory, basename,
                            output_path=os.path.join(reports_dir, f'{basename}_report.pdf'),
                        )
                    except Exception as e:
                        self._send_message(f"report (individual) failed for {basename}: {e}")
                try:
                    skill.report_summary(
                        directory,
                        output_path=os.path.join(reports_dir, 'summary_report.pdf'),
                    )
                except Exception as e:
                    self._send_message(f"report (summary) failed: {e}")

            profiles_data = []
            for basename, (idx, profile_path), plot_path in zip(
                basename_list, enumerate(profile_paths), profile_pic_paths):
                q, I, _, metadata = read_saxs(profile_path)
                profiles_data.append(
                    {
                        'basename': basename,
                        'path': profile_path,
                        'q': q,
                        'I': I,
                        'metadata': metadata,
                        'plot_path': plot_path,
                    }
                )
            # Profile selection only if at least one step after simple_analysis/plots (§4 step 7, §11)
            steps_after_simple_analysis = {'mixture', 'bodies', 'dammif', 'ai_analysis'}
            request_profile_selection = bool(set(steps) & steps_after_simple_analysis)
            if request_profile_selection:
                profiles_data = sorted(profiles_data, key=lambda p: p.get("basename", ""))
                selected_profiles = self._request_profile_selection(profiles_data)
            else:
                selected_profiles = {}

            # Run mixture, bodies, dammif per selected profile (public skill entry points)
            selected_order = sorted(selected_profiles)
            mixture_results_by_idx = {}
            bodies_dirs_list = []
            dammif_dirs_list = []
            if selected_order:
                if 'mixture' in steps:
                    try:
                        q_range_nm = context.config.get('mixture', {}).get('q_range_nm') if context.config else None
                        q_min_nm = q_range_nm[0] if q_range_nm and len(q_range_nm) >= 2 else None
                        q_max_nm = q_range_nm[1] if q_range_nm and len(q_range_nm) >= 2 else None
                        mixture_root = os.path.join(directory, 'mixture')
                        for i, b in enumerate(selected_order):
                            out_mixture = fit_mixture(
                                selected_profiles[b]['path'],
                                os.path.join(mixture_root, b),
                                config_path=context.config_path,
                                q_min_nm=q_min_nm,
                                q_max_nm=q_max_nm,
                                use_cache=fast_forward,
                            )
                            os_sub = out_mixture.get('output_subdir', '')
                            context.append_path('mixture', os_sub)
                            mixture_results_by_idx[i] = {
                                'output_subdir': os_sub,
                                'comparison_path': out_mixture.get('comparison_path'),
                                'distributions_path': out_mixture.get('distributions_path'),
                                'results_csv_path': out_mixture.get('results_csv_path'),
                            }
                    except Exception as e:
                        self._send_message(f"mixture failed: {e}")
                if 'bodies' in steps:
                    try:
                        bodies_root = os.path.join(directory, 'bodies')
                        bodies_dirs_list = []
                        for b in selected_order:
                            out_bodies = fit_bodies(
                                selected_profiles[b]['path'],
                                os.path.join(bodies_root, b),
                                use_cache=fast_forward,
                            )
                            d = out_bodies.get('output_subdir', '')
                            bodies_dirs_list.append(d)
                            context.append_path('bodies', d)
                    except Exception as e:
                        self._send_message(f"bodies failed: {e}")
                if 'dammif' in steps:
                    try:
                        dammif_root = os.path.join(directory, 'dammif')
                        dammif_dirs_list = []
                        for b in selected_order:
                            gnom_p = descriptors_by_basename.get(b, (None, None))[1]
                            prof_p = selected_profiles[b]['path']
                            out_dammif = fit_dammif(
                                prof_p,
                                os.path.join(dammif_root, b),
                                gnom_path=gnom_p or prof_p,
                                use_cache=fast_forward,
                            )
                            d = out_dammif.get('output_subdir', '')
                            dammif_dirs_list.append(d)
                            context.append_path('dammif', d)
                    except Exception as e:
                        self._send_message(f"dammif failed: {e}")

            for idx, basename in enumerate(selected_order):
                profile = selected_profiles[basename]
                profile_path = profile['path']
                profile_pic_path = profile.get('plot_path')
                atsas_res_path, gnom_path = descriptors_by_basename.get(basename, (None, None))
                if atsas_res_path:
                    context.append_path('atsas_res', atsas_res_path)
                if gnom_path:
                    context.append_path('P(r)', gnom_path)
                # Plot paths: sub (profile_pic_path) + guinier, kratky, loglog from plots-for-all pass (§4 step 8; plots run in first pass)
                plot_paths = ([profile_pic_path] if profile_pic_path else []) + list(plots_by_basename.get(basename, []))
                if plot_paths:
                    context.append_path('plot', plot_paths)
                mixture_result = mixture_results_by_idx.get(idx) or {}
                mixture_dir = mixture_result.get('output_subdir', '')
                bodies_dir = bodies_dirs_list[idx] if idx < len(bodies_dirs_list) else ''
                dammif_dir = dammif_dirs_list[idx] if idx < len(dammif_dirs_list) else ''
                if 'ai_analysis' in steps:
                    assert len(selected_profiles) == 1
                    assert profile_path is not None and gnom_path is not None
                    assert len(plot_paths) > 1
                    try:
                        self.ai_analysis(atsas_res_path, plot_paths,
                            dest_dir=os.path.join(directory, 'ai_analysis'),
                            text_model=model, vision_model=vision_model,
                            fast_forward=fast_forward)
                    except Exception as e:
                        self._send_message(f"ai_analysis failed for {basename}: {e}")
                # self.ai_analysis(atsas_res_path, plot_paths, directory, text_model=model, vision_model=vision_model)

                # Second report pass: full data for this selected profile via report skill (overwrites first-pass PDF)
                try:
                    skill.report_individual(
                        directory, basename,
                        output_path=os.path.join(directory, 'reports', f'{basename}_report.pdf'),
                    )
                except Exception as e:
                    self._send_message(f"report (individual) failed for {basename}: {e}")

            context.extend_paths('profile', profile_paths)

            upload_more = self._request_choice(
                'Upload more data? Type "no" to exit program, type Enter or "yes" to continue',
                options={},
            )
            run_process_cycle = not (upload_more or '').lower().startswith('n')
            iteration_number += 1
        
        return context
    
    def pipeline_batch(
        self, all_from_config=False, config_path: Optional[str] = None, fast_forward=False):
        # TODO currently the pipeline is oriented on proteins. Since the pipeline for other samples is sort of similar, I think, there will be only one pipeline in the end

        model = 'GLM-4.6'
        # model = 'DeepSeek-V3.1'
        vision_model = 'GLM-4.5V'

        context = Context()
        
        if all_from_config:
            assert config_path is not None
            context.set_config(config_path)
            
            steps = context['steps']
            directory = context['directory']
            context.set_directory(directory)

        else:
            # pipeline_choice, steps = get_pipeline_spec_gui()
            # save_latest_steps(pipeline_choice, steps)

            # descr, descr_path = get_pipeline_description(pipeline_choice)
            # print(descr)
            # directory = self.interface.ask_for_file('Write a path to a directory for your data')
            # context.set_directory(directory)

           
            # config_path = glob.glob(os.path.join(directory, 'config.conf'))
            # assert len(config_path) == 1
            # config_path, = config_path
            # context.set_config(config_path)

            raise NotImplementedError

        ai = None
        if 'calibration' in steps:
            assert 'calib_2d' in context.paths
            calib_path = context['paths', 'calib_2d', -1]
            if context['mask_config', 'mode'] in ['from_file', 'combined']:
                mask_path = context['paths', 'calib_mask']
                if mask_path:
                    mask_path, = mask_path
                else:
                    mask_path = None
            res_calib = self.autocalib(
                calib_path, mask_path, context=context, fast_forward=fast_forward)
            ai = res_calib['integrator']
        
        if 'integration' in steps and 'calibration' not in steps:
            ai_subdir = 'integrator_params'
            def exists_condition():
                return all(os.path.exists(os.path.join(directory, ai_subdir, p)) 
                for p in ['ai_params.json', 'detector_params.json', 'mask.npy']) 
            
            assert exists_condition(), 'IntegratorExtended object can not be created - the data does not exist'
            ai = IntegratorExtended.from_disk(os.path.join(directory, ai_subdir))
        
        if 'integration' in steps:
            for p in context['paths', 'buffer_2d']:
                int_p = self.integrate(
                    ai, context, p, metadata={'type': 'buffer'}, 
                    dest_dir=os.path.join(directory, 'averaged'), 
                    fast_forward=fast_forward)
                context.append_path('buffer_1d', int_p)
            
            for p in context['paths', 'sample_2d']:
                int_p = self.integrate(
                    ai, context, p, metadata={'type': 'sample'}, 
                    dest_dir=os.path.join(directory, 'averaged'), 
                    fast_forward=fast_forward)
                context.append_path('sample_1d', int_p)
        
        if 'subtraction' in steps:
            profile_paths = []
            profile_pic_paths = []

            sample_1d_list = _dedupe_sort_paths(context['paths', 'sample_1d'])
            buffer_1d_list = _dedupe_sort_paths(context['paths', 'buffer_1d'])
            alignment_res = map_sample_files_to_buffer_files(sample_1d_list, buffer_1d_list)
            aligned_pairs = _dedupe_sort_pairs(alignment_res['aligned_pairs'])
            alignment_check = not(alignment_res['overlapped'] or alignment_res['not_paired'])
            if not alignment_check:
                overlap_str = '\n'.join([', '.join(p) for p in alignment_res['overlapped']])
                not_paired_str = '\n'.join(alignment_res['not_paired'])
                raise RuntimeError(f"Buffer-sample alignment failed!\n\nOverlapped:\n{overlap_str[:2000]}\n\nNot paired:\n{not_paired_str}")

            for b_path, s_path in aligned_pairs:
                sub_path, sub_pic_path = self.subtract(
                    context, s_path, b_path, 
                    dest_dir=os.path.join(directory, 'subtracted'), fast_forward=fast_forward
                )
                context.append_path('sub', sub_path)
                context.append_path('sub_picture', sub_pic_path)

        if 'sample_analysis' in steps:
            for p in context['paths', 'sub']:
                atsas_res_path, gnom_path = self.get_descriptors(
                    context, p, 
                    dest_dir=os.path.join(directory, 'descriptors'), fast_forward=fast_forward)
                context.append_path('astas_analysis', atsas_res_path)
                context.append_path('p(R)', gnom_path)
        
        if 'plots' in steps:
            for sub_path, sub_pic_path in zip(context['paths', 'sub'], context['paths', 'sub_picture']):
                plot_paths = self.plot(
                    context, sub_path, dest_dir=os.path.join(directory, 'plots'), fast_forward=fast_forward)
                plot_paths = [sub_pic_path, ] + plot_paths
                context.append_path('plot', plot_paths)
        
        if 'bodies' in steps:
            for p in context['paths', 'sub']:
                self.bodies_fit(
                    context, p, os.path.join(directory, 'bodies'), fast_forward=fast_forward
                )
        
        if 'dammif' in steps:
            for sub_path, gnom_path in zip(context['paths', 'sub'], context['paths', 'p(R)']):
                self.dammif_fit(
                    context, sub_path, gnom_path, os.path.join(directory, 'dammif'),
                    fast_forward=fast_forward
                )
        
        # if context['analyze_with_ai']:
        #     self.ai_analysis(atsas_res_path, plot_paths, directory, text_model=model, vision_model=vision_model)
    
    # def pipeline(self):
    #     try:
    #         self.load_config('calib_config.conf')
            
    #         if os.path.exists(CALIBRATED_GEOMETRY_PATH):
    #             if_calibrate = self.interface.ask_question(
    #                 f'Should the detector geometry be calibrated or the parameters from {CALIBRATED_GEOMETRY_PATH} should be used?',
    #                 options={'c': 'calibrate', 'u': 'use existent'}
    #             )
    #         else:
    #             self._send_message(
    #                 'Calibrated geometry file does not exists. You need to calibrate the geometry of the detector first'
    #                 )
    #             if_calibrate = 'c'
                
    #         if if_calibrate == 'c':
    #             self.calibration_block(fast_forward=True)
    #             if_satisfied = self.interface.ask_question(
    #                 'Are calibration results fine or the parameters should be adjusted?',
    #                 options={'f': 'fine', 'a': 'adjust'}, default_op='f')
                
    #             while if_satisfied == 'a':
    #                 self.calibration_block(fast_forward=False)
    #                 if_satisfied = self.interface.ask_question(
    #                     'Are calibration results fine or the parameters should be adjusted?',
    #                     options={'f': 'fine', 'a': 'adjust'}, default_op='f')

    #         if_liquid = self.interface.ask_question(
    #             'Your sample is in the liquid on in the powder form? (l-liquid/p-powder) ',
    #             options={'l': 'liquid', 'p': 'powder'}, default_op='l')
            
    #         dispersity = self.interface.ask_question(
    #                 'Is your sample monodisperse or polydisperse or it is unkown?',
    #                 options={'m': 'monodisperse', 'p': 'polydisperse', 'u': 'unknown'}
    #             )
            
    #         if if_liquid == 'l':
    #             if dispersity == 'm':
    #                 concentration = self.interface.ask_for_parameter(
    #                     'concentration', float, query='Enter the concentration of the substance of interest, mg/ml ',
    #                 )
                    
    #                 if concentration > 5.:
    #                     do_conc_series = self.interface.ask_question(
    #                         'Since your sample is of high substance concentration, it is recommended for you to proceed with concentration series. '
    #                         'Start concentration series? (yes/no, default yes) ',
    #                         default_op='y'
    #                         )
    #                     if do_conc_series.lower().startswith('y'):
    #                         self.concentration_series()
    #             else:
    #                 raise RuntimeError('There is yet no pipeline for samples which are monodisperse')
    #         elif if_liquid == 'p':
    #             raise RuntimeError('There is no pipeline for powder sample analysis yet')
        
    #         self._send_message('The processing of SAXS data is finished. Good luck!')
            
    #     except Exception as e:
    #         logging.exception("An unhandled exception occurred and interrupted the work of the app.")
    #         self._send_message(f"\nAn unexpected error occurred and interrupted the work of the app: {e}. See calibration_app.log for details.")



