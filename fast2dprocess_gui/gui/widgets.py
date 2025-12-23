"""Utility widgets and helper functions for GUI."""
import customtkinter as ctk
import tkinter as tk


def center_window(win):
    """Center a Tk/CTk window on the primary screen."""
    win.update_idletasks()
    width = win.winfo_width()
    height = win.winfo_height()
    screen_width = win.winfo_screenwidth()
    screen_height = win.winfo_screenheight()
    x = int((screen_width - width) / 2)
    y = int((screen_height - height) / 2)
    win.geometry(f"+{x}+{y}")


def enable_text_copying(widget):
    """
    Enable text copying for a widget via right-click context menu.
    Note: CTkEntry widgets already support text selection and Ctrl+C by default.
    This mainly adds right-click copy functionality for CTkLabel widgets.
    
    Args:
        widget: The widget to enable copying for
    """
    def get_text_from_widget(w):
        """Extract text from various widget types."""
        if isinstance(w, (ctk.CTkLabel, ctk.CTkCheckBox, tk.Label)):
            return w.cget("text")
        elif hasattr(w, 'cget'):
            try:
                text = w.cget("text")
                if text:
                    return text
            except:
                pass
        # For entry widgets, get selected text if any, otherwise all text
        if isinstance(w, (ctk.CTkEntry, tk.Entry)):
            try:
                # Try to get selected text first
                if w.selection_present():
                    return w.selection_get()
                else:
                    return w.get()
            except:
                return w.get() if hasattr(w, 'get') else ""
        return ""
    
    def copy_text(event=None):
        """Copy text to clipboard."""
        text = get_text_from_widget(widget)
        if text:
            root = widget.winfo_toplevel()
            root.clipboard_clear()
            root.clipboard_append(str(text))
    
    def show_context_menu(event):
        """Show context menu with copy option."""
        text = get_text_from_widget(widget)
        if text:
            menu = tk.Menu(widget, tearoff=0)
            menu.add_command(label="Copy", command=copy_text)
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
    
    # Only bind right-click context menu (CTkEntry already supports Ctrl+C)
    widget.bind("<Button-3>", show_context_menu)  # Button-3 is right-click on Linux/Windows
    widget.bind("<Button-2>", show_context_menu)  # Button-2 is right-click on macOS


def enable_text_copying_recursive(parent):
    """
    Recursively enable text copying for all text widgets in a container.
    This is a global approach - call once on the root window after all widgets are created.
    
    Note: CTkEntry widgets already support text selection and Ctrl+C by default.
    This mainly adds right-click copy functionality for CTkLabel and CTkCheckBox widgets.
    
    Args:
        parent: The parent widget/container to process
    """
    # Process the parent itself if it's a text widget
    if isinstance(parent, (ctk.CTkLabel, ctk.CTkCheckBox, tk.Label)):
        enable_text_copying(parent)
    # Also enable for CTkEntry to add right-click menu (Ctrl+C already works)
    elif isinstance(parent, (ctk.CTkEntry, tk.Entry)):
        enable_text_copying(parent)
    
    # Recursively process all children
    try:
        for child in parent.winfo_children():
            enable_text_copying_recursive(child)
    except:
        pass

