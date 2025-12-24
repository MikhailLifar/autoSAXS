"""Utility functions for generating descriptive filenames."""
import os
from typing import Optional


def generate_filename(
    original_path: str,
    operation: str,
    extension: str,
    additional_info: Optional[str] = None,
    base_dir: Optional[str] = None
) -> str:
    """
    Generate a descriptive filename based on original name and operation.
    
    Args:
        original_path: Path to the original file
        operation: Operation performed (e.g., 'int', 'subtracted', 'calibrant', 'buffer', 'sample', 'guinier', 'kratky', 'loglog')
        extension: File extension (e.g., '.dat', '.tif', '.png')
        additional_info: Optional additional information to include in filename
        base_dir: Optional base directory for output path
    
    Returns:
        Full path to the output file
    
    Examples:
        generate_filename('sample_001.tif', 'int', '.dat')
        -> 'int_sample_001.dat'
        
        generate_filename('buffer_001.tif', 'buffer', '.tif')
        -> 'buffer_buffer_001.tif'
        
        generate_filename('int_sample_001.dat', 'guinier', '.png', base_dir='/tmp')
        -> '/tmp/guinier_int_sample_001.png'
    """
    # Get basename without extension
    original_basename = os.path.splitext(os.path.basename(str(original_path)))[0]
    
    # Build filename components
    parts = [operation]
    if additional_info:
        parts.append(additional_info)
    parts.append(original_basename)
    
    # Join parts with underscores
    filename = "_".join(parts) + extension
    
    # Add base directory if provided
    if base_dir:
        return os.path.join(base_dir, filename)
    
    return filename


def generate_curve_plot_filename(
    curve_path: str,
    plot_type: str,
    extension: str = '.png',
    base_dir: Optional[str] = None
) -> str:
    """
    Generate filename for curve plots.
    
    Args:
        curve_path: Path to the curve data file
        plot_type: Type of plot (e.g., 'guinier', 'kratky', 'loglog', 'plot_1d', 'plot_2d')
        extension: File extension (default: '.png')
        base_dir: Optional base directory for output path
    
    Returns:
        Full path to the plot file
    """
    return generate_filename(curve_path, plot_type, extension, base_dir=base_dir)


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to remove invalid characters.
    
    Args:
        filename: Original filename
    
    Returns:
        Sanitized filename safe for filesystem
    """
    # Replace invalid characters with underscores
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '_')
    return filename

