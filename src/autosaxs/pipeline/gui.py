import io
import sys
import os
import json
import yaml
import subprocess
import contextlib
import itertools
import tempfile
from pathlib import Path

import customtkinter as ctk
from matplotlib import rcParams
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# from viewer import setPlotDefaults

from ..core.utils import LATEST_STEPS_PATH


def _center_window(win):
    """
    Center a Tk/CTk window on the primary screen.
    """
    win.update_idletasks()
    width = win.winfo_width()
    height = win.winfo_height()
    screen_width = win.winfo_screenwidth()
    screen_height = win.winfo_screenheight()
    x = int((screen_width - width) / 2)
    y = int((screen_height - height) / 2)
    win.geometry(f"+{x}+{y}")


def _load_latest_steps():
    """
    Load previously saved steps configuration if it exists.
    Returns a dict with optional 'steps' and 'pipeline' keys or {}.
    """
    path = Path(LATEST_STEPS_PATH)
    if path.exists():
        with path.open("r") as f:
            data = yaml.safe_load(f) or {}
            # Only keep list entries that look like steps
            steps = data.get("steps")
            if isinstance(steps, (list, tuple)):
                return {
                    "steps": list(steps),
                    "pipeline": data.get("pipeline"),
                }
    return {}


def _run_gui_interactive():
    """
    Run the actual GUI in this process and return (pipeline_choice, steps).
    This function should only be called from a separate helper process or
    for manual testing, not from the main CLI-driven process.
    """
    # Configuration for available pipelines and their default steps
    PIPELINES = {
        "protein_v0": {
            "label": "protein-v0",
            "steps": [
                "calibration",
                "integration",
                # "averaging",
                "subtraction",
                # "scaling",
                "simple_analysis",
                "plots",
                "mixture",
                "polydispfit",
                "bodies",
                "dammif",
                "ai_analysis",
            ],
            "default_steps": [
                "calibration",
                "integration",
                "subtraction",
                "simple_analysis",
                "plots",
                "polydispfit",
                "bodies",
            ],
        },
    }

    latest_cfg = _load_latest_steps()
    has_latest_cfg = bool(latest_cfg.get("steps"))

    selected_pipeline_key = {"value": None}
    selected_steps = []
    use_latest_steps = {"value": False}

    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")

    # Phase 1: choose pipeline
    pipeline_window = ctk.CTk()
    pipeline_window.title("Choose pipeline")

    pipeline_window.grid_columnconfigure(0, weight=1)

    label = ctk.CTkLabel(
        master=pipeline_window,
        text="Choose a pipeline:",
        font=ctk.CTkFont(size=18, weight="bold"),
    )
    label.grid(row=0, column=0, padx=24, pady=(24, 12), sticky="nsew")

    button_frame = ctk.CTkFrame(master=pipeline_window)
    button_frame.grid(row=1, column=0, padx=24, pady=(0, 24), sticky="nsew")
    button_frame.grid_columnconfigure(0, weight=1)

    def make_pipeline_callback(key, use_latest=False):
        def _callback():
            selected_pipeline_key["value"] = key
            use_latest_steps["value"] = use_latest
            pipeline_window.destroy()

        return _callback

    row = 0
    for key, cfg in PIPELINES.items():
        btn = ctk.CTkButton(
            master=button_frame,
            text=cfg["label"],
            command=make_pipeline_callback(key),
            width=240,
            height=40,
            font=ctk.CTkFont(size=16, weight="normal"),
        )
        btn.grid(row=row, column=0, padx=12, pady=8, sticky="ew")
        row += 1

    if has_latest_cfg:
        latest_pipeline_key = latest_cfg.get("pipeline") or next(iter(PIPELINES))
        btn = ctk.CTkButton(
            master=button_frame,
            text="latest configuration",
            command=make_pipeline_callback(latest_pipeline_key, use_latest=True),
            width=240,
            height=40,
            font=ctk.CTkFont(size=16, weight="normal"),
        )
        btn.grid(row=row, column=0, padx=12, pady=8, sticky="ew")
        row += 1

    _center_window(pipeline_window)
    pipeline_window.mainloop()

    pipeline_choice = selected_pipeline_key["value"]
    if pipeline_choice is None:
        # User closed the window without choosing – fall back to defaults
        pipeline_choice = "protein_v0"

    pipeline_cfg = PIPELINES[pipeline_choice]

    # Phase 2: choose steps
    steps_window = ctk.CTk()
    steps_window.title("Choose steps")

    steps_window.grid_columnconfigure(0, weight=1)

    steps_label = ctk.CTkLabel(
        master=steps_window,
        text=f"Choose steps for {pipeline_cfg['label']}:",
        font=ctk.CTkFont(size=18, weight="bold"),
    )
    steps_label.grid(row=0, column=0, padx=24, pady=(24, 12), sticky="nsew")

    steps_frame = ctk.CTkScrollableFrame(
        master=steps_window,
        width=360,
        height=280,
    )
    steps_frame.grid(row=1, column=0, padx=24, pady=(0, 12), sticky="nsew")
    steps_frame.grid_columnconfigure(0, weight=1)

    checkbox_vars = {}
    if use_latest_steps["value"]:
        default_selected_steps = [
            s for s in latest_cfg["steps"]
        ]
    else:
        default_selected_steps = list(pipeline_cfg["default_steps"])

    for i, step in enumerate(pipeline_cfg["steps"]):
        var = ctk.BooleanVar(
            value=step in default_selected_steps,
        )
        cb = ctk.CTkCheckBox(
            master=steps_frame,
            text=step,
            variable=var,
            font=ctk.CTkFont(size=15),
            height=30,
        )
        cb.grid(row=i, column=0, padx=12, pady=6, sticky="w")
        checkbox_vars[step] = var

    def on_confirm():
        nonlocal selected_steps
        selected_steps = [s for s, v in checkbox_vars.items() if v.get()]
        if not selected_steps:
            # If user unchecks everything, keep defaults
            selected_steps = list(pipeline_cfg["default_steps"])
        steps_window.destroy()

    confirm_button = ctk.CTkButton(
        master=steps_window,
        text="OK",
        command=on_confirm,
        width=140,
        height=40,
        font=ctk.CTkFont(size=16, weight="bold"),
    )
    confirm_button.grid(row=2, column=0, padx=24, pady=(12, 24))

    _center_window(steps_window)
    steps_window.mainloop()

    return pipeline_choice, selected_steps


def _run_choose_profiles_gui(profiles):
    """
    Show all provided SAXS profiles and let the user choose which to keep.

    Args:
        profiles (list[dict]): Each dict should contain:
            - basename: str
            - q: array-like
            - I: array-like

    Returns:
        list[str]: selected basenames.
    """
    if not profiles:
        return []
    profiles = sorted(profiles, key=lambda p: p.get("basename", ""))

    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")

    window = ctk.CTk()
    window.title("Choose SAXS profiles")
    window.grid_columnconfigure(0, weight=1)
    window.grid_rowconfigure(1, weight=5, minsize=360)
    window.grid_rowconfigure(2, weight=2, minsize=180)
    window.grid_rowconfigure(3, weight=0)
    window.geometry("1180x820")
    window.minsize(1000, 700)

    title_label = ctk.CTkLabel(
        master=window,
        text="Select SAXS profiles to process",
        font=ctk.CTkFont(size=18, weight="bold"),
    )
    title_label.grid(row=0, column=0, padx=24, pady=(24, 12), sticky="nsew")

    fig = Figure(dpi=100)
    ax = fig.add_subplot(111)
    prop_cycle = rcParams.get("axes.prop_cycle")
    color_cycle = (
        itertools.cycle(prop_cycle.by_key().get("color", []))
        if prop_cycle is not None
        else None
    )

    for profile in profiles:
        color = next(color_cycle) if color_cycle else None
        ax.plot(profile["q"], profile["I"], label=profile["basename"], color=color)

    ax.set_xlabel("q, (nm-1)")
    ax.set_ylabel("I, (a.u.)")
    ax.set_title("SAXS profiles")
    # ax.legend(loc="best")
    fig.tight_layout()

    canvas = FigureCanvasTkAgg(fig, master=window)
    canvas.draw()
    canvas_widget = canvas.get_tk_widget()
    canvas_widget.grid(row=1, column=0, padx=24, pady=(0, 12), sticky="nsew")

    checkbox_frame = ctk.CTkScrollableFrame(master=window, width=480, height=220)
    checkbox_frame.grid(row=2, column=0, padx=24, pady=(0, 12), sticky="ew")
    for col in range(4):
        checkbox_frame.grid_columnconfigure(col, weight=1)

    checkbox_vars = {}
    for i, profile in enumerate(profiles):
        var = ctk.BooleanVar(value=False)
        cb = ctk.CTkCheckBox(
            master=checkbox_frame,
            text=profile["basename"],
            variable=var,
            font=ctk.CTkFont(size=15),
            height=28,
        )
        cb.grid(row=i // 4, column=i % 4, padx=12, pady=4, sticky="w")
        checkbox_vars[profile["basename"]] = (var, profile)

    selected = []

    def on_confirm():
        nonlocal selected
        selected = [
            name
            for name, (var, _profile) in checkbox_vars.items()
            if var.get()
        ]
        window.destroy()

    def on_check_all():
        for var, _profile in checkbox_vars.values():
            var.set(True)

    def on_uncheck_all():
        for var, _profile in checkbox_vars.values():
            var.set(False)

    button_frame = ctk.CTkFrame(master=window)
    button_frame.grid(row=3, column=0, padx=24, pady=(12, 24), sticky="ew")
    button_frame.grid_columnconfigure(0, weight=1)
    button_frame.grid_columnconfigure(1, weight=1)
    button_frame.grid_columnconfigure(2, weight=1)

    check_all_button = ctk.CTkButton(
        master=button_frame,
        text="Check all",
        command=on_check_all,
        width=140,
        height=40,
        font=ctk.CTkFont(size=16, weight="bold"),
    )
    check_all_button.grid(row=0, column=0, padx=8, pady=0)

    uncheck_all_button = ctk.CTkButton(
        master=button_frame,
        text="Uncheck all",
        command=on_uncheck_all,
        width=140,
        height=40,
        font=ctk.CTkFont(size=16, weight="bold"),
    )
    uncheck_all_button.grid(row=0, column=1, padx=8, pady=0)

    confirm_button = ctk.CTkButton(
        master=button_frame,
        text="Confirm",
        command=on_confirm,
        width=140,
        height=40,
        font=ctk.CTkFont(size=16, weight="bold"),
    )
    confirm_button.grid(row=0, column=2, padx=8, pady=0)

    window.protocol("WM_DELETE_WINDOW", on_confirm)
    _center_window(window)
    window.mainloop()

    return selected


def choose_profiles(profiles):
    """
    Public API to choose profiles without letting the GUI pollute CLI stdio.

    Spawns a short-lived helper process (this same file) that runs the actual
    CustomTkinter GUI and returns the selected profiles. Only structured JSON
    is exchanged with the helper process; any stray GUI stdout/stderr stays
    confined to the helper.
    """
    if not profiles:
        return {}
    profiles = sorted(profiles, key=lambda p: p.get("basename", ""))

    helper_path = os.path.abspath(__file__)
    tmp_path = None
    try:
        serializable_profiles = [
            {
                "basename": p["basename"],
                "q": list(p["q"]),
                "I": list(p["I"]),
            }
            for p in profiles
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
            json.dump(serializable_profiles, tmp)
            tmp_path = tmp.name

        proc = subprocess.run(
            [sys.executable, helper_path, "--choose-profiles", tmp_path],
            check=True,
            text=True,
            capture_output=True,
        )
        data = json.loads(proc.stdout.strip() or "{}")
        selected_basenames = data.get("selected_basenames", [])
    finally:
        if tmp_path:
            with contextlib.suppress(FileNotFoundError):
                os.remove(tmp_path)

    return {
        p["basename"]: p
        for p in profiles
        if p["basename"] in selected_basenames
    }


def get_pipeline_spec_gui():
    """
    Public API used by the rest of the application.

    It launches a short-lived helper Python process that runs the actual
    CustomTkinter GUI. Any GUI/Tcl noise (including click_animation errors)
    stays confined to that helper process and does not touch the main CLI.
    """
    helper_path = os.path.abspath(__file__)
    proc = subprocess.run(
        [sys.executable, helper_path, "--run-gui"],
        check=True,
        text=True,
        capture_output=True,
    )
    # We only expose structured JSON; ignore any stray stderr from the GUI.
    data = json.loads(proc.stdout.strip())
    return data["pipeline_choice"], data["steps"]


if __name__ == "__main__":
    # Helper modes used by get_pipeline_spec_gui / choose_profiles
    if len(sys.argv) > 1 and sys.argv[1] == "--run-gui":
        choice, steps = _run_gui_interactive()
        print(json.dumps({"pipeline_choice": choice, "steps": steps}))
    elif len(sys.argv) > 2 and sys.argv[1] == "--choose-profiles":
        profiles_path = Path(sys.argv[2])
        with profiles_path.open("r") as f:
            profiles = json.load(f)
        profiles = sorted(profiles, key=lambda p: p.get("basename", ""))
        selected = _run_choose_profiles_gui(profiles)
        print(json.dumps({"selected_basenames": selected}))
    else:
        # Manual testing: run the pipeline GUI directly.
        choice, steps = _run_gui_interactive()
        print(f"Pipeline: {choice}")
        print(f"Steps: {steps}")


