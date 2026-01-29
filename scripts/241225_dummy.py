#!/usr/bin/env python3
"""
Dummy integration script that processes .tif files and integrates them.

Usage:
    python 241225_dummy.py <tif_file_or_dir> [mask_file]

Arguments:
    tif_file_or_dir: Path to a .tif file or directory containing .tif files (non-recursive)
    mask_file: Optional path to mask file (.npy, .txt, or .msk format)
"""

import sys
import os
import argparse
import glob
import numpy as np
import pandas as pd

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autosaxs.processor import IntegratorExtended
from autosaxs.utils import read_from_tiff, write_saxs


def create_dummy_integrator(mask=None):
    """
    Create a dummy IntegratorExtended object with default parameters.
    
    Args:
        mask: Optional mask array (numpy array or None)
    
    Returns:
        IntegratorExtended object
    """
    # Default detector parameters (Pilatus1M with 100um pixels)
    detector_params = {
        'detector_name': 'Pilatus1M',
        'pixel_size': [0.0001, 0.0001]  # 100um in meters
    }
    
    # Default geometry parameters (typical SAXS setup)
    ai_params = {
        'wavelength': 1.54e-10,  # 1.54 Angstrom (typical Cu K-alpha)
        'dist': 0.5,  # 0.5 meters sample-to-detector distance
        'poni1': 0.05,  # Beam center Y position in meters
        'poni2': 0.05,  # Beam center X position in meters
        'rot1': 0.0,  # Rotation 1
        'rot2': 0.0,  # Rotation 2
        'rot3': 0.0   # Rotation 3
    }
    
    return IntegratorExtended(ai_params=ai_params, detector_params=detector_params, mask=mask)


def process_tif_file(tif_path, integrator, output_dir=None):
    """
    Process a single .tif file: integrate, calculate statistics, and save.
    
    Args:
        tif_path: Path to .tif file
        integrator: IntegratorExtended object
        output_dir: Optional output directory (default: same as input file)
    
    Returns:
        dict: Contains 'sum', 'min', 'max' values
    """
    # Read the image
    print(f"Processing: {tif_path}")
    saxs_2d = read_from_tiff(tif_path)
    
    # Integrate using the integrator
    npt = 1000  # Number of points for integration
    q, I, sigma = integrator.integrate1d(saxs_2d, npt=npt)
    
    # Calculate statistics on the intensity array
    I_sum = np.sum(I)
    I_min = np.min(I)
    I_max = np.max(I)
    
    # Prepare output path
    if output_dir is None:
        output_dir = os.path.dirname(tif_path)
        if output_dir == '':
            output_dir = '.'
    
    base_name = os.path.splitext(os.path.basename(tif_path))[0]
    output_path = os.path.join(output_dir, f"{base_name}_integrated.dat")
    
    # Prepare metadata
    metadata = {
        'source_file': os.path.basename(tif_path),
        'source_path': tif_path,
        'sum': float(I_sum),
        'min': float(I_min),
        'max': float(I_max),
        'npt': npt
    }
    
    # Save the integrated curve with metadata
    write_saxs(output_path, q, I, sigma, metadata)
    print(f"Saved integrated curve to: {output_path}")
    
    return {'sum': I_sum, 'min': I_min, 'max': I_max}


def main():
    parser = argparse.ArgumentParser(
        description='Integrate .tif files using a dummy IntegratorExtended object'
    )
    parser.add_argument(
        'tif_input',
        help='Path to .tif file or directory containing .tif files (non-recursive)'
    )
    parser.add_argument(
        'mask_file',
        nargs='?',
        default=None,
        help='Optional path to mask file (.npy, .txt, or .msk format)'
    )
    
    args = parser.parse_args()
    
    # Check if input is a file or directory
    if os.path.isfile(args.tif_input):
        tif_files = [args.tif_input]
    elif os.path.isdir(args.tif_input):
        # Find all .tif files in the directory (non-recursive)
        tif_files = glob.glob(os.path.join(args.tif_input, '*.tif'))
        tif_files.extend(glob.glob(os.path.join(args.tif_input, '*.TIF')))
        if not tif_files:
            print(f"Error: No .tif files found in directory: {args.tif_input}")
            sys.exit(1)
    else:
        print(f"Error: Input path does not exist: {args.tif_input}")
        sys.exit(1)
    
    # Load mask if provided
    mask = None
    if args.mask_file is not None:
        if not os.path.exists(args.mask_file):
            print(f"Error: Mask file not found: {args.mask_file}")
            sys.exit(1)
        mask = IntegratorExtended.read_mask(args.mask_file)
        print(f"Loaded mask from: {args.mask_file}")
    
    # Create dummy integrator
    integrator = create_dummy_integrator(mask=mask)
    
    # Track if we're processing a directory (for DataFrame creation)
    is_directory = os.path.isdir(args.tif_input)
    results = []
    
    # Process each .tif file
    for tif_file in tif_files:
        try:
            stats = process_tif_file(tif_file, integrator)
            # Print statistics to stdout
            print(f"Statistics for {os.path.basename(tif_file)}:")
            print(f"  sum: {stats['sum']}")
            print(f"  min: {stats['min']}")
            print(f"  max: {stats['max']}")
            print()
            
            # Store results for DataFrame if processing a directory
            if is_directory:
                base_name = os.path.splitext(os.path.basename(tif_file))[0]
                results.append({
                    'filename': base_name,
                    'sum': float(stats['sum']),
                    'min': float(stats['min']),
                    'max': float(stats['max'])
                })
        except Exception as e:
            print(f"Error processing {tif_file}: {e}", file=sys.stderr)
            continue
    
    # Create DataFrame if processing a directory
    if is_directory and results:
        df = pd.DataFrame(results)
        # Save DataFrame to CSV in the same directory
        df_path = os.path.join(args.tif_input, 'integration_summary.csv')
        df.to_csv(df_path, index=False)
        print(f"\nSummary DataFrame saved to: {df_path}")
        print(f"\nDataFrame contents:")
        print(df.to_string(index=False))


if __name__ == '__main__':
    main()

