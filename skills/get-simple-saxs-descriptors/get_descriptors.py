#!/usr/bin/env python3
"""
SAXS descriptor extraction script. Accepts path to 1D SAXS data (*.dat);
output directory is always the directory containing the input file.
Invokes Controller.get_descriptors from the autosaxs package.
"""

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(
        description="Run SAXS descriptor extraction. Output directory is derived from the input .dat path."
    )
    parser.add_argument(
        "path_to_analysis",
        type=str,
        help="Path to the 1D SAXS data file to analyze (e.g. *.dat).",
    )
    args = parser.parse_args()

    path_to_analysis = os.path.abspath(args.path_to_analysis)

    if not os.path.isfile(path_to_analysis):
        print(f"Error: Data file not found: {path_to_analysis}", file=sys.stderr)
        sys.exit(1)

    dest_dir = os.path.dirname(path_to_analysis)
    if not dest_dir:
        dest_dir = os.getcwd()

    # Use non-interactive backend for headless execution
    import matplotlib
    matplotlib.use("Agg")

    from autosaxs.context import Context
    from autosaxs.event_bus import EventBus, EventType
    from autosaxs.saxs_controller import Controller
    from autosaxs.viewer import PLTViewer

    event_bus = EventBus()

    def on_message(data):
        text = (data or {}).get("text", "")
        if text:
            print(text, flush=True)

    event_bus.subscribe(EventType.MESSAGE, on_message)

    viewer = PLTViewer()
    controller = Controller(event_bus, viewer)

    context = Context()
    context.set_directory(dest_dir)

    try:
        results_file, gnom_file = controller.get_descriptors(
            context,
            to_analyze_path=path_to_analysis,
            dest_dir=dest_dir,
            fast_forward=False,
        )
    except Exception as e:
        print(f"Error: Descriptor extraction failed: {e}", file=sys.stderr)
        sys.exit(1)

    if not results_file or not os.path.isfile(results_file):
        print("Descriptor extraction produced no output.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
