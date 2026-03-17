"""
Entry point for the interactive SAXS pipeline.
Wires EventBus, connects CLI (or GUI), creates Controller and runs pipeline_interactive.
See docs/pipeline_interactive_spec.md §3.
"""
from autosaxs import EventBus, Controller, cli_interface, gui_interface, viewer

if __name__ == "__main__":
    event_bus = EventBus()
    cli_interface.connect(event_bus)
    # gui_interface.connect(event_bus)
    controller = Controller(event_bus, viewer.PLTViewer())
    controller.pipeline_interactive(fast_forward=True)
