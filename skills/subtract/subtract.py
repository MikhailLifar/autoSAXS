#!/usr/bin/env python3
"""
SAXS buffer subtraction script. Accepts sample 1D path, buffer 1D path, and config file;
working directory is always derived from the sample path.
Invokes Controller.subtract from the autosaxs package.
"""

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(
        description="Run SAXS buffer subtraction. Working directory is derived from the sample path."
    )
    parser.add_argument(
        "sample_path",
        type=str,
        help="Path to the sample 1D SAXS curve (e.g. *.dat).",
    )
    parser.add_argument(
        "buffer_path",
        type=str,
        help="Path to the buffer 1D SAXS curve (e.g. *.dat).",
    )
    parser.add_argument(
        "config_path",
        type=str,
        help="Path to the YAML config file (e.g. *.conf).",
    )
    args = parser.parse_args()

    sample_path = os.path.abspath(args.sample_path)
    buffer_path = os.path.abspath(args.buffer_path)
    config_path = os.path.abspath(args.config_path)

    if not os.path.isfile(sample_path):
        print(f"Error: Sample file not found: {sample_path}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(buffer_path):
        print(f"Error: Buffer file not found: {buffer_path}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(config_path):
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    directory = os.path.dirname(sample_path)
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

    try:
        sub_path, sub_plot_path = controller.subtract(
            context,
            sample_path,
            buffer_path,
            dest_dir=directory,
            fast_forward=False,
        )
    except KeyError as e:
        print(f"Error: Config is missing required key (e.g. sub.q_range_abs): {e}", file=sys.stderr)
        sys.exit(1)

    if not sub_path or not os.path.isfile(sub_path):
        print("Subtraction produced no output.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
