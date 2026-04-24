"""
Entry point for the interactive SAXS pipeline.
Wires EventBus, connects CLI (or GUI), creates Controller and runs pipeline_interactive.
See docs/pipeline_interactive_spec.md §3.
"""
from autosaxs.core.event_bus import EventBus
from autosaxs.core import viewer
from autosaxs.pipeline.saxs_controller import Controller
from autosaxs.pipeline import cli_interface, gui_interface

if __name__ == "__main__":
    event_bus = EventBus()
    cli_interface.connect(event_bus)
    # gui_interface.connect(event_bus)
    controller = Controller(event_bus, viewer.PLTViewer())
    controller.pipeline_interactive(fast_forward=True)
