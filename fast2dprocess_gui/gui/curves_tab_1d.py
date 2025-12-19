"""1D curves display tab for SAXS curves."""
import os
import customtkinter as ctk
import tkinter as tk
import numpy as np
from typing import Optional, Callable
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import matplotlib.cm as cm
from ..core.constants import TEMP_DIR
from utils import read_saxs


class CurvesTab1D:
    """Tab for displaying 1D SAXS curves with checkbox selection."""
    
    def __init__(self, parent):
        """
        Initialize the 1D curves tab.
        
        Args:
            parent: Parent tabview or frame
        """
        self.tab = parent
        self.tab.grid_columnconfigure(0, weight=0)  # Checkbox panel (fixed width)
        self.tab.grid_columnconfigure(1, weight=1)  # Plot area (flexible)
        self.tab.grid_rowconfigure(1, weight=1)  # Main content row
        
        # Store curves: {filename: (file_path, curve_type, checkbox_var, checkbox_widget)}
        self.curves = {}
        self.last_added_curve = None
        
        # Left panel: Checkboxes for curve selection
        checkbox_frame = ctk.CTkScrollableFrame(self.tab, width=200)
        checkbox_frame.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(10, 5), pady=10)
        checkbox_frame.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(
            checkbox_frame,
            text="Curves",
            font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=0, column=0, pady=(0, 10))
        
        self.checkbox_frame = checkbox_frame
        
        # Right panel: Plot area
        plot_frame = ctk.CTkFrame(self.tab)
        plot_frame.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(5, 10), pady=10)
        plot_frame.grid_columnconfigure(0, weight=1)
        plot_frame.grid_rowconfigure(1, weight=1)  # Canvas row gets most space
        
        # Plot type selection frame (narrow bar at top)
        plot_type_frame = ctk.CTkFrame(plot_frame)
        plot_type_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        plot_type_frame.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(
            plot_type_frame,
            text="Plot Type:",
            font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=10, pady=5)
        
        # Create plot type segmented button
        plot_types = ["I vs q", "log I vs q", "log I vs log q", "Guinier: log I vs q^2", "Kratky: q^2 * I vs q"]
        self.plot_type_segbutton = ctk.CTkSegmentedButton(
            plot_type_frame,
            values=plot_types,
            command=self.on_plot_type_change
        )
        self.plot_type_segbutton.set("I vs q")
        self.plot_type_segbutton.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        
        # Create figure for 1D curves
        self.fig = Figure(figsize=(10, 6))
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title("1D SAXS Curves")
        self.ax.set_xlabel("q (nm⁻¹)")
        self.ax.set_ylabel("Intensity (a.u.)")
        self.ax.grid(True, alpha=0.3)
        
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        
        self.plot_type = "I vs q"
        self.update_callback: Optional[Callable[[], None]] = None  # Will be set by parent
    
    def on_plot_type_change(self, value):
        """Callback when plot type is changed."""
        self.plot_type = value
        self.update_display()
    
    def add_curve(self, file_path, curve_type="Unknown"):
        """Add a new curve to the list. Returns True if added, False if already exists."""
        if not file_path or not os.path.exists(file_path):
            return False
        
        filename = os.path.basename(str(file_path))
        
        # Check if curve already exists
        if filename in self.curves:
            return False
        
        # Create checkbox variable
        checkbox_var = tk.BooleanVar(value=False)  # Default to unchecked
        
        # Create checkbox widget
        checkbox = ctk.CTkCheckBox(
            self.checkbox_frame,
            text=filename,
            variable=checkbox_var,
            command=self.update_display,
            font=ctk.CTkFont(size=10)
        )
        
        # Find next available row (skip title label at row 0)
        row = len(self.curves) + 1
        checkbox.grid(row=row, column=0, sticky="w", padx=10, pady=2)
        
        # Store curve data
        self.curves[filename] = (file_path, curve_type, checkbox_var, checkbox)
        
        # Set as last added and check it (default behavior: show only last curve)
        self.last_added_curve = filename
        
        # Uncheck all previous curves and check the new one
        for other_filename, (_, _, other_var, _) in self.curves.items():
            if other_filename != filename:
                other_var.set(False)
        checkbox_var.set(True)
        
        # Update display
        self.update_display()
        
        return True
    
    def update_display(self):
        """Update the plot with currently checked curves."""
        self.ax.clear()
        
        # Get current plot type
        self.plot_type = self.plot_type_segbutton.get()
        
        # Color palette for curves
        n_curves = max(len(self.curves), 1)  # Avoid division by zero
        colors = cm.get_cmap('tab10')(np.linspace(0, 1, n_curves))
        color_idx = 0
        
        # Plot all checked curves
        for filename, (file_path, curve_type, checkbox_var, _) in self.curves.items():
            if checkbox_var.get():  # Only plot if checked
                self._plot_1d_curve(file_path, filename, colors[color_idx % len(colors)], self.plot_type)
                color_idx += 1
        
        # Set labels based on plot type
        if self.plot_type == "I vs q":
            self.ax.set_xlabel("q (nm⁻¹)")
            self.ax.set_ylabel("Intensity (a.u.)")
            self.ax.set_yscale('linear')
        elif self.plot_type == "log I vs q":
            self.ax.set_xlabel("q (nm⁻¹)")
            self.ax.set_ylabel("log₁₀(I)")
            self.ax.set_yscale('linear')
        elif self.plot_type == "log I vs log q":
            self.ax.set_xlabel("log₁₀(q / nm⁻¹)")
            self.ax.set_ylabel("log₁₀(I)")
            self.ax.set_yscale('linear')
        elif self.plot_type == "Guinier: log I vs q^2":
            self.ax.set_xlabel("q² (nm⁻²)")
            self.ax.set_ylabel("log₁₀(I)")
            self.ax.set_yscale('linear')
        elif self.plot_type == "Kratky: q^2 * I vs q":
            self.ax.set_xlabel("q (nm⁻¹)")
            self.ax.set_ylabel("q² × I (a.u.)")
            self.ax.set_yscale('linear')
        
        self.ax.set_title(f"1D SAXS Curves - {self.plot_type}")
        self.ax.grid(True, alpha=0.3)
        if color_idx > 0:  # Only show legend if there are curves
            self.ax.legend(loc='best', fontsize=9)
        self.canvas.draw()
    
    def _plot_1d_curve(self, file_path, label, color, plot_type):
        """Helper to load and plot a 1D curve with specified plot type as scatter plot."""
        if file_path and os.path.exists(file_path):
            try:
                q, I, sigma, _ = read_saxs(file_path)
                q_nm = q * 1e-9  # Convert q from 1/m to 1/nm
                
                # Filter out zero/negative values for log plots
                valid_mask = (I > 0) & (q_nm > 0)
                q_plot = q_nm[valid_mask]
                I_plot = I[valid_mask]
                
                if plot_type == "I vs q":
                    x_data = q_plot
                    y_data = I_plot
                elif plot_type == "log I vs q":
                    x_data = q_plot
                    y_data = np.log10(I_plot)
                elif plot_type == "log I vs log q":
                    x_data = np.log10(q_plot)
                    y_data = np.log10(I_plot)
                elif plot_type == "Guinier: log I vs q^2":
                    x_data = q_plot ** 2
                    y_data = np.log10(I_plot)
                elif plot_type == "Kratky: q^2 * I vs q":
                    x_data = q_plot
                    y_data = (q_plot ** 2) * I_plot
                else:
                    # Default to I vs q
                    x_data = q_plot
                    y_data = I_plot
                
                # Use scatter plot with nice-looking markers
                # Adjust marker size based on data density
                n_points = len(x_data)
                if n_points > 1000:
                    marker_size = 8
                    alpha = 0.6
                elif n_points > 500:
                    marker_size = 10
                    alpha = 0.7
                else:
                    marker_size = 12
                    alpha = 0.8
                
                self.ax.scatter(
                    x_data, y_data,
                    label=label,
                    color=color,
                    s=marker_size,
                    alpha=alpha,
                    edgecolors='none',
                    marker='o'
                )
            except Exception as e:
                print(f"Error loading {label} curve: {str(e)}")
    
    def display_curves(self, *args):
        """Legacy method for backward compatibility. Now uses update_display."""
        self.update_display()
    
    def save_plot(self, filename):
        """Save figure to temp directory."""
        try:
            plot_path = os.path.join(TEMP_DIR, filename)
            self.fig.savefig(plot_path, dpi=150, bbox_inches='tight')
        except Exception as e:
            print(f"Error saving plot: {e}")

