# Set threading environment variables BEFORE importing NumPy/SciPy/pyFAI
# to prevent threading conflicts in calibration worker thread
from .utils.threading_env import setup_threading_env, restore_threading_env

# Setup threading environment (will also be done by module import, but explicit here)
setup_threading_env()

import os
import sys
from tkinter import filedialog, messagebox


_GUI_DEPS_HELP = 'GUI dependencies are not installed. Install with: pip install "autosaxs[gui]"'


def _ask_working_directory(root):
    """Show directory selection dialog. Return path if valid empty directory chosen, None if user cancels."""
    while True:
        path = filedialog.askdirectory(
            parent=root,
            title="Select working directory (must be empty)",
            mustexist=True,
        )
        if not path:
            return None
        try:
            if os.listdir(path):
                messagebox.showerror(
                    "Invalid directory",
                    "The selected directory is not empty. Please choose an empty directory.",
                    parent=root,
                )
                continue
        except OSError as e:
            messagebox.showerror(
                "Error",
                f"Cannot access directory: {e}",
                parent=root,
            )
            continue
        return path


def main():
    """Main entry point for guisaxs (SAXS data processor GUI)."""
    try:
        import customtkinter as ctk
        from tkinterdnd2 import TkinterDnD

        from .core.style import COLOR_THEME
        from .gui import SAXSProcessorGUI
    except ImportError:
        print(_GUI_DEPS_HELP, file=sys.stderr)
        raise SystemExit(1)

    # Create root window with DND support
    root = TkinterDnD.Tk()
    root.withdraw()  # Hide until we have working directory

    # Set theme from style module
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme(COLOR_THEME)

    working_dir = _ask_working_directory(root)
    if not working_dir:
        root.destroy()
        sys.exit(0)

    root.deiconify()

    # Create GUI with chosen working directory
    app = SAXSProcessorGUI(root, working_dir)

    # Restore environment variables when window is closed
    def on_closing():
        restore_threading_env()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)

    # Run the application
    root.mainloop()


if __name__ == "__main__":
    main()
