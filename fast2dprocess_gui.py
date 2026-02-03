# Set threading environment variables BEFORE importing NumPy/SciPy/pyFAI
# to prevent threading conflicts in calibration worker thread
from fast2dprocess_gui.utils.threading_env import setup_threading_env, restore_threading_env

# Setup threading environment (will also be done by module import, but explicit here)
setup_threading_env()

import customtkinter as ctk
from tkinterdnd2 import TkinterDnD
from fast2dprocess_gui.core.style import COLOR_THEME
from fast2dprocess_gui.gui import SAXSProcessorGUI


def main():
    """Main entry point for the SAXS Data Processor GUI."""
    # Create root window with DND support
    root = TkinterDnD.Tk()
    
    # Set theme from style module
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme(COLOR_THEME)
    
    # Create GUI
    app = SAXSProcessorGUI(root)
    
    # Restore environment variables when window is closed
    def on_closing():
        restore_threading_env()
        root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    
    # Run the application
    root.mainloop()


if __name__ == "__main__":
    main()
