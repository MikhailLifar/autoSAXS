#!/usr/bin/env python3
"""
Standalone calibration service that runs calibration in a separate process.
This prevents deadlocks by completely isolating calibration from the GUI.

Usage:
    python calibration_service.py <config_json> <output_dir>

Where config_json contains:
    - calibrant_path: Path to calibrant image
    - mask_path: Optional path to mask file
    - config: Calibration configuration dictionary
"""

# Set threading environment variables BEFORE importing NumPy/SciPy/pyFAI
import os
import sys
import json
import yaml
import traceback
import argparse

# Threading environment variable names - comprehensive list
_THREADING_ENV_VARS = [
    'OMP_NUM_THREADS',
    'MKL_NUM_THREADS',
    'NUMEXPR_NUM_THREADS',
    'OPENBLAS_NUM_THREADS',
    'VECLIB_MAXIMUM_THREADS',
    'BLIS_NUM_THREADS',
    'TBB_NUM_THREADS',
    'NUMBA_NUM_THREADS',
]

# Determine optimal thread count for calibration
# Since this runs in a separate process, we can use all available cores
try:
    import multiprocessing
    num_cores = multiprocessing.cpu_count()
    # Use all available cores for maximum performance
    # The GUI runs in a separate process, so this won't cause conflicts
    optimal_threads = str(num_cores)
except (ImportError, AttributeError):
    # Fallback to 4 threads if we can't detect cores
    optimal_threads = '4'

# Set thread counts to use all available cores for better performance
# Process isolation ensures no deadlocks with GUI
for var in _THREADING_ENV_VARS:
    os.environ[var] = optimal_threads

print(f"Calibration service: Using {optimal_threads} threads for computation", file=sys.stderr)

# Now import processing modules
# Add script directory to path to ensure imports work
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from autosaxs.processor import autocalib, IntegratorExtended
from autosaxs.utils import read_from_tiff

# Status file for progress updates
STATUS_FILE = None
OUTPUT_DIR = None


def write_status(message, status_type="info"):
    """Write status message to file for GUI to read."""
    if STATUS_FILE:
        try:
            with open(STATUS_FILE, 'w') as f:
                json.dump({
                    'message': message,
                    'type': status_type,
                    'timestamp': os.path.getmtime(__file__) if os.path.exists(__file__) else 0
                }, f)
        except Exception as e:
            print(f"Error writing status: {e}", file=sys.stderr)


def main():
    global STATUS_FILE, OUTPUT_DIR
    
    parser = argparse.ArgumentParser(description='Standalone calibration service')
    parser.add_argument('config_file', help='Path to JSON config file')
    parser.add_argument('output_dir', help='Directory to save results')
    parser.add_argument('--status-file', help='Path to status file for progress updates')
    
    args = parser.parse_args()
    
    STATUS_FILE = args.status_file
    OUTPUT_DIR = args.output_dir
    
    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Load configuration
    try:
        with open(args.config_file, 'r') as f:
            config_data = json.load(f)
    except Exception as e:
        write_status(f"Error loading config: {str(e)}", "error")
        print(f"Error loading config: {e}", file=sys.stderr)
        sys.exit(1)
    
    calibrant_path = config_data.get('calibrant_path')
    mask_path = config_data.get('mask_path')
    config = config_data.get('config')
    
    if not calibrant_path or not config:
        write_status("Missing required parameters: calibrant_path or config", "error")
        print("Error: Missing required parameters", file=sys.stderr)
        sys.exit(1)
    
    # Run calibration
    try:
        write_status("Loading image", "progress")
        
        write_status("Finding center (this may take a while)", "progress")
        
        # Run autocalib - this is the computationally intensive part
        autocalib_result = autocalib(str(calibrant_path), config, mask_path=mask_path)
        calibrated_params = autocalib_result['refined']
        integrator = autocalib_result['integrator']
        
        write_status("Saving integrator", "progress")
        
        # Save integrator to disk
        integrator_subd = os.path.join(OUTPUT_DIR, 'integrator_params')
        integrator.to_disk(integrator_subd)
        
        # Save calibrated parameters
        result_file = os.path.join(OUTPUT_DIR, 'calibration_result.json')
        result_data = {
            'calibrated_params': calibrated_params,
            'status': 'success'
        }
        with open(result_file, 'w') as f:
            json.dump(result_data, f, indent=2)
        
        write_status("Calibration complete", "success")
        sys.exit(0)
        
    except Exception as e:
        error_msg = f"Calibration failed: {str(e)}"
        write_status(error_msg, "error")
        print(error_msg, file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        
        # Save error to result file
        result_file = os.path.join(OUTPUT_DIR, 'calibration_result.json')
        result_data = {
            'status': 'error',
            'error': str(e)
        }
        try:
            with open(result_file, 'w') as f:
                json.dump(result_data, f, indent=2)
        except:
            pass
        
        sys.exit(1)


if __name__ == "__main__":
    main()

