import os
import sys
import time
import select

from processor import *  # noqa: F401,F403  (brings in utils helpers like read_from_tiff, get_interring_dist_px, integrate_2d_to_1d)
from interface import CLIInterface, PipelineInterrupt
from viewer import PLTViewer
from context import Context


def timed_input(prompt: str, timeout_sec: float, default: str) -> str:
    """
    Wait for *any key press* for up to timeout_sec seconds.
    If no key is pressed within timeout_sec, return default.
    If a key is pressed, switch to normal line input and return the entered value
    (empty line keeps the default).
    """
    import threading
    import termios
    import tty

    sys.stdout.write(prompt)
    sys.stdout.flush()

    key_pressed = {"pressed": False}
    done_event = threading.Event()

    def watch_keypress():
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            # Set terminal to cbreak mode to capture a single key without waiting for Enter
            tty.setcbreak(fd)
            ch = sys.stdin.read(1)
            if ch:
                key_pressed["pressed"] = True
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            done_event.set()

    watcher = threading.Thread(target=watch_keypress, daemon=True)
    watcher.start()

    # Wait for either a key press or timeout
    done_event.wait(timeout_sec)

    if not key_pressed["pressed"]:
        CLIInterface.send_message(
            f"\nNo key press within {timeout_sec:.0f} seconds. Using default: {default}"
        )
        return default

    # A key was pressed within timeout; now read the full line normally
    sys.stdout.write("\n")
    sys.stdout.flush()
    line = input("Enter value: ").rstrip("\n")
    return line if line else default


def wait_for_keypress(timeout_sec: float) -> bool:
    """
    Wait up to timeout_sec seconds for the user to press Enter.
    Returns True if Enter was pressed, False if timeout elapsed.
    """
    CLIInterface.send_message(f"Press Enter within {timeout_sec:.0f} seconds to interrupt, or wait to continue...")
    rlist, _, _ = select.select([sys.stdin], [], [], timeout_sec)
    if rlist:
        # Consume the line the user entered
        sys.stdin.readline()
        return True
    return False


def run_calibration_cycle(interface: CLIInterface, viewer: PLTViewer, directory, context: Context):
    """
    One calibration cycle:
      - ask for working directory
      - ask for calibrant type (with 2s timeout, default AgBh)
      - wait for calibrant .tif file path
      - perform calibration, show plots for 5s, print refined parameters
    Returns (context, integrator) or (None, None) if calibration was not performed.
    """

    # Calibrant image path
    calibrant_path = interface.wait_for_file(
        directory, 
        query="Upload a _calib.tif file with calibration image",
        obligatory=True,
        filepattern="*_calib.tif"
    )

    # Prepare calibration data
    calib_data = read_from_tiff(calibrant_path)

    interface.send_message("Autocalibration...")

    center_ref_params = {
        k: context["center_refinement", k]
        for k in ["q_start", "q_stop", "min_segment_len"]
    }
    interface.send_message("    Center search...")

    # Center refinement
    center_step_ret = find_center(calib_data, **center_ref_params)

    d_geom = context["detector_geometry"]
    interring_dist_px = get_interring_dist_px(
        d_geom["dist"], d_geom["wavelength"], d_geom["pixel_size"][0]
    )

    ring_search_params = {
        k: context["ring_search", k]
        for k in ["q_stop", "ring_I_threshold", "r_max_px", "r_step_px"]
    }
    ring_search_params.update(
        {
            "r_beam_px": context["r_beam_px"],
            "center_y_px": center_step_ret["center_y_px"],
            "center_x_px": center_step_ret["center_x_px"],
            "interring_dist_px": interring_dist_px,
        }
    )
    interface.send_message("    Rings identification...")
    rings_step_ret = find_rings(calib_data, **ring_search_params)

    geometry_params = {
        k: context["detector_geometry", k]
        for k in ["dist", "wavelength", "pixel_size", "rot1", "rot2", "rot3"]
    }
    geometry_params.update(
        {
            "r_beam_px": context["r_beam_px"],
            "center_y_px": center_step_ret["center_y_px"],
            "center_x_px": center_step_ret["center_x_px"],
            "calibrant_name": context['calibrant_name'],
        }
    )
    interface.send_message("    Geometry refinement...")
    refine_step_ret = refine(
        calib_data,
        rings_step_ret["rings"],
        **geometry_params,
    )

    # Combined visualization of calibration results for 5 seconds
    calibration_results_file = os.path.join(directory, "calibration.png")
    viewer.view_calibration(
        img_data=calib_data,
        tiff_path=calibrant_path,
        show_duration=10.0,
        plotFilePath=calibration_results_file,
        **center_step_ret,
        **rings_step_ret,
        **refine_step_ret,
    )

    # Persist integrator and refined parameters
    integrator_subd = os.path.join(directory, "integrator_params")
    refine_step_ret["integrator"].to_disk(integrator_subd)

    refined = refine_step_ret["refined"]
    refined.update({"wavelength": context["detector_geometry", "wavelength"]})
    context["refined"] = refined
    context.update_config("refined", values=refined)

    interface.send_message(
        "\n-- Calibrated geometry parameters --\n"
        + "\n".join(f"{p}: {v}" for p, v in refined.items())
        + "\n"
    )

    interface.send_message("Finished calibration")

    return context, refine_step_ret["integrator"]


def calibration_loop(interface: CLIInterface, viewer: PLTViewer):
    """
    Full calibration UX loop with optional redo:
      - runs run_calibration_cycle()
      - 5 second pause; if user presses Enter, ask if they want to redo
      - if confirmed, repeat calibration (optionally with changed config or calibrant path)
    Returns (context, integrator).
    """
    context = None
    integrator = None

    # Working directory
    directory = interface.ask_for_file("Write a path to the working directory", obligatory=True)

    # Create a minimal Context: only config from <directory>/config.conf is used
    context = Context(directory, pipe_descr_path=None, interface=interface)

    # Calibrant type with timeout and default
    calibrant_name = timed_input(
        "Enter calibrant name (default AgBh, 5 seconds to change): ",
        timeout_sec=5.0,
        default="AgBh",
    )
    context["calibrant_name"] = calibrant_name

    while True:
        context, integrator = run_calibration_cycle(interface, viewer, directory=directory, context=context)

        # 5s pause during which user can decide to redo calibration
        if wait_for_keypress(timeout_sec=5.0):
            redo = interface.ask_question(
                "Redo calibration? (yes/no, default no) ", default_op="n"
            )
            if redo.lower().startswith("y"):
                interface.send_message(
                    "Redoing calibration. You may adjust configuration or provide another calibrant path."
                )
                continue

        # Either timeout elapsed (no keypress) or redo not confirmed → finish
        break

    return context, integrator


def integration_loop(
    interface: CLIInterface, viewer: PLTViewer, context: Context, integrator: IntegratorExtended
):
    """
    Integration UX loop:
      - waits for .tif file path from the user
      - integrates 2D SAXS data to 1D and writes .dat file
      - loop can be exited after a keypress + confirmation
    """
    directory = context.directory
    run_loop = True
    while run_loop:
        to_int_path = interface.wait_for_file(
            directory, 
            query="Upload .tif file with 2D SAXS data to integrate (or press Enter to request exit)",
            obligatory=False,
            filepattern="*.tif"
        )

        if not to_int_path:
            # User pressed Enter without providing a file → ask for exit confirmation
            confirm_exit = interface.ask_question(
                "Exit integration loop? (yes/no, default yes) ", default_op="y"
            )
            if confirm_exit.lower().startswith("y"):
                break
            else:
                continue

        root, fname = os.path.split(to_int_path)
        base, _ = os.path.splitext(fname)
        metadata = {"type": "unknown", "source_2d_path": to_int_path}

        int_path = os.path.join(directory, f"int_{base}.dat")

        integrate_2d_to_1d(
            integrator,
            read_from_tiff(to_int_path),
            destpath=int_path,
            metadata=metadata,
        )
        interface.send_message(f"Integrated 1D data saved to: {int_path}")

        # After each integration, allow user to interrupt the cycle
        if wait_for_keypress(timeout_sec=5.0):
            confirm_exit = interface.ask_question(
                "Exit integration loop? (yes/no, default no) ", default_op="n"
            )
            if confirm_exit.lower().startswith("y"):
                run_loop = False


def main():
    interface = CLIInterface()
    viewer = PLTViewer()

    try:
        # Calibration phase
        context, integrator = calibration_loop(interface, viewer)
        if context is None or integrator is None:
            interface.send_message("Calibration was not completed. Exiting.")
            return

        # Integration phase
        integration_loop(interface, viewer, context, integrator)
        interface.send_message("Program finished.")

    except PipelineInterrupt as e:
        interface.send_message(f"Pipeline interrupted by user: {e}")
    except KeyboardInterrupt:
        interface.send_message("Interrupted by user (Ctrl+C). Exiting.")


if __name__ == "__main__":
    main()


