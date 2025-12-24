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

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from processor import (
    IntegratorExtended, find_center, find_rings, refine, 
    get_interring_dist_px, integrate_2d_to_1d, get_r_beam_px
)
from utils import read_from_tiff, read_saxs, write_saxs
from context import Context
from saxs_controller import Controller
from interface import CLIInterface
from viewer import *


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
    controller = Controller(CLIInterface(), PLTViewer())
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
    
    # Step 9: Prepare data for PCA
    print("\n9. Preparing data for PCA analysis...")
    
    # Check if PCA results already exist
    plots_dir = os.path.join(directory, 'pca_plots')
    pca_results_path = os.path.join(plots_dir, 'pca_results.npz')
    
    # Check if interpolation is needed
    q_values_identical = check_q_values_identical(integrated_data)
    
    if q_values_identical:
        print("   All curves have identical q values, no interpolation needed")
        # Use the q values from the first curve
        q_common = integrated_data[0]['q']
        # Stack all intensity arrays directly
        I_matrix = np.array([data['I'] for data in integrated_data])
    else:
        print("   Curves have different q values, interpolating to common grid...")
        q_common, I_matrix = interpolate_curves_to_common_grid(integrated_data, n_points=1000)
    
    print(f"   Data matrix shape: {I_matrix.shape} (n_curves x n_points)")
    
    # Apply q_range filter if present in config
    if q_range_plot is not None:
        print(f"   Applying q_range filter: [{q_min_plot}, {q_max_plot}] nm⁻¹")
        # Filter q_common and I_matrix
        mask = (q_common >= q_min_plot) & (q_common <= q_max_plot)
        q_common = q_common[mask]
        I_matrix = I_matrix[:, mask]
        print(f"   After filtering: {len(q_common)} points remain")
    
    # Step 10: Perform PCA
    print("\n10. Performing PCA analysis...")
    # Center the data (subtract mean)
    I_mean = np.mean(I_matrix, axis=0)
    I_centered = I_matrix - I_mean
    
    # Perform PCA
    pca = PCA()
    pca.fit(I_centered)
    
    # Get PCA components and scores
    components = pca.components_  # Principal components (eigenvectors)
    scores = pca.transform(I_centered)  # Projections (PCA coefficients)
    explained_variance = pca.explained_variance_ratio_
    
    print(f"   First component explains {explained_variance[0]*100:.2f}% of variance")
    print(f"   Second component explains {explained_variance[1]*100:.2f}% of variance")
    
    # Step 11: Create plots
    print("\n11. Creating plots...")
    os.makedirs(plots_dir, exist_ok=True)
    
    # Plot 1: First PCA component
    plt.figure()
    plt.plot(q_common, components[0], 'b-', linewidth=2, label='1st PCA component')
    plt.xlabel('q (nm⁻¹)')
    plt.ylabel('I (a. u.)')
    plt.title('First PCA Component')
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, 'pca_component_1.png'), dpi=150)
    plt.close()
    print("   Saved: pca_component_1.png")
    
    # Plot 1b: Second PCA component
    plt.figure()
    plt.plot(q_common, components[1], 'r-', linewidth=2, label='2nd PCA component')
    plt.xlabel('q (nm⁻¹)')
    plt.ylabel('I (a. u.)')
    plt.title('Second PCA Component')
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, 'pca_component_2.png'), dpi=150)
    plt.close()
    print("   Saved: pca_component_2.png")
    
    # Plot 1c: Explained variance bar plot
    n_components_to_show = min(10, len(explained_variance))
    plt.figure()
    component_indices = np.arange(1, n_components_to_show + 1)
    bars = plt.bar(component_indices, explained_variance[:n_components_to_show] * 100, 
            color='steelblue', alpha=0.7, edgecolor='black', linewidth=0.5)
    
    # Add text labels on top of bars showing explained variance ratios
    for i, (bar, var_ratio) in enumerate(zip(bars, explained_variance[:n_components_to_show] * 100)):
        height = bar.get_height()
        plt.text(bar.get_x() + bar.get_width() / 2., height,
                f'{var_ratio:.1f}%',
                ha='center', va='bottom', fontsize=9)
    
    plt.xlabel('Principal Component')
    plt.ylabel('Explained Variance (%)')
    plt.title('Explained Variance by Principal Component')
    plt.xticks(component_indices)
    plt.grid(True, alpha=0.3, axis='y')
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, 'pca_explained_variance.png'), dpi=150)
    plt.close()
    print("   Saved: pca_explained_variance.png")
    
    # Plot 2: Curves with lowest and highest 1st PCA coefficient
    idx_lowest = np.argmin(scores[:, 0])
    idx_highest = np.argmax(scores[:, 0])
    
    fig, ax = plt.subplots()
    
    # Apply q_range filter if present
    data_lowest = integrated_data[idx_lowest]
    data_highest = integrated_data[idx_highest]
    
    q_lowest = data_lowest['q']
    I_lowest = data_lowest['I']
    sigma_lowest = data_lowest['sigma']
    q_highest = data_highest['q']
    I_highest = data_highest['I']
    sigma_highest = data_highest['sigma']
    
    if q_range_plot is not None:
        mask_lowest = (q_lowest >= q_min_plot) & (q_lowest <= q_max_plot)
        mask_highest = (q_highest >= q_min_plot) & (q_highest <= q_max_plot)
        q_lowest = q_lowest[mask_lowest]
        I_lowest = I_lowest[mask_lowest]
        sigma_lowest = sigma_lowest[mask_lowest]
        q_highest = q_highest[mask_highest]
        I_highest = I_highest[mask_highest]
        sigma_highest = sigma_highest[mask_highest]
    
    # Plot as scatter with error bars
    ax.errorbar(
        q_lowest, I_lowest, yerr=sigma_lowest,
        fmt='o', markersize=3, alpha=0.6, color='blue',
        capsize=1, capthick=0.5, elinewidth=0.5,
        label=f'Lowest PC1 ({data_lowest["file"]}, PC1={scores[idx_lowest, 0]:.2f})'
    )
    ax.errorbar(
        q_highest, I_highest, yerr=sigma_highest,
        fmt='o', markersize=3, alpha=0.6, color='red',
        capsize=1, capthick=0.5, elinewidth=0.5,
        label=f'Highest PC1 ({data_highest["file"]}, PC1={scores[idx_highest, 0]:.2f})'
    )
    ax.set_xlabel('q (nm⁻¹)')
    ax.set_ylabel('I (a.u.)')
    ax.set_title('Curves with Extreme 1st PCA Coefficients')
    # ax.set_xscale('log')
    # ax.set_yscale('log')
    ax.grid(True, alpha=0.3)
    ax.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, 'pca_extreme_curves.png'), dpi=150)
    plt.close()
    print("   Saved: pca_extreme_curves.png")
    
    # Plot 3: Scatter plot in PC1-PC2 space
    plt.figure()
    scatter = plt.scatter(
        scores[:, 0], scores[:, 1], 
        c=range(len(scores)), cmap='viridis', 
        s=50, alpha=0.6
    )
    plt.xlabel(f'PC1 ({explained_variance[0]*100:.1f}% variance)')
    plt.ylabel(f'PC2 ({explained_variance[1]*100:.1f}% variance)')
    plt.title('All Curves in PC1-PC2 Space')
    plt.grid(True, alpha=0.3)
    plt.colorbar(scatter, label='File index')
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, 'pca_scatter_pc1_pc2.png'), dpi=150)
    plt.close()
    print("   Saved: pca_scatter_pc1_pc2.png")
    
    # Plot 4: All integrated curves colored by 1st PCA coefficient
    fig, ax = plt.subplots()
    
    # Sort by PC1 coefficient for better visualization
    sorted_indices = np.argsort(scores[:, 0])
    pc1_sorted = scores[sorted_indices, 0]
    
    # Create colormap
    cmap = plt.cm.get_cmap('RdYlBu_r')
    norm = mcolors.Normalize(vmin=pc1_sorted.min(), vmax=pc1_sorted.max())
    
    for i, idx in enumerate(sorted_indices):
        color = cmap(norm(pc1_sorted[i]))
        data = integrated_data[idx]
        
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
            fmt='o', markersize=1.5, alpha=0.4, color=color,
            capsize=0.5, capthick=0.3, elinewidth=0.3
        )
    
    ax.set_xlabel('q (nm⁻¹)')
    ax.set_ylabel('I (a.u.)')
    ax.set_title('All Integrated Curves')
    # ax.set_xscale('log')
    # ax.set_yscale('log')
    ax.grid(True, alpha=0.3)
    
    # Add colorbar
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cbar = plt.colorbar(sm, ax=ax)
    cbar.set_label('1st PCA Coefficient', rotation=270, labelpad=20)
    
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, 'pca_all_curves_colored.png'), dpi=150)
    plt.close()
    print("   Saved: pca_all_curves_colored.png")
    
    # Save PCA results to file
    pca_results = {
        'q_common': q_common,
        'mean_curve': I_mean,
        'components': components[:10],  # Save first 10 components
        'scores': scores,
        'explained_variance': explained_variance[:10],
        'file_names': [d['file'] for d in integrated_data]
    }
    
    np.savez(
        os.path.join(plots_dir, 'pca_results.npz'),
        **{k: (v if isinstance(v, np.ndarray) else np.array(v)) for k, v in pca_results.items()}
    )
    
    # Print summary (using loaded or calculated data)
    
    print("\n" + "="*60)
    print("PCA Analysis Complete!")
    print("="*60)
    print(f"Results saved in: {plots_dir}")
    print(f"Integrated files saved in: {output_dir}")
    print(f"\nSummary:")
    print(f"  - Files processed: {len(integrated_data)}")
    print(f"  - PC1 variance explained: {explained_variance[0]*100:.2f}%")
    print(f"  - PC2 variance explained: {explained_variance[1]*100:.2f}%")
    print(f"  - Total variance explained (first 2 components): {(explained_variance[0]+explained_variance[1])*100:.2f}%")


if __name__ == '__main__':
    main()

