#!/usr/bin/env python3
"""
SAXS 2D-to-1D integration script. Accepts integrator directory and image path (*.tif);
output directory is always the directory containing the image.
Invokes Controller.integrate from the autosaxs package.
"""

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(
        description="Run SAXS 2D-to-1D integration. Output directory is derived from the image path."
    )
    parser.add_argument(
        "integrator_path",
        type=str,
        help="Path to the directory where the integrator is stored (calibration results).",
    )
    parser.add_argument(
        "image_path",
        type=str,
        help="Path to the 2D SAXS image to integrate (e.g. *.tif).",
    )
    args = parser.parse_args()

    integrator_path = os.path.abspath(args.integrator_path)
    image_path = os.path.abspath(args.image_path)

    if not os.path.isdir(integrator_path):
        print(f"Error: Integrator path is not a directory: {integrator_path}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(image_path):
        print(f"Error: Image file not found: {image_path}", file=sys.stderr)
        sys.exit(1)

    dest_dir = os.path.dirname(image_path)
    if not dest_dir:
        dest_dir = os.getcwd()

    # Use non-interactive backend for headless execution
    import matplotlib
    matplotlib.use("Agg")

    from autosaxs.context import Context
    from autosaxs.event_bus import EventBus, EventType
    from autosaxs.processor import IntegratorExtended
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

    try:
        integrator = IntegratorExtended.from_disk(integrator_path)
    except Exception as e:
        print(f"Error: Failed to load integrator from {integrator_path}: {e}", file=sys.stderr)
        sys.exit(1)

    context = Context()
    context.set_directory(dest_dir)

    try:
        int_path = controller.integrate(
            integrator,
            context,
            image_path,
            dest_dir=dest_dir,
            metadata={"type": "integrate"},
            fast_forward=False,
        )
    except Exception as e:
        print(f"Error: Integration failed: {e}", file=sys.stderr)
        sys.exit(1)

    if not int_path or not os.path.isfile(int_path):
        print("Integration produced no output.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
