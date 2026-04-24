#!/usr/bin/env python3
"""
Debug script: run simple_analysis (get_descriptors) on all subtracted curves
from debug/unstable_rg_debug. Writes descriptors and GNOM outputs into
debug/unstable_rg_debug/descriptors/.
"""
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR / "repos") not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR / "repos"))

# Non-interactive backend for headless run
import matplotlib
matplotlib.use("Agg")

from autosaxs.core.context import Context
from autosaxs.core.event_bus import EventBus, EventType
from autosaxs.pipeline.saxs_controller import Controller
from autosaxs.core.utils import _parse_descriptors_from_results
from autosaxs.core import viewer


DEBUG_DIR = SCRIPT_DIR / "debug" / "unstable_rg_debug"


def find_subtracted_curves(base_dir: Path):
    """Return list of .dat paths: subtracted/*.dat then *.dat in base_dir."""
    subtracted = base_dir / "subtracted"
    if subtracted.is_dir():
        paths = sorted(subtracted.glob("*.dat"))
        if paths:
            return [str(p) for p in paths]
    paths = sorted(base_dir.glob("*.dat"))
    return [str(p) for p in paths]


def main():
    if not DEBUG_DIR.is_dir():
        print(f"Error: directory not found: {DEBUG_DIR}", file=sys.stderr)
        sys.exit(1)

    curves = find_subtracted_curves(DEBUG_DIR)
    if len(curves) == 0:
        print(f"Error: no .dat curves found under {DEBUG_DIR} or {DEBUG_DIR / 'subtracted'}", file=sys.stderr)
        sys.exit(1)

    descriptors_dir = DEBUG_DIR / "descriptors"
    descriptors_dir.mkdir(parents=True, exist_ok=True)

    event_bus = EventBus()

    def on_message(data):
        text = (data or {}).get("text", "")
        if text:
            print(text, flush=True)

    event_bus.subscribe(EventType.MESSAGE, on_message)
    controller = Controller(event_bus, viewer.PLTViewer())

    print(f"Running simple_analysis on {len(curves)} curve(s) from {DEBUG_DIR}")
    print(f"Output: {descriptors_dir}\n")

    context = Context()
    context.set_directory(str(DEBUG_DIR))

    for curve_path in curves:
        name = os.path.basename(curve_path)
        try:
            res_path, gnom_path = controller.get_descriptors(
                context,
                curve_path,
                dest_dir=str(descriptors_dir),
                fast_forward=False,
            )
            print(f"  OK: {name} -> {res_path}")
            if res_path and os.path.isfile(res_path):
                desc = _parse_descriptors_from_results(res_path)
                if desc:
                    for k, v in desc.items():
                        print(f"      {k}: {v}")
        except Exception as e:
            print(f"  FAIL: {name} -> {e}", file=sys.stderr)

    print("\nDone.")


if __name__ == "__main__":
    main()
