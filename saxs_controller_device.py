import json
import sys
import os
import traceback

import tango
from tango import DevState
from tango.server import Device, command, attribute, run

# Make sure we can import the existing Controller
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

from saxs_controller import Controller, CLIInterface, PLTViewer  # noqa: E402


class SAXSProcessing(Device):
    """
    Tango device wrapping the existing SAXS `Controller`.

    Design:
    - Provides:
        - RW attribute `config` (JSON-encoded dictionary)
        - Command `execute_pipeline` which runs the high-level pipeline
          (no commands for individual steps).
    """

    # -----------------
    # Tango attributes
    # -----------------

    @attribute(
        name="config",
        dtype=str,
        label="Configuration",
        doc="JSON-encoded configuration dictionary.",
        access=tango.AttrWriteType.READ_WRITE,
    )
    def config(self):
        """
        Returns current configuration as JSON string.
        """
        return json.dumps(self._config or {})

    @config.write
    def config(self, value):
        """
        Accepts configuration as JSON string and stores it internally.
        """
        try:
            cfg = json.loads(value) if value else {}
            if not isinstance(cfg, dict):
                raise ValueError("Config must be a JSON object (dictionary).")
        except Exception as exc:
            msg = f"Failed to parse config JSON: {exc}"
            self.error_stream(msg)
            raise tango.DevFailed(msg)

        self._config = cfg
        self.info_stream(f"Config updated: keys = {list(self._config.keys())}")

    # -----------------
    # Tango commands
    # -----------------

    @command(dtype_in=bool, doc_in="If true, runs pipeline in 'fast_forward' mode.", dtype_out=str, doc_out="Result message.")
    def execute_pipeline(self, fast_forward):
        """
        Execute the full SAXS pipeline once.

        This calls the existing high-level `pipeline_interactive` method of the
        wrapped `Controller`. No granular step commands are exposed.
        """
        if self._controller is None:
            raise tango.DevFailed("Controller is not initialized.")

        # Update state
        self.set_state(DevState.RUNNING)
        self.set_status("Pipeline execution in progress.")

        try:
            # NOTE:
            # - The current `Controller.pipeline_interactive` expects to use its
            #   own CLI-driven `interface` for I/O.
            # - The self._config dict can be used in the future to control
            #   behavior (e.g. choose batch vs interactive pipeline), but for
            #   now we just pass fast_forward flag through.
            self.info_stream(
                f"Starting pipeline_interactive(fast_forward={fast_forward}) "
                f"with config keys: {list(self._config.keys())}"
            )

            # Blocking call; pipeline handles its own user interaction.
            self._controller.pipeline_interactive(fast_forward=bool(fast_forward))

            self.set_state(DevState.ON)
            self.set_status("Pipeline execution finished successfully.")
            return "Pipeline executed successfully."

        except Exception as exc:
            tb = traceback.format_exc()
            msg = f"Error during pipeline execution: {exc}"
            self.error_stream(msg)
            self.debug_stream(tb)
            self.set_state(DevState.FAULT)
            self.set_status(msg)
            raise tango.DevFailed(msg)

    # -----------------
    # Device lifecycle
    # -----------------

    def init_device(self):
        """
        Tango initialization hook.
        """
        super().init_device()

        # Internal configuration storage (JSON-serializable dict)
        self._config = {}

        # Wrap the existing CLI-based Controller for now.
        # Later this can be replaced by a Tango-aware Interface/Viewer.
        try:
            interface = CLIInterface()
            viewer = PLTViewer()
            self._controller = Controller(interface, viewer)
            self.set_state(DevState.STANDBY)
            self.set_status("ControllerDevice initialized and ready.")
            self.info_stream("ControllerDevice initialized successfully.")
        except Exception as exc:
            tb = traceback.format_exc()
            msg = f"Failed to initialize underlying Controller: {exc}"
            self.error_stream(msg)
            self.debug_stream(tb)
            self._controller = None
            self.set_state(DevState.FAULT)
            self.set_status(msg)
            raise tango.DevFailed(msg)


def main(args=None):
    """
    Entry point for running the Tango device server.
    """
    # Allow main() to be called both from CLI and programmatically.
    if args is None:
        args = sys.argv

    run((SAXSProcessing,), args=args)


if __name__ == "__main__":
    main()


