#!/usr/bin/env python3
"""
SAXS buffer subtraction script. Accepts sample 1D path, buffer 1D path, and config file;
working directory is always derived from the sample path.
Invokes Controller.subtract from the autosaxs package.

CLI flags override the corresponding keys under ``sub`` in the YAML config when provided.
"""

import argparse
import copy
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
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Directory for subtracted .dat and plots (default: <sample_dir>/subtracted).",
    )
    parser.add_argument(
        "--method",
        type=str,
        default=None,
        choices=("point_match", "match_tail"),
        help="Scaling method (overrides config sub.method). Default: point_match.",
    )
    parser.add_argument(
        "--sample-form",
        type=str,
        default=None,
        help="Sample fit form for point_match: linear, Porod, Porod-plus-linear (overrides config).",
    )
    parser.add_argument(
        "--buffer-form",
        type=str,
        default=None,
        help="Buffer fit form for point_match: linear, Porod, Porod-plus-linear (overrides config).",
    )
    parser.add_argument(
        "--q-min",
        type=float,
        default=None,
        help="Lower q bound for fit/scaling window (requires --q-max; overrides sub.q_range_abs).",
    )
    parser.add_argument(
        "--q-max",
        type=float,
        default=None,
        help="Upper q bound; point_match uses this as the match point (overrides sub.q_range_abs).",
    )
    parser.add_argument(
        "--point-match-factor",
        type=float,
        default=None,
        help="For point_match: scale * I_buffer_fit = factor * I_sample_fit at q_max (default 0.995).",
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

    if (args.q_min is None) ^ (args.q_max is None):
        print("Error: Provide both --q-min and --q-max, or neither.", file=sys.stderr)
        sys.exit(1)

    out_dir = args.output_dir
    if out_dir is None:
        out_dir = os.path.join(directory, "subtracted")
    out_dir = os.path.abspath(out_dir)

    # Use non-interactive backend for headless execution
    import matplotlib
    matplotlib.use("Agg")

    from autosaxs.core.context import Context
    from autosaxs.core.event_bus import EventBus, EventType
    from autosaxs.pipeline.saxs_controller import Controller
    from autosaxs.core.viewer import PLTViewer

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

    cfg = copy.deepcopy(context.config) if context.config else {}
    sub = dict(cfg.get("sub") or {})
    if args.method is not None:
        sub["method"] = args.method
    if args.sample_form is not None:
        sub["sample_form"] = args.sample_form
    if args.buffer_form is not None:
        sub["buffer_form"] = args.buffer_form
    if args.q_min is not None and args.q_max is not None:
        sub["q_range_abs"] = [args.q_min, args.q_max]
    if args.point_match_factor is not None:
        sub["point_match_factor"] = args.point_match_factor
    cfg["sub"] = sub
    context.config = cfg

    try:
        sub_path, sub_plot_path = controller.subtract(
            context,
            sample_path,
            buffer_path,
            dest_dir=out_dir,
            fast_forward=False,
        )
    except (KeyError, ValueError) as e:
        print(f"Error: Invalid config or parameters for subtraction: {e}", file=sys.stderr)
        sys.exit(1)

    if not sub_path or not os.path.isfile(sub_path):
        print("Subtraction produced no output.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
