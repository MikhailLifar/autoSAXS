"""1D curves display tab for SAXS curves."""
import os
import customtkinter as ctk
import tkinter as tk
import numpy as np
from typing import Optional, Callable
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import matplotlib.cm as cm
import matplotlib.pyplot as plt
from ..core.constants import TEMP_DIR
from ..utils.filename_utils import generate_curve_plot_filename
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
        
        # Store curves: {unique_id: (file_path, curve_type, checkbox_var, checkbox_widget, filename)}
        # Use full path as unique identifier to allow multiple files with same basename
        self.curves = {}
        self.last_added_curve = None
        
        # Left panel: Checkboxes for curve selection
        checkbox_frame = ctk.CTkScrollableFrame(self.tab, width=200)
        checkbox_frame.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(10, 5), pady=10)
        checkbox_frame.grid_columnconfigure(0, weight=1)
        
        curves_label = ctk.CTkLabel(
            checkbox_frame,
            text="Curves",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        curves_label.grid(row=0, column=0, pady=(0, 10))
        
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
        
        plot_type_label = ctk.CTkLabel(
            plot_type_frame,
            text="Plot Type:",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        plot_type_label.grid(row=0, column=0, sticky="w", padx=10, pady=5)
        
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
        # Save the plot when user explicitly selects a specialized plot type
        self.save_current_plot_type()
    
    def add_curve(self, file_path, curve_type="Unknown"):
        """Add a new curve to the list. Returns True if added, False if already exists."""
        if not file_path or not os.path.exists(file_path):
            return False
        
        # Use full absolute path as unique identifier to allow multiple files with same basename
        unique_id = os.path.abspath(str(file_path))
        filename = os.path.basename(str(file_path))
        
        # Check if curve already exists (by unique path)
        if unique_id in self.curves:
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
        
        # Store curve data: (file_path, curve_type, checkbox_var, checkbox_widget, filename)
        self.curves[unique_id] = (file_path, curve_type, checkbox_var, checkbox, filename)
        
        # Set as last added and check it (default behavior: show only last curve)
        self.last_added_curve = unique_id
        
        # Uncheck all previous curves and check the new one
        for other_id, (_, _, other_var, _, _) in self.curves.items():
            if other_id != unique_id:
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
        for unique_id, (file_path, curve_type, checkbox_var, _, filename) in self.curves.items():
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
            # If filename is already a full path, use it; otherwise join with TEMP_DIR
            if os.path.isabs(filename):
                plot_path = filename
            else:
                plot_path = os.path.join(TEMP_DIR, filename)
            self.fig.savefig(plot_path, dpi=150, bbox_inches='tight')
        except Exception as e:
            print(f"Error saving plot: {e}")
    
    def save_all_curve_plots(self, curve_path: str):
        """
        Save only the main 1D plot for a curve.
        Specialized plots (Guinier, Kratky, loglog) are saved only when user selects those plot types.
        
        Args:
            curve_path: Path to the curve data file
        """
        if not curve_path or not os.path.exists(curve_path):
            return
        
        try:
            # Save only main 1D curve plot
            plot_filename = generate_curve_plot_filename(
                curve_path,
                "plot_1d",
                ".png",
                base_dir=TEMP_DIR
            )
            self.save_plot(plot_filename)
            
        except Exception as e:
            print(f"Error saving curve plot for {curve_path}: {e}")
            import traceback
            traceback.print_exc()
    
    def save_current_plot_type(self):
        """
        Save the currently selected plot type for all checked curves.
        This is called when user changes plot type in the panel.
        Only saves specialized plots (not "I vs q").
        """
        if not self.plot_type:
            return
        
        # Map plot type names to save identifiers
        # Only save specialized plot types, not the basic "I vs q"
        plot_type_map = {
            "log I vs q": "logI_vs_q",
            "log I vs log q": "loglog",
            "Guinier: log I vs q^2": "guinier",
            "Kratky: q^2 * I vs q": "kratky",
        }
        
        save_type = plot_type_map.get(self.plot_type)
        if not save_type:
            # For "I vs q", don't save automatically
            return
        
        # Save plot for all checked curves
        for unique_id, (file_path, curve_type, checkbox_var, _, filename) in self.curves.items():
            if checkbox_var.get() and os.path.exists(file_path):
                try:
                    q, I, sigma, _ = read_saxs(file_path)
                    q_nm = q * 1e-9  # Convert q from 1/m to 1/nm
                    
                    # Filter out zero/negative values for log plots
                    valid_mask = (I > 0) & (q_nm > 0)
                    q_plot = q_nm[valid_mask]
                    I_plot = I[valid_mask]
                    
                    if len(q_plot) == 0:
                        continue
                    
                    # Generate appropriate data based on plot type
                    if save_type == "guinier":
                        x_data = q_plot ** 2
                        y_data = np.log10(I_plot)
                        xlabel = "q² (nm⁻²)"
                        ylabel = "log₁₀(I)"
                    elif save_type == "kratky":
                        x_data = q_plot
                        y_data = (q_plot ** 2) * I_plot
                        xlabel = "q (nm⁻¹)"
                        ylabel = "q² × I (a.u.)"
                    elif save_type == "loglog":
                        x_data = np.log10(q_plot)
                        y_data = np.log10(I_plot)
                        xlabel = "log₁₀(q / nm⁻¹)"
                        ylabel = "log₁₀(I)"
                    elif save_type == "logI_vs_q":
                        x_data = q_plot
                        y_data = np.log10(I_plot)
                        xlabel = "q (nm⁻¹)"
                        ylabel = "log₁₀(I)"
                    else:
                        continue
                    
                    # Save the plot
                    self._save_single_curve_plot(file_path, x_data, y_data, save_type, xlabel, ylabel)
                    
                except Exception as e:
                    print(f"Error saving {save_type} plot for {filename}: {e}")
    
    def _save_single_curve_plot(self, curve_path: str, x_data, y_data, plot_type: str, 
                                xlabel: str, ylabel: str):
        """
        Save a single curve plot.
        
        Args:
            curve_path: Path to the curve data file
            x_data: X-axis data
            y_data: Y-axis data
            plot_type: Type of plot (e.g., "guinier", "kratky", "loglog")
            xlabel: X-axis label
            ylabel: Y-axis label
        """
        try:
            # Create a new figure for this plot
            fig, ax = plt.subplots(figsize=(10, 6))
            
            # Plot the data
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
            
            ax.scatter(
                x_data, y_data,
                s=marker_size,
                alpha=alpha,
                edgecolors='none',
                marker='o',
                color='#1f77b4'
            )
            
            ax.set_xlabel(xlabel)
            ax.set_ylabel(ylabel)
            ax.set_title(f"{plot_type.capitalize()} Plot: {os.path.basename(curve_path)}")
            ax.grid(True, alpha=0.3)
            
            # Generate filename
            plot_path = generate_curve_plot_filename(curve_path, plot_type, '.png', TEMP_DIR)
            
            # Save plot
            fig.savefig(plot_path, dpi=150, bbox_inches='tight')
            plt.close(fig)
            
        except Exception as e:
            print(f"Error saving {plot_type} plot: {e}")
            import traceback
            traceback.print_exc()

