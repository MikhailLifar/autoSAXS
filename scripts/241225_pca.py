#!/usr/bin/env python3
"""
PCA analysis script for SAXS data.

Usage:
    python 241225_pca.py <directory>

Arguments:
    directory: Path to directory containing .tif files
"""

import sys
import os
import re
import glob
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from sklearn.decomposition import PCA
import subprocess

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autosaxs.processor import (
    IntegratorExtended, find_center, find_rings, refine,
    get_interring_dist_px, integrate_2d_to_1d, get_r_beam_px
)
from autosaxs.utils import read_from_tiff, read_saxs, write_saxs
from autosaxs.context import Context
from autosaxs.event_bus import EventBus
from autosaxs import cli_interface
from autosaxs.saxs_controller import Controller
from autosaxs.viewer import *


def find_calibration_file(directory):
    """Find calibration file matching AgBh pattern."""
    pattern = r".*AgBh\d{2,4}.*\.tif"
    tif_files = glob.glob(os.path.join(directory, "*.tif"))
    tif_files.extend(glob.glob(os.path.join(directory, "*.TIF")))
    
    for tif_file in tif_files:
        if re.match(pattern, os.path.basename(tif_file), re.IGNORECASE):
            return tif_file
    
    raise FileNotFoundError(
        f"No calibration file matching pattern '.*AgBh\\d{{2,4}}.*\\.tif' found in {directory}"
    )


def find_mask_file(directory):
    """Find mask file in directory."""
    mask_files = glob.glob(os.path.join(directory, "*.msk"))
    assert len(mask_files) < 2
    if mask_files:
        return mask_files[0]
    return None


def longest_common_substring(strings):
    """
    Find the longest common substring among a list of strings.
    
    Args:
        strings: List of strings
    
    Returns:
        str: Longest common substring
    """
    if not strings:
        return ""
    if len(strings) == 1:
        return strings[0]
    
    # Use the first string as reference
    reference = strings[0]
    longest = ""
    
    # Try all possible substrings of the reference string
    for i in range(len(reference)):
        for j in range(i + 1, len(reference) + 1):
            substring = reference[i:j]
            # Check if this substring appears in all other strings
            if all(substring in s for s in strings[1:]):
                if len(substring) > len(longest):
                    longest = substring
    
    return longest


def check_q_values_identical(integrated_data, rtol=1e-5):
    """
    Check if all integrated curves have identical q values.
    
    Args:
        integrated_data: List of dicts with 'q' arrays
        rtol: Relative tolerance for comparison
    
    Returns:
        bool: True if all q values are identical, False otherwise
    """
    if len(integrated_data) <= 1:
        return True
    
    q_first = integrated_data[0]['q']
    for data in integrated_data[1:]:
        q_current = data['q']
        if len(q_first) != len(q_current):
            return False
        if not np.allclose(q_first, q_current, rtol=rtol):
            return False
    
    return True


def interpolate_curves_to_common_grid(integrated_data, n_points=1000):
    """
    Interpolate all curves to a common q-grid.
    
    Args:
        integrated_data: List of dicts with 'q', 'I', and 'sigma' arrays
        n_points: Number of points in the common q-grid
    
    Returns:
        tuple: (q_common, I_matrix) where:
            q_common: Common q-grid array
            I_matrix: 2D array (n_curves x n_points) with interpolated intensities
    """
    # Find common q range
    q_min = max(d['q'].min() for d in integrated_data)
    q_max = min(d['q'].max() for d in integrated_data)
    q_common = np.linspace(q_min, q_max, n_points)
    
    # Interpolate all curves to common q-grid
    I_matrix = []
    for data in integrated_data:
        I_interp = np.interp(q_common, data['q'], data['I'])
        I_matrix.append(I_interp)
    
    I_matrix = np.array(I_matrix)
    return q_common, I_matrix


def main():
    parser = argparse.ArgumentParser(
        description='Perform PCA analysis on SAXS integrated curves'
    )
    parser.add_argument(
        'directory',
        help='Directory containing .tif files'
    )
    parser.add_argument(
        '--force', '-f',
        action='store_true',
        default=False,
        help='Force recalculation and overwrite existing results (default: False, skip if results exist)'
    )
    parser.add_argument(
        '--no-int-plots',
        action='store_true',
        default=False,
        help='Skip plotting individual integrated datasets (faster execution)'
    )
    
    args = parser.parse_args()
    directory = os.path.abspath(args.directory)
    
    if not os.path.isdir(directory):
        print(f"Error: Directory does not exist: {directory}", file=sys.stderr)
        sys.exit(1)
    
    print(f"Processing directory: {directory}")
    
    # Step 1: Find calibration file
    print("\n1. Searching for calibration file...")
    calibrant_path = find_calibration_file(directory)
    print(f"   Found calibration file: {os.path.basename(calibrant_path)}")
    
    # Step 2: Find mask file
    print("\n2. Searching for mask file...")
    mask_path = find_mask_file(directory)
    if mask_path:
        print(f"   Found mask file: {os.path.basename(mask_path)}")
    else:
        print("   No mask file found, proceeding without mask")
    
    # Step 3: Set up context and config
    print("\n3. Setting up configuration...")
    config_path = os.path.join(directory, 'config.conf')
    if not os.path.exists(config_path):
        print(f"Error: config.conf not found in {directory}", file=sys.stderr)
        sys.exit(1)
    
    context = Context()
    context.set_directory(directory)
    context.set_config(config_path)
    
    # Update mask config if mask file is found
    if mask_path:
        context.update_config('mask_config', values={'mode': 'combined'})
    else:
        context.update_config('mask_config', values={'mode': 'auto'})
    
    # Check for q_range in config for plotting and analysis
    q_range_plot = None
    q_min_plot = None
    q_max_plot = None
    try:
        q_range_plot = context['q_range']
        if q_range_plot is not None and isinstance(q_range_plot, (list, tuple)) and len(q_range_plot) == 2:
            q_min_plot, q_max_plot = float(q_range_plot[0]), float(q_range_plot[1])
            print(f"   Found q_range in config: [{q_min_plot}, {q_max_plot}] nm⁻¹")
        else:
            q_range_plot = None
    except (KeyError, TypeError):
        pass
    
    # Step 4: Estimate r_beam_px from calibration image
    print("\n4. Estimating beam-stop radius...")
    calib_data = read_from_tiff(calibrant_path)
    # First find center to estimate beam radius
    center_ref_params = {
        k: context['center_refinement', k] 
        for k in ['q_start', 'q_stop', 'min_segment_len']
    }
    center_result = find_center(calib_data, **center_ref_params)
    estimated_r_beam_px = get_r_beam_px(
        calib_data, 
        center_result['center_y_px'], 
        center_result['center_x_px']
    )
    
    if estimated_r_beam_px is not None:
        print(f"   Estimated r_beam_px: {estimated_r_beam_px:.1f} pixels")
        # Update context with estimated r_beam_px
        context['r_beam_px'] = estimated_r_beam_px
    else:
        print("   Could not estimate r_beam_px, using value from config")
        # Use value from config if estimation failed
        if 'r_beam_px' not in context.config:
            print("   Warning: r_beam_px not found in config, using default value of 35", file=sys.stderr)
            context['r_beam_px'] = 35
    
    # Step 5: Perform autocalibration
    print("\n5. Performing autocalibration...")
    event_bus = EventBus()
    cli_interface.connect(event_bus)
    controller = Controller(event_bus, PLTViewer())
    # Use fast_forward mode if force=False (skip if results exist)
    calib_result = controller.autocalib(
        calibrant_path, mask_path, context=context, fast_forward=not args.force
    )
    integrator = calib_result['integrator']
    
    if integrator is None:
        print("Error: Calibration failed", file=sys.stderr)
        sys.exit(1)
    
    print("   Calibration completed successfully")
    
    # Step 6: Find all .tif files (except calibration file)
    print("\n6. Finding all .tif files for integration...")
    all_tif_files = glob.glob(os.path.join(directory, "*.tif"))
    all_tif_files.extend(glob.glob(os.path.join(directory, "*.TIF")))
    
    # Exclude calibration file
    tif_files_to_integrate = [
        f for f in all_tif_files 
        if os.path.abspath(f) != os.path.abspath(calibrant_path)
    ]
    
    print(f"   Found {len(tif_files_to_integrate)} files to integrate")
    
    # Step 7: Integrate all files
    print("\n7. Integrating files...")
    integrated_files = []
    integrated_data = []
    
    output_dir = os.path.join(directory, 'integrated')
    os.makedirs(output_dir, exist_ok=True)
    
    # Per-file fast-forward check: integrate only files that don't have results yet
    for tif_file in tif_files_to_integrate:
        base_name = os.path.splitext(os.path.basename(tif_file))[0]
        output_path = os.path.join(output_dir, f"{base_name}_integrated.dat")
        
        # Check if this file's integration result already exists (unless force=True)
        should_skip = not args.force and os.path.exists(output_path)
        
        if should_skip:
            # Load existing integrated data
            try:
                q, I, sigma, _ = read_saxs(output_path)  # Returns (wavenumber, intensity, sigma, metadata)
                integrated_files.append(output_path)
                integrated_data.append({
                    'file': base_name,
                    'path': output_path,
                    'q': q,
                    'I': I,
                    'sigma': sigma
                })
                print(f"   Skipped (exists): {base_name}")
                continue  # Skip to next file
            except Exception as e:
                print(f"   Error loading {base_name}, will re-integrate: {e}", file=sys.stderr)
                should_skip = False
        
        if not should_skip:
            # Integrate this file
            try:
                saxs_2d = read_from_tiff(tif_file)
                metadata = {'source_file': os.path.basename(tif_file)}
                q, I, sigma = integrate_2d_to_1d(
                    integrator, saxs_2d, npt=1000, destpath=output_path, metadata=metadata
                )
                
                integrated_files.append(output_path)
                integrated_data.append({
                    'file': base_name,
                    'path': output_path,
                    'q': q,
                    'I': I,
                    'sigma': sigma
                })
                print(f"   Integrated: {base_name}")
            except Exception as e:
                print(f"   Error integrating {os.path.basename(tif_file)}: {e}", file=sys.stderr)
                continue
    
    if len(integrated_data) == 0:
        print("Error: No files were successfully integrated", file=sys.stderr)
        sys.exit(1)
    
    print(f"   Successfully integrated {len(integrated_data)} files")
    
    # Step 8: Plot individual integrated curves (per-file fast-forward check)
    if args.no_int_plots:
        print("\n8. Plotting individual integrated curves...")
        print("   Skipped (--no-int-plots flag set)")
    else:
        print("\n8. Plotting individual integrated curves...")
        individual_plots_dir = os.path.join(directory, 'individual_curves')
        os.makedirs(individual_plots_dir, exist_ok=True)
        
        for i, data in enumerate(integrated_data):
            plot_path = os.path.join(individual_plots_dir, f"{data['file']}_integrated.png")
            
            # Check if this plot already exists (unless force=True)
            should_skip = not args.force and os.path.exists(plot_path)
            
            if should_skip:
                print(f"   Skipped (exists): {data['file']}_integrated.png")
                continue
            
            fig, ax = plt.subplots()
            
            # Apply q_range filter if present
            q_plot = data['q']
            I_plot = data['I']
            sigma_plot = data['sigma']
            if q_range_plot is not None:
                mask = (q_plot >= q_min_plot) & (q_plot <= q_max_plot)
                q_plot = q_plot[mask]
                I_plot = I_plot[mask]
                sigma_plot = sigma_plot[mask]
            
            # Plot as scatter with error bars
            ax.errorbar(
                q_plot, I_plot, yerr=sigma_plot,
                fmt='o', markersize=2, alpha=0.6, 
                capsize=1, capthick=0.5, elinewidth=0.5,
                label='integrated saxs 1d'
            )
            ax.set_xlabel('q (nm⁻¹)')
            ax.set_ylabel('I (a.u.)')
            ax.set_title(f'File: {data["file"]}')
            # ax.set_xscale('log')
            # ax.set_yscale('log')
            ax.grid(True, alpha=0.3)
            ax.legend()
            plt.tight_layout()
            plt.savefig(plot_path, dpi=150)
            plt.close()
            print(f"   Plotted: {data['file']}_integrated.png")
        
        print(f"   Individual curve plots saved to {individual_plots_dir}")
    
    # Step 8b: Export all integrated curves to CSV
    print("\n8b. Exporting all integrated curves to CSV...")
    
    # Get base names of all integrated files (without extension)
    base_names = [data['file'] for data in integrated_data]
    
    # Find longest common substring
    common_substring = longest_common_substring(base_names)
    
    # Strip '_' from beginning and end
    common_substring = common_substring.strip('_')
    
    # Generate CSV filename
    if len(common_substring) >= 3:
        csv_filename = f"{common_substring}_curves.csv"
    else:
        csv_filename = "curves.csv"
    
    # Save CSV to main directory (not integrated subdirectory)
    csv_path = os.path.join(directory, csv_filename)
    
    # Prepare data for CSV export
    # Check if all curves have identical q values
    q_values_identical = check_q_values_identical(integrated_data)
    
    if q_values_identical:
        # Use the q values from the first curve
        q_csv = integrated_data[0]['q']
        # Create DataFrame with q column and I/sigma columns for each file
        csv_data = {'q': q_csv}
        for data in integrated_data:
            csv_data[f"{data['file']}_I"] = data['I']
            csv_data[f"{data['file']}_sigma"] = data['sigma']
    else:
        # Interpolate to common q grid for CSV
        q_min = max(d['q'].min() for d in integrated_data)
        q_max = min(d['q'].max() for d in integrated_data)
        q_csv = np.linspace(q_min, q_max, 1000)
        
        csv_data = {'q': q_csv}
        for data in integrated_data:
            I_interp = np.interp(q_csv, data['q'], data['I'])
            sigma_interp = np.interp(q_csv, data['q'], data['sigma'])
            csv_data[f"{data['file']}_I"] = I_interp
            csv_data[f"{data['file']}_sigma"] = sigma_interp
    
    # Create DataFrame and save to CSV
    df_csv = pd.DataFrame(csv_data)
    df_csv.to_csv(csv_path, index=False)
    print(f"   Saved CSV file: {csv_filename}")
    print(f"   CSV contains {len(df_csv)} rows and {len(df_csv.columns)} columns")

    # Step 9: PCA (delegated to reusable pca_to_saxs_1d.py)
    print("\n9. Performing PCA analysis (1D curves)...")
    plots_dir = os.path.join(directory, "pca_plots")
    os.makedirs(plots_dir, exist_ok=True)
    pca_script = os.path.join(os.path.dirname(__file__), "pca_to_saxs_1d.py")
    cmd = [
        sys.executable,
        pca_script,
        "--input-dir",
        output_dir,
        "--output-dir",
        plots_dir,
        "--glob",
        "*_integrated.dat",
    ]
    if q_range_plot is not None:
        cmd += ["--q-min", str(q_min_plot), "--q-max", str(q_max_plot)]
    subprocess.run(cmd, check=True)

    print("\n" + "=" * 60)
    print("PCA Analysis Complete!")
    print("=" * 60)
    print(f"Results saved in: {plots_dir}")
    print(f"Integrated files saved in: {output_dir}")


if __name__ == '__main__':
    main()

