"""GUI components for fast2dprocess_gui."""
from .main_window import SAXSProcessorGUI
from .control_panel import ControlPanel
from .image_tab_2d import ImageTab2D
from .curves_tab_1d import CurvesTab1D
from .widgets import center_window

__all__ = [
    'SAXSProcessorGUI',
    'ControlPanel',
    'ImageTab2D',
    'CurvesTab1D',
    'center_window',
]

