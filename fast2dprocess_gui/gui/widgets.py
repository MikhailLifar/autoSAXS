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

