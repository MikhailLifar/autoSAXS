# GUI implementation of the pipeline EventBus contract.
# Subscribes to request events; uses short-lived CustomTkinter dialogs.
# Pipeline/step and profile selection as dialogs (spec §3).
# Pipeline/step and profile selection reuse gui internals (LATEST_STEPS_PATH from utils).

import glob
import fnmatch
import os

import customtkinter as ctk

from ..core.event_bus import EventBus, EventType
from .cli_interface import PipelineInterrupt
from .gui import _run_gui_interactive, _run_choose_profiles_gui


def _center_window(win):
    win.update_idletasks()
    w = win.winfo_width()
    h = win.winfo_height()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    win.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")


def connect(bus: EventBus) -> None:
    """Subscribe to request events and respond with short-lived dialogs."""

    def on_directory_requested(data):
        query = (data or {}).get("query", "Choose directory for your data")
        root = ctk.CTk()
        root.withdraw()
        try:
            from tkinter import filedialog
            path = filedialog.askdirectory(title=query)
            root.destroy()
            if path:
                bus.publish(EventType.DIRECTORY_SPECIFIED, {"path": path})
            else:
                bus.publish(EventType.PROGRAM_INTERRUPTED, {"reason": "directory selection canceled"})
        except Exception as e:
            root.destroy()
            bus.publish(EventType.PROGRAM_INTERRUPTED, {"reason": str(e)})

    def on_file_requested(data):
        if not data:
            return
        directory = data.get("directory", "")
        query = data.get("query", "Select file(s)")
        filepattern = data.get("filepattern", "*")
        obligatory = data.get("obligatory", False)
        skip_if_exists = data.get("skip_if_exists", True)
        except_prev_paths = data.get("except_prev_paths", False)
        allow_same_time = data.get("allow_same_time", (1, float("inf")))

        def _pattern_matches(paths, pattern, directory):
            if not directory or not pattern:
                return paths
            norm = os.path.normpath(directory)
            matched = []
            for p in paths:
                rel = os.path.relpath(p, norm)
                if rel.startswith(".."):
                    continue
                name = os.path.basename(p)
                if fnmatch.fnmatch(name, os.path.basename(pattern)) or fnmatch.fnmatch(rel, pattern):
                    matched.append(p)
            return matched

        if skip_if_exists and directory and os.path.isdir(directory):
            pattern_files = glob.glob(os.path.join(directory, filepattern))
            if isinstance(except_prev_paths, (list, tuple)):
                prev = set(except_prev_paths)
                pattern_files = [p for p in pattern_files if p not in prev]
            min_a, max_a = allow_same_time
            if min_a <= len(pattern_files) <= max_a:
                bus.publish(EventType.FILE_UPLOADED, {"paths": pattern_files})
                return

        root = ctk.CTk()
        root.withdraw()
        try:
            from tkinter import filedialog
            paths = filedialog.askopenfilenames(title=query, initialdir=directory or None)
            root.destroy()
            paths = list(paths) if paths else []
            if paths:
                bus.publish(EventType.FILE_UPLOADED, {"paths": paths})
            elif obligatory:
                bus.publish(EventType.PROGRAM_INTERRUPTED, {"reason": "file upload canceled"})
                bus.publish(EventType.FILE_UPLOAD_CANCELED, {"reason": "obligatory file missing"})
            else:
                bus.publish(EventType.FILE_UPLOADED, {"paths": []})
        except Exception as e:
            root.destroy()
            bus.publish(EventType.FILE_UPLOAD_CANCELED, {"reason": str(e)})
            if obligatory:
                bus.publish(EventType.PROGRAM_INTERRUPTED, {"reason": str(e)})

    def on_choice_requested(data):
        if data is None:
            return
        query = data.get("query", "")
        options = data.get("options") or {}
        default_op = data.get("default_op", "no default")
        result = {"value": None}

        root = ctk.CTk()
        root.title("Choice")
        root.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(master=root, text=query, wraplength=400).grid(row=0, column=0, padx=24, pady=(24, 12), sticky="ew")
        frame = ctk.CTkFrame(master=root)
        frame.grid(row=1, column=0, padx=24, pady=12, sticky="ew")
        frame.grid_columnconfigure(0, weight=1)

        def make_cb(key):
            def _():
                result["value"] = key
                root.quit()
                root.destroy()
            return _

        row = 0
        for key, label in options.items():
            btn = ctk.CTkButton(
                master=frame,
                text=f"{key}: {label}" if key != label else label,
                command=make_cb(key),
                width=240,
            )
            btn.grid(row=row, column=0, padx=8, pady=4, sticky="ew")
            row += 1
        if default_op != "no default" and default_op in options:
            def default_cb():
                result["value"] = default_op
                root.quit()
                root.destroy()
            ctk.CTkButton(master=frame, text=f"Default: {default_op}", command=default_cb, width=240).grid(
                row=row, column=0, padx=8, pady=4, sticky="ew"
            )

        def on_cancel():
            result["value"] = None
            root.quit()
            root.destroy()

        ctk.CTkButton(master=root, text="Cancel", command=on_cancel, width=120).grid(row=2, column=0, padx=24, pady=(0, 24))
        _center_window(root)
        root.mainloop()

        if result["value"] is not None:
            bus.publish(EventType.OPTION_CHOSEN, {"choice": result["value"]})
        else:
            bus.publish(EventType.OPTION_CHOICE_CANCELED, {})
            bus.publish(EventType.PROGRAM_INTERRUPTED, {"reason": "choice canceled"})

    def on_message(data):
        text = (data or {}).get("text", "")
        if not text:
            return
        root = ctk.CTk()
        root.withdraw()
        from tkinter import messagebox
        messagebox.showinfo("Message", text)
        root.destroy()

    def on_pipeline_steps_requested(_data):
        try:
            pipeline_choice, steps = _run_gui_interactive()
            bus.publish(
                EventType.PIPELINE_STEPS_SPECIFIED,
                {"pipeline_choice": pipeline_choice, "steps": steps},
            )
        except Exception as e:
            bus.publish(EventType.PROGRAM_INTERRUPTED, {"reason": str(e)})

    def on_profile_selection_requested(data):
        profiles_data = (data or {}).get("profiles_data") or []
        profiles_data = sorted(profiles_data, key=lambda p: p.get("basename", ""))
        if not profiles_data:
            bus.publish(EventType.PROFILE_SELECTION_SPECIFIED, {"selected_profiles": {}})
            return
        try:
            selected_basenames = _run_choose_profiles_gui(profiles_data)
            selected_profiles = {
                p["basename"]: p
                for p in profiles_data
                if p["basename"] in selected_basenames
            }
            bus.publish(EventType.PROFILE_SELECTION_SPECIFIED, {"selected_profiles": selected_profiles})
        except Exception as e:
            bus.publish(EventType.PROFILE_SELECTION_SPECIFIED, {"selected_profiles": {}})

    bus.subscribe(EventType.DIRECTORY_REQUESTED, on_directory_requested)
    bus.subscribe(EventType.FILE_REQUESTED, on_file_requested)
    bus.subscribe(EventType.CHOICE_REQUESTED, on_choice_requested)
    bus.subscribe(EventType.MESSAGE, on_message)
    bus.subscribe(EventType.PIPELINE_STEPS_REQUESTED, on_pipeline_steps_requested)
    bus.subscribe(EventType.PROFILE_SELECTION_REQUESTED, on_profile_selection_requested)
