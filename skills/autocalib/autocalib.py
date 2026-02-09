#!/usr/bin/env python3
"""
SAXS autocalibration script. Accepts calibrant image, config file, and optional mask;
working directory is always derived from the calibrant image path.
Invokes Controller.autocalib from the autosaxs package.
"""

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(
        description="Run SAXS autocalibration. Working directory is derived from the calibrant image path."
    )
    parser.add_argument(
        "calibrant_path",
        type=str,
        help="Path to the calibrant TIFF image (e.g. *.tif).",
    )
    parser.add_argument(
        "config_path",
        type=str,
        help="Path to the YAML config file (e.g. *.conf).",
    )
    parser.add_argument(
        "--mask-path",
        type=str,
        default="",
        metavar="PATH",
        help="Path to the mask file (e.g. mask*). Optional depending on config mask_config.mode.",
    )
    args = parser.parse_args()

    calibrant_path = os.path.abspath(args.calibrant_path)
    config_path = os.path.abspath(args.config_path)
    mask_path = args.mask_path.strip() if args.mask_path else ""
    if mask_path:
        mask_path = os.path.abspath(mask_path)
    else:
        mask_path = ""

    if not os.path.isfile(calibrant_path):
        print(f"Error: Calibrant image not found: {calibrant_path}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(config_path):
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)
    if mask_path and not os.path.isfile(mask_path):
        print(f"Error: Mask file not found: {mask_path}", file=sys.stderr)
        sys.exit(1)

    directory = os.path.dirname(calibrant_path)
    if not directory:
        directory = os.getcwd()

    # Use non-interactive backend for headless execution
    import matplotlib
    matplotlib.use("Agg")

    from autosaxs.context import Context
    from autosaxs.event_bus import EventBus, EventType
    from autosaxs.saxs_controller import Controller
    from autosaxs.viewer import PLTViewer

    event_bus = EventBus()

    # Print messages that Controller would publish (no GUI subscriber)
    def on_message(data):
        text = (data or {}).get("text", "")
        if text:
            print(text, flush=True)

    event_bus.subscribe(EventType.MESSAGE, on_message)

    viewer = PLTViewer()
    controller = Controller(event_bus, viewer)

    context = Context()
    context.set_directory(directory)
    context.set_config(config_path)

    result = controller.autocalib(
        calibrant_path,
        mask_path,
        context,
        fast_forward=False,
    )

    if result.get("integrator") is None and result.get("refined") is None:
        print("Calibration produced no result (empty calibrant path or failure).", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
