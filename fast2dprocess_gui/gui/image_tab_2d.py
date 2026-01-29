"""2D image display tab for SAXS images."""
import os
import traceback
import customtkinter as ctk
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from scipy.ndimage import zoom
from ..core.constants import TEMP_DIR
from ..utils.filename_utils import generate_filename
from autosaxs.processor import IntegratorExtended
from autosaxs.utils import read_from_tiff


class ImageTab2D:
    """Tab for displaying 2D SAXS images."""
    
    def __init__(self, parent):
        """
        Initialize the 2D image tab.
        
        Args:
            parent: Parent tabview or frame
        """
        self.tab = parent
        self.tab.grid_columnconfigure(0, weight=0)  # Thumbnail panel (fixed width)
        self.tab.grid_columnconfigure(1, weight=1)  # Main display (flexible)
        self.tab.grid_rowconfigure(0, weight=1)
        
        # Store image data: {unique_id (file path): (image_path, thumbnail_widget, image_type, filename)}
        self.image_data = {}
        self.selected_image = None  # Stores unique_id of selected image
        
        # Thumbnail panel (left side)
        self.thumbnail_frame = ctk.CTkScrollableFrame(self.tab, width=150)
        self.thumbnail_frame.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=10)
        self.thumbnail_frame.grid_columnconfigure(0, weight=1)
        
        # Thumbnail title label
        title_label = ctk.CTkLabel(
            self.thumbnail_frame,
            text="Images",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        title_label.grid(row=0, column=0, pady=(0, 10))
        
        # Main display area (right side)
        display_frame = ctk.CTkFrame(self.tab)
        display_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=10)
        display_frame.grid_columnconfigure(0, weight=1)
        display_frame.grid_rowconfigure(0, weight=1)
        
        # Create figure for 2D images
        self.fig = Figure(figsize=(10, 6))
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title("2D SAXS Images")
        self.ax.set_xlabel("X (pixels)")
        self.ax.set_ylabel("Y (pixels)")
        
        self.canvas = FigureCanvasTkAgg(self.fig, master=display_frame)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
    
    def _create_thumbnail(self, image_data, max_size=120):
        """Create a thumbnail from image data using efficient resizing."""
        try:
            h, w = image_data.shape
            scale = min(max_size / w, max_size / h, 1.0)
            
            if scale < 1.0:
                new_h, new_w = int(h * scale), int(w * scale)
                # Use zoom for efficient downsampling
                thumbnail = zoom(image_data, (new_h / h, new_w / w), order=1, prefilter=False)
            else:
                thumbnail = image_data
            
            return thumbnail
        except Exception as e:
            print(f"Error creating thumbnail: {e}")
            return None
    
    def _create_thumbnail_widget(self, thumbnail_data, image_type, filename, image_path, unique_id):
        """Create a clickable thumbnail widget."""
        if thumbnail_data is None:
            return None
        
        # Create container frame for thumbnail
        thumb_container = ctk.CTkFrame(self.thumbnail_frame)
        thumb_container.grid_columnconfigure(0, weight=1)
        
        # Create small figure for thumbnail
        thumb_fig = Figure(figsize=(1.2, 1.2), dpi=100)
        thumb_ax = thumb_fig.add_subplot(111)
        thumb_ax.axis('off')
        
        # Display thumbnail with log scale
        thumb_ax.imshow(
            np.log1p(thumbnail_data),
            cmap='viridis',
            origin='lower',
            aspect='auto'
        )
        
        thumb_canvas = FigureCanvasTkAgg(thumb_fig, master=thumb_container)
        thumb_canvas.get_tk_widget().configure(width=120, height=120)
        thumb_canvas.draw()
        
        # Create label with image type and filename
        display_text = f"{image_type}\n{filename}"
        label = ctk.CTkLabel(
            thumb_container,
            text=display_text,
            font=ctk.CTkFont(size=9),
            wraplength=120,
            justify="center"
        )
        
        # Pack elements
        thumb_canvas.get_tk_widget().pack(pady=(0, 5))
        label.pack(pady=(0, 5))
        
        # Make clickable
        def on_click(event):
            self._select_image(unique_id, image_path, image_type)
        
        thumb_canvas.get_tk_widget().bind("<Button-1>", on_click)
        label.bind("<Button-1>", on_click)
        thumb_container.bind("<Button-1>", on_click)
        
        # Store reference using setattr to avoid linting errors
        setattr(thumb_container, 'thumb_canvas', thumb_canvas)
        setattr(thumb_container, 'thumb_fig', thumb_fig)
        setattr(thumb_container, 'unique_id', unique_id)
        
        return thumb_container
    
    def _select_image(self, unique_id, image_path, image_type):
        """Select and display an image when thumbnail is clicked."""
        self.selected_image = unique_id
        filename = os.path.basename(str(image_path))
        display_title = f"{image_type}: {filename}"
        self.display_image(image_path, display_title)
        
        # Update thumbnail highlighting (optional visual feedback)
        for child in self.thumbnail_frame.winfo_children():
            if isinstance(child, ctk.CTkFrame) and hasattr(child, 'unique_id'):
                child_id = getattr(child, 'unique_id', None)
                if child_id == unique_id:
                    child.configure(fg_color=("gray75", "gray35"))
                else:
                    child.configure(fg_color=("gray90", "gray20"))
    
    def add_image_thumbnail(self, image_path, image_type):
        """Add a thumbnail for an image. Uses file path as unique identifier."""
        if not image_path or not os.path.exists(image_path):
            return
        
        # Use normalized absolute path as unique identifier
        unique_id = os.path.abspath(str(image_path))
        filename = os.path.basename(str(image_path))
        
        # Check if this image already exists
        if unique_id in self.image_data:
            # Image already exists, just select it
            self._select_image(unique_id, image_path, image_type)
            return
        
        try:
            # Read image data - try read_from_tiff first, then fallback to fabio for masks
            img_data = None
            try:
                img_data = read_from_tiff(image_path)
            except Exception:
                # Try fabio for mask files or other formats
                try:
                    import fabio
                    img_data = fabio.open(image_path).data
                except Exception:
                    # Try IntegratorExtended.read_mask as last resort
                    try:
                        img_data = IntegratorExtended.read_mask(image_path)
                        # Convert boolean mask to numeric for display
                        if img_data.dtype == bool:
                            img_data = img_data.astype(np.float32)
                    except Exception as e2:
                        print(f"Could not read {image_type} image {filename}: {e2}")
                        return
            
            if img_data is None:
                return
            
            # Create thumbnail
            thumbnail_data = self._create_thumbnail(img_data)
            if thumbnail_data is None:
                return
            
            # Create new thumbnail widget
            thumb_widget = self._create_thumbnail_widget(thumbnail_data, image_type, filename, image_path, unique_id)
            if thumb_widget:
                # Find next available row (skip title label at row 0)
                row = len(self.image_data) + 1
                thumb_widget.grid(row=row, column=0, sticky="ew", pady=5)
                
                # Store reference using unique_id as key
                self.image_data[unique_id] = (image_path, thumb_widget, image_type, filename)
                
                # If this is the first image, display it
                if self.selected_image is None:
                    self._select_image(unique_id, image_path, image_type)
        except Exception as e:
            print(f"Error adding thumbnail for {image_type} {filename}: {str(e)}")
            traceback.print_exc()
    
    def display_image(self, image_path, title):
        """Display a 2D image."""
        self.ax.clear()
        
        try:
            # Read image data - try read_from_tiff first, then fallback to fabio for masks
            img_data = None
            try:
                img_data = read_from_tiff(image_path)
            except Exception:
                # Try fabio for mask files or other formats
                try:
                    import fabio
                    img_data = fabio.open(image_path).data
                except Exception:
                    # Try IntegratorExtended.read_mask as last resort
                    try:
                        img_data = IntegratorExtended.read_mask(image_path)
                        # Convert boolean mask to numeric for display
                        if img_data.dtype == bool:
                            img_data = img_data.astype(np.float32)
                    except Exception as e2:
                        print(f"Could not read {title} image: {e2}")
                        return
            
            if img_data is None:
                return
            
            # Display image with log scale
            im = self.ax.imshow(
                np.log1p(img_data), 
                cmap='viridis', 
                origin='lower'
            )
            
            self.ax.set_title(f"2D Image: {title}")
            self.ax.set_xlabel("X (pixels)")
            self.ax.set_ylabel("Y (pixels)")
            
            self.canvas.draw()
        except Exception as e:
            print(f"Error displaying image: {str(e)}")
            traceback.print_exc()
    
    def save_plot(self, filename):
        """
        Save figure to temp directory.
        
        Args:
            filename: Filename (relative or absolute path)
        """
        try:
            # If filename is already a full path, use it; otherwise join with TEMP_DIR
            if os.path.isabs(filename):
                plot_path = filename
            else:
                plot_path = os.path.join(TEMP_DIR, filename)
            self.fig.savefig(plot_path, dpi=150, bbox_inches='tight')
        except Exception as e:
            print(f"Error saving plot: {e}")
    
    def save_calibrant_plot(self, image_path: str):
        """
        Save calibrant plot with descriptive filename.
        
        Args:
            image_path: Path to the original image file
        """
        plot_filename = generate_filename(
            image_path,
            "calibrant",
            ".png",
            additional_info="2d",
            base_dir=TEMP_DIR
        )
        self.save_plot(plot_filename)
    
    def save_image_plot(self, image_path: str, image_type: str):
        """
        Save image plot with descriptive filename.
        
        Args:
            image_path: Path to the original image file
            image_type: Type of image (e.g., "buffer", "sample")
        """
        plot_filename = generate_filename(
            image_path,
            image_type,
            ".png",
            additional_info="2d",
            base_dir=TEMP_DIR
        )
        self.save_plot(plot_filename)

