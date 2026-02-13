# CLI implementation of the pipeline EventBus contract.
# Subscribes to DIRECTORY_REQUESTED, FILE_REQUESTED, CHOICE_REQUESTED, MESSAGE,
# PIPELINE_STEPS_REQUESTED, PROFILE_SELECTION_REQUESTED; publishes the corresponding
# response events. Pipeline/step and profile selection in this module (spec §3).
# Also defines PipelineInterrupt and CLIInterface for standalone use (e.g. integrate.py).

import copy
import glob
import os
import select
import sys
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any, Optional

import numpy as np
import yaml

from .event_bus import EventBus, EventType
from .utils import LATEST_STEPS_PATH


class PipelineInterrupt(Exception):
    """Exception raised when user wants to force early program termination."""
    pass


class CLIInterface:
    """CLI I/O: stdin/print and file monitoring. Used by connect(bus) and by integrate.py."""

    @staticmethod
    def ask_for_parameter(param_name, type_caster, query=None, default='no default'):
        if query is None:
            query = f'Set {param_name}'

        query_text = query
        if default != 'no default':
            query_text += f" (default: {default})"
        query_text += ": "

        user_input = 'starting value'
        value = 'no value'

        while user_input and value == 'no value':
            try:
                user_input = input(query_text)
                if not user_input and default != 'no default':
                    value = default
                elif not user_input:
                    continue
                else:
                    value = type_caster(user_input)
                print(f"-> {param_name} set to: {value}")
            except (ValueError, TypeError):
                error_msg = f"Invalid value for '{param_name}'. Expected type {type_caster.__name__}."
                print(error_msg)

        if value != 'no value':
            return value
        else:
            raise PipelineInterrupt(f"User terminated parameter input for '{param_name}'")

    @staticmethod
    def ask_for_multiple(parameters, types=None, group_name=None, defaults=None):
        if defaults is None:
            defaults = dict()

        if group_name is not None:
            CLIInterface.send_message(f'-- Setting {group_name} parameters --')
        else:
            CLIInterface.send_message(f'-- Setting parameters --')

        if defaults is not None and all(p in defaults for p in parameters):
            CLIInterface.send_message('Default parameters available:\n' + '\n'.join(f'{p}: {defaults[p]}' for p in parameters) + '\n')
            accept_defaults = CLIInterface.ask_question(
                f'Do you accept the default parameters? (yes/no, default is yes) ')
            if accept_defaults.lower().startswith('y') or not accept_defaults:
                CLIInterface.send_message('Accepted default parameters')
                return {p: copy.deepcopy(defaults[p]) for p in parameters}

        assert types is not None
        new_parameters = dict()
        for p, t in zip(parameters, types):
            v = CLIInterface.ask_for_parameter(p, t, default=defaults.get(p, 'no default'))
            new_parameters[p] = v

        return new_parameters

    @staticmethod
    def ask_for_file(query=None, obligatory=True):
        assert query is not None
        query += ': '
        filepath = input(query)
        run_condition = not os.path.exists(filepath) and (obligatory or filepath)
        while run_condition:
            print(f'{filepath} does not exist')
            filepath = input(query)
            run_condition = not os.path.exists(filepath) and (obligatory or filepath)
            if not filepath and obligatory:
                break

        if filepath:
            return filepath
        elif obligatory:
            raise PipelineInterrupt("User terminated file selection")
        return ''

    @staticmethod
    def wait_for_file(
        directory, query=None, obligatory=False,
        wait=2.0, wait_to_end_loading=2.0,
        filepattern='*', skip_if_exists=True, except_prev_paths=False,
        allow_same_time=(1, float('inf'))):
        assert query is not None

        def _get_all_files():
            return set(glob.glob(os.path.join(directory, '**/*'), recursive=True))

        def _get_pattern_files():
            return set(glob.glob(os.path.join(directory, filepattern), recursive=True))

        min_allowed, max_allowed = allow_same_time

        if skip_if_exists:
            pattern_files = _get_pattern_files()
            assert except_prev_paths is not True
            if isinstance(except_prev_paths, Iterable):
                pattern_files = pattern_files - set(except_prev_paths)
            pattern_files = list(pattern_files)
            if min_allowed <= len(pattern_files) <= max_allowed:
                CLIInterface.send_message(f"Skipped file uploading for pattern '{filepattern}', since found existing files")
                return pattern_files

        old_files = _get_all_files()

        query += ': '
        print(query, flush=True)

        # Shared state between threads
        result = {'new_files': None, 'user_interrupted': False}
        stop_event = threading.Event()

        def monitor_files():
            nonlocal old_files
            while not stop_event.is_set():
                current_files = _get_all_files()
                added_files = current_files - old_files
                if added_files:
                    pattern_files = [p for p in _get_pattern_files() if p in added_files]
                    if min_allowed <= len(pattern_files) <= max_allowed:
                        time.sleep(wait_to_end_loading * (1.0 + np.log(len(pattern_files))))
                        result['new_files'] = pattern_files
                        stop_event.set()
                        pattern_files_str = '\n  '.join(pattern_files)
                        CLIInterface.send_message(f"Uploaded files:\n{pattern_files_str}")
                        return
                    elif len(pattern_files) == 0:
                        CLIInterface.send_message(f"No files match the required pattern '{filepattern}'. Please upload a correct file.")
                    else:
                        CLIInterface.send_message(f"The number of uploaded files matching '{filepattern}' should be >= {min_allowed} and <= {max_allowed}. Please make a correct upload.")
                old_files = current_files
                time.sleep(wait)

        def wait_for_input():
            timeout = wait
            while not stop_event.is_set():
                try:
                    rlist, _, _ = select.select([sys.stdin], [], [], timeout)
                except (OSError, ValueError):
                    return
                if stop_event.is_set():
                    return
                if not rlist:
                    continue
                line = sys.stdin.readline()
                if stop_event.is_set():
                    return
                CLIInterface.send_message('User interrupted file selection')
                result['user_interrupted'] = True
                stop_event.set()
                return

        file_thread = threading.Thread(target=monitor_files, daemon=True)
        input_thread = threading.Thread(target=wait_for_input, daemon=True)
        file_thread.start()
        input_thread.start()
        stop_event.wait()
        time.sleep(0.1)

        if result['user_interrupted']:
            new_files = []
        elif result['new_files'] is not None:
            new_files = result['new_files']
        else:
            raise RuntimeError("Unexpected error occurred")

        if obligatory and not new_files:
            raise PipelineInterrupt('The user interrupted the pipeline execution')

        if except_prev_paths is True:
            return new_files
        elif except_prev_paths is False:
            return list(_get_pattern_files())
        elif isinstance(except_prev_paths, Iterable):
            return list(_get_pattern_files() - set(except_prev_paths))

    @staticmethod
    def ask_question(query, options: Optional[dict] = None, default_op='no default'):
        if options is None:
            options = dict()
        query += ' '
        if options:
            query += '(' + '/'.join(f'{op_short}: {op}' for op_short, op in options.items()) \
                + f', default: {default_op}' * (default_op != 'no default') + ') '
        answ = input(query)
        if not answ and default_op != 'no default':
            CLIInterface.send_message(f'Set to default: {default_op}')
            answ = default_op
        if options:
            while answ not in options and answ != default_op:
                if answ or default_op == 'no default':
                    CLIInterface.send_message(f'Answer "{answ}" is not valid!')
                    answ = input(query)
                else:
                    CLIInterface.send_message(f'Set to default: {default_op}')
                    answ = default_op
        return answ

    @staticmethod
    def send_message(msg):
        print(msg)

    @staticmethod
    def interactive(parameters: dict, types: list, func: Callable[..., Any], continue_query: str = 'Adjust parameters?'):
        continue_query += ' (yes/no, default yes) '
        run_cycle = 'yes'
        func_ret = None
        while run_cycle.startswith('y'):
            for (p, v), t in zip(parameters.items(), types):
                v = CLIInterface.ask_for_parameter(p, t, default=v)
                parameters[p] = v
            func_ret = func(**parameters)
            run_cycle = CLIInterface.ask_question(continue_query).lower()
            if not run_cycle:
                run_cycle = 'yes'
        return parameters, func_ret


# --- EventBus wiring and pipeline/profile selection ---

# All available steps (order preserved). Used for step selection like profile selection.
ALL_STEPS = [
    "calibration",
    "integration",
    "subtraction",
    "simple_analysis",
    "plots",
    "polydispfit",
    "bodies",
    "dammif",
    "ai_analysis",
]
DEFAULT_STEPS = [
    "calibration",
    "integration",
    "subtraction",
    "simple_analysis",
    "plots",
    "polydispfit",
    "bodies",
]

# Pipeline definitions aligned with gui (for API/latest compatibility)
DEFAULT_PIPELINES = {
    "protein_v0": {
        "label": "protein-v0",
        "steps": list(ALL_STEPS),
        "default_steps": list(DEFAULT_STEPS),
    },
}


def _load_latest_steps():
    if os.path.exists(LATEST_STEPS_PATH):
        with open(LATEST_STEPS_PATH, "r") as f:
            data = yaml.safe_load(f) or {}
            steps = data.get("steps")
            if isinstance(steps, (list, tuple)):
                return {"steps": list(steps), "pipeline": data.get("pipeline")}
    return {}


def _step_list_to_indices(step_names: list) -> list:
    """Return list of indices into ALL_STEPS for the given step names (order preserved)."""
    step_to_idx = {s: i for i, s in enumerate(ALL_STEPS)}
    return [step_to_idx[s] for s in step_names if s in step_to_idx]


def connect(bus: EventBus) -> None:
    """Subscribe to request events and respond via the bus. One response per request."""

    def on_directory_requested(data):
        query = (data or {}).get("query", "Write a path to a directory for your data")
        try:
            path = CLIInterface.ask_for_file(query=query, obligatory=True)
            bus.publish(EventType.DIRECTORY_SPECIFIED, {"path": path})
        except PipelineInterrupt:
            bus.publish(EventType.PROGRAM_INTERRUPTED, {"reason": "directory selection canceled"})

    def on_file_requested(data):
        if not data:
            return
        directory = data.get("directory", "")
        query = data.get("query", "Upload file")
        filepattern = data.get("filepattern", "*")
        obligatory = data.get("obligatory", False)
        skip_if_exists = data.get("skip_if_exists", True)
        except_prev_paths = data.get("except_prev_paths", False)
        allow_same_time = data.get("allow_same_time", (1, float("inf")))
        try:
            paths = CLIInterface.wait_for_file(
                directory=directory,
                query=query,
                filepattern=filepattern,
                obligatory=obligatory,
                skip_if_exists=skip_if_exists,
                except_prev_paths=except_prev_paths,
                allow_same_time=allow_same_time,
            )
            bus.publish(EventType.FILE_UPLOADED, {"paths": paths if isinstance(paths, list) else [paths]})
        except PipelineInterrupt:
            bus.publish(EventType.PROGRAM_INTERRUPTED, {"reason": "file upload canceled"})
            if obligatory:
                bus.publish(EventType.FILE_UPLOAD_CANCELED, {"reason": "obligatory file missing"})
        except Exception as e:
            bus.publish(EventType.FILE_UPLOAD_CANCELED, {"reason": str(e)})
            if obligatory:
                bus.publish(EventType.PROGRAM_INTERRUPTED, {"reason": str(e)})

    def on_choice_requested(data):
        if data is None:
            return
        query = data.get("query", "")
        options = data.get("options") or {}
        default_op = data.get("default_op", "no default")
        try:
            choice = CLIInterface.ask_question(query=query, options=options, default_op=default_op)
            bus.publish(EventType.OPTION_CHOSEN, {"choice": choice})
        except PipelineInterrupt:
            bus.publish(EventType.OPTION_CHOICE_CANCELED, {})
            bus.publish(EventType.PROGRAM_INTERRUPTED, {"reason": "choice canceled"})

    def on_message(data):
        text = (data or {}).get("text", "")
        if text:
            CLIInterface.send_message(text)

    def on_pipeline_steps_requested(_data):
        latest_cfg = _load_latest_steps()
        has_latest = bool(latest_cfg.get("steps"))
        # Show steps with indices (same style as profile selection)
        CLIInterface.send_message("Choose pipeline steps:")
        for i, step in enumerate(ALL_STEPS):
            CLIInterface.send_message(f"  {i}: {step}")
        protein_v0_indices = ", ".join(str(i) for i in _step_list_to_indices(DEFAULT_STEPS))
        prompt_lines = [
            "Enter comma-separated indices to select, or one of the names of the following default sequences:",
            f"  protein-v0: {protein_v0_indices}",
        ]
        if has_latest:
            latest_steps = latest_cfg.get("steps") or []
            latest_idx_list = _step_list_to_indices(latest_steps)
            if latest_idx_list:
                latest_indices = ", ".join(str(i) for i in latest_idx_list)
                prompt_lines.append(f"  latest: {latest_indices}")
        CLIInterface.send_message("\n".join(prompt_lines))
        try:
            line = input().strip()
            raw_lower = line.lower()
            if raw_lower == "latest" and has_latest:
                pipeline_choice = latest_cfg.get("pipeline") or "protein_v0"
                steps = latest_cfg.get("steps") or DEFAULT_STEPS
                steps = list(steps)
            elif raw_lower in ("protein-v0", "protein_v0") or not line:
                pipeline_choice = "protein_v0"
                steps = list(DEFAULT_STEPS)
            else:
                pipeline_choice = "protein_v0"
                indices = []
                for s in line.split(","):
                    s = s.strip()
                    if s.isdigit():
                        idx = int(s)
                        if 0 <= idx < len(ALL_STEPS):
                            indices.append(idx)
                # Preserve order and avoid duplicates
                seen = set()
                steps = []
                for i in indices:
                    if i not in seen:
                        seen.add(i)
                        steps.append(ALL_STEPS[i])
                if not steps:
                    steps = list(DEFAULT_STEPS)
            bus.publish(
                EventType.PIPELINE_STEPS_SPECIFIED,
                {"pipeline_choice": pipeline_choice, "steps": steps},
            )
        except (EOFError, KeyboardInterrupt):
            bus.publish(EventType.PROGRAM_INTERRUPTED, {"reason": "pipeline selection canceled"})

    def on_profile_selection_requested(data):
        profiles_data = (data or {}).get("profiles_data") or []
        if not profiles_data:
            bus.publish(EventType.PROFILE_SELECTION_SPECIFIED, {"selected_profiles": {}})
            return
        for i, p in enumerate(profiles_data):
            CLIInterface.send_message(f"  {i}: {p.get('basename', '')}")
        CLIInterface.send_message("Enter comma-separated indices to select, or 'all' (empty = none):")
        try:
            line = input().strip().lower()
            if line == "all":
                selected = {p["basename"]: p for p in profiles_data}
            elif not line:
                selected = {}
            else:
                indices = []
                for s in line.split(","):
                    s = s.strip()
                    if s.isdigit():
                        idx = int(s)
                        if 0 <= idx < len(profiles_data):
                            indices.append(idx)
                selected = {
                    profiles_data[i]["basename"]: profiles_data[i]
                    for i in indices
                }
            bus.publish(EventType.PROFILE_SELECTION_SPECIFIED, {"selected_profiles": selected})
        except (EOFError, KeyboardInterrupt):
            bus.publish(EventType.PROFILE_SELECTION_SPECIFIED, {"selected_profiles": {}})

    bus.subscribe(EventType.DIRECTORY_REQUESTED, on_directory_requested)
    bus.subscribe(EventType.FILE_REQUESTED, on_file_requested)
    bus.subscribe(EventType.CHOICE_REQUESTED, on_choice_requested)
    bus.subscribe(EventType.MESSAGE, on_message)
    bus.subscribe(EventType.PIPELINE_STEPS_REQUESTED, on_pipeline_steps_requested)
    bus.subscribe(EventType.PROFILE_SELECTION_REQUESTED, on_profile_selection_requested)
