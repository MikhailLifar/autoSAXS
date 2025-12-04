import io
import sys
import os
import json
import subprocess
import contextlib

import customtkinter as ctk


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
                "bodies",
                "dammif",
                # "ai_analysis",
            ],
            "default_steps": [
                "calibration",
                "integration",
                "subtraction",
                "simple_analysis",
                "plots",
                "bodies",
            ],
        },
    }

    selected_pipeline_key = {"value": None}
    selected_steps = []

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

    def make_pipeline_callback(key):
        def _callback():
            selected_pipeline_key["value"] = key
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
    for i, step in enumerate(pipeline_cfg["steps"]):
        var = ctk.BooleanVar(
            value=step in pipeline_cfg["default_steps"],
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
    # Helper mode used by get_pipeline_spec_gui
    if len(sys.argv) > 1 and sys.argv[1] == "--run-gui":
        choice, steps = _run_gui_interactive()
        print(json.dumps({"pipeline_choice": choice, "steps": steps}))
    else:
        # Manual testing: run the GUI directly.
        choice, steps = _run_gui_interactive()
        print(f"Pipeline: {choice}")
        print(f"Steps: {steps}")


