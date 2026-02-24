# Script-based interface to the pipeline (§3.2).
# Responds to pipeline requests from function arguments and hardcoded values.

import glob
import os

from .cli_interface import PipelineInterrupt
from .event_bus import EventBus, EventType
from .saxs_controller import Controller
from . import viewer


def _resolve_file_request(directory: str, filepattern: str, obligatory: bool):
    """Resolve filepattern under directory; return list of paths. Raise if obligatory and missing."""
    pattern = os.path.join(directory, filepattern)
    paths = sorted(glob.glob(pattern))
    if obligatory and not paths:
        raise FileNotFoundError(f"Required file(s) not found: {pattern}")
    return paths


def _wrap_handler(bus: EventBus, callback):
    """Wrap a callback so that return (evt, payload) is published; on exception, publish PROGRAM_INTERRUPTED and re-raise."""
    def handler(data):
        try:
            resp_evt, resp_data = callback(data)
            if resp_evt is not None:
                bus.publish(resp_evt, resp_data or {})
        except Exception as e:
            bus.publish(EventType.PROGRAM_INTERRUPTED, {"reason": str(e)})
            raise
    return handler


def fast_first_processing(directory: str, steps=None, mask_choice=None):
    """Run pipeline with configurable steps (§3.2.1).
    directory: working directory; required files must exist there.
    steps: list of step names (e.g. ["calibration", "integration"]). Default: calibration, integration, subtraction, simple_analysis, plots.
    mask_choice: when calibration asks for mask, use this choice: 'a' (automask), 'f' (from file), 'c' (combine). Default: 'c'.
    Raises if a required file is missing or on alignment failure.
    """
    if steps is None:
        steps = ["calibration", "integration", "subtraction", "simple_analysis", "plots"]
    if mask_choice is None:
        mask_choice = "c"
    pipeline_choice = "protein_v0"

    def on_pipeline_steps_requested(_data):
        return (EventType.PIPELINE_STEPS_SPECIFIED, {"pipeline_choice": pipeline_choice, "steps": steps})

    def on_directory_requested(_data):
        return (EventType.DIRECTORY_SPECIFIED, {"path": directory})

    def on_file_requested(data):
        d = data or {}
        dir_ = d.get("directory") or directory
        filepattern = d.get("filepattern", "*")
        obligatory = d.get("obligatory", False)
        paths = _resolve_file_request(dir_, filepattern, obligatory)
        if not paths and filepattern == "raw/*_sample.tif":
            raise PipelineInterrupt("No sample files to process")
        return (EventType.FILE_UPLOADED, {"paths": paths})

    def on_choice_requested(data):
        d = data or {}
        query = (d.get("query") or "").lower()
        if "mask" in query:
            return (EventType.OPTION_CHOSEN, {"choice": mask_choice})
        return (EventType.OPTION_CHOSEN, {"choice": "no"})

    def on_message(data):
        text = (data or {}).get("text") or ""
        if "overlapped" in text or "not_paired" in text or "not paired" in text:
            raise RuntimeError("Buffer-sample alignment failure")
        return (None, None)

    def on_profile_selection_requested(_data):
        raise RuntimeError("Profile selection not used in fast_first_processing")

    def _connect(bus: EventBus):
        bus.subscribe(EventType.PIPELINE_STEPS_REQUESTED, _wrap_handler(bus, on_pipeline_steps_requested))
        bus.subscribe(EventType.DIRECTORY_REQUESTED, _wrap_handler(bus, on_directory_requested))
        bus.subscribe(EventType.FILE_REQUESTED, _wrap_handler(bus, on_file_requested))
        bus.subscribe(EventType.CHOICE_REQUESTED, _wrap_handler(bus, on_choice_requested))
        bus.subscribe(EventType.MESSAGE, _wrap_handler(bus, on_message))
        bus.subscribe(EventType.PROFILE_SELECTION_REQUESTED, _wrap_handler(bus, on_profile_selection_requested))

    event_bus = EventBus()
    _connect(event_bus)
    controller = Controller(event_bus, viewer.PLTViewer())
    return controller.pipeline_interactive(fast_forward=True)


def slow_second_processing(directory: str, selected_profiles: list):
    """Run pipeline with steps simple_analysis, polydispfit, bodies, dammif (§3.2.2). Plots are run in fast first processing.
    directory: working directory (assumes calibration, integration, subtraction already done).
    selected_profiles: list of file names from the subtracted subdirectory to process (e.g. ["sub_foo.dat", "sub_bar.dat"]).
    The controller sends profiles_data (list of profile dicts with full paths); we filter by these names and return the expected dict.
    """
    steps = ["simple_analysis", "polydispfit", "bodies", "dammif"]
    pipeline_choice = "protein_v0"
    selected_filenames = set(selected_profiles or [])

    def on_pipeline_steps_requested(_data):
        return (EventType.PIPELINE_STEPS_SPECIFIED, {"pipeline_choice": pipeline_choice, "steps": steps})

    def on_directory_requested(_data):
        return (EventType.DIRECTORY_SPECIFIED, {"path": directory})

    def on_file_requested(data):
        d = data or {}
        dir_ = d.get("directory") or directory
        filepattern = d.get("filepattern", "*")
        obligatory = d.get("obligatory", False)
        paths = _resolve_file_request(dir_, filepattern, obligatory)
        if not paths and filepattern == "subtracted/*.dat":
            raise PipelineInterrupt("No sample files to process")
        return (EventType.FILE_UPLOADED, {"paths": paths})

    def on_choice_requested(data):
        d = data or {}
        query = (d.get("query") or "").lower()
        if "upload more" in query:
            return (EventType.OPTION_CHOSEN, {"choice": "no"})
        return (EventType.OPTION_CHOSEN, {"choice": "no"})

    def on_message(_data):
        return (None, None)

    def on_profile_selection_requested(data):
        # Controller sends {"profiles_data": list of profile dicts}; each profile has "path" (full path), "basename", etc.
        profiles_data = (data or {}).get("profiles_data") or []
        filtered = {
            p["basename"]: p
            for p in profiles_data
            if os.path.basename(p.get("path") or "") in selected_filenames
        }
        if len(filtered) == 0:
            raise PipelineInterrupt("No profiles selected")
        return (EventType.PROFILE_SELECTION_SPECIFIED, {"selected_profiles": filtered})

    def _connect(bus: EventBus):
        bus.subscribe(EventType.PIPELINE_STEPS_REQUESTED, _wrap_handler(bus, on_pipeline_steps_requested))
        bus.subscribe(EventType.DIRECTORY_REQUESTED, _wrap_handler(bus, on_directory_requested))
        bus.subscribe(EventType.FILE_REQUESTED, _wrap_handler(bus, on_file_requested))
        bus.subscribe(EventType.CHOICE_REQUESTED, _wrap_handler(bus, on_choice_requested))
        bus.subscribe(EventType.MESSAGE, _wrap_handler(bus, on_message))
        bus.subscribe(EventType.PROFILE_SELECTION_REQUESTED, _wrap_handler(bus, on_profile_selection_requested))

    event_bus = EventBus()
    _connect(event_bus)
    controller = Controller(event_bus, viewer.PLTViewer())
    return controller.pipeline_interactive(fast_forward=True)
