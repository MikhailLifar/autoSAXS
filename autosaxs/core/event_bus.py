# EventBus and event types for Controller–Interface I/O.
# Single channel: Controller publishes requests; Interface implementation
# (cli_interface or gui_interface) publishes responses.
# See docs/pipeline_interactive_spec.md §3.1.

from enum import Enum
from typing import Any, Callable, Dict, List


class EventType(Enum):
    # Directory
    DIRECTORY_REQUESTED = "directory_requested"
    DIRECTORY_SPECIFIED = "directory_specified"
    # File
    FILE_REQUESTED = "file_requested"
    FILE_UPLOADED = "file_uploaded"
    FILE_UPLOAD_CANCELED = "file_upload_canceled"
    # Choice
    CHOICE_REQUESTED = "choice_requested"
    OPTION_CHOSEN = "option_chosen"
    OPTION_CHOICE_CANCELED = "option_choice_canceled"
    # One-way
    MESSAGE = "message"
    PROGRAM_INTERRUPTED = "program_interrupted"
    # Pipeline/step and profile selection (same request–response pattern)
    PIPELINE_STEPS_REQUESTED = "pipeline_steps_requested"
    PIPELINE_STEPS_SPECIFIED = "pipeline_steps_specified"
    PROFILE_SELECTION_REQUESTED = "profile_selection_requested"
    PROFILE_SELECTION_SPECIFIED = "profile_selection_specified"


class EventBus:
    """
    Single channel for Controller–Interface I/O.
    One response per request; PROGRAM_INTERRUPTED always exits.
    Delivery is synchronous: publish() calls subscribers in turn.
    """

    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable[[Any], None]]] = {}

    def subscribe(self, event_type: EventType, callback: Callable[[Any], None]) -> None:
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: EventType, callback: Callable[[Any], None]) -> None:
        if event_type in self._subscribers:
            try:
                self._subscribers[event_type].remove(callback)
            except ValueError:
                pass

    def publish(self, event_type: EventType, data: Any = None) -> None:
        if event_type not in self._subscribers:
            return
        for cb in list(self._subscribers[event_type]):
            try:
                cb(data)
            except Exception as e:
                import logging
                logging.getLogger(__name__).exception("Event callback error for %s: %s", event_type, e)
