'''
This module provides a command-line interface (CLI) for calibrating experimental data.

The CLI allows users to:
- Specify a calibrant (e.g., "AgBh").
- Provide a path to a TIFF image for calibration.
- Interactively set calibration and ring search parameters.
- Calibrate the data using the provided parameters.
- Visualize the calibrated curve and the ideal calibrant curve.
- Repeat the calibration process with different parameters until the user is satisfied.

The module separates core functionality from the user interface, making it easier to adapt to a graphical user interface (GUI) in the future.
'''

import os
import yaml
import logging

import numpy as np

from pyFAI.io import image
import matplotlib.pyplot as plt
from calibrator import Calibrator

CONFIG_FILE = "calib_config.conf"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='calibration_app.log',
    filemode='w'
)

def get_user_input(prompt, default=None, type_caster=str):
    '''Generic function to get user input with a default value and type casting.'''
    prompt_text = prompt
    if default is not None:
        prompt_text += f" (default: {default})"
    prompt_text += ": "

    while True:
        try:
            user_input = input(prompt_text)
            if not user_input and default is not None:
                value = default
            else:
                value = type_caster(user_input)
            
            print(f"-> {prompt.split(' (')[0]} set to: {value}")
            return value
        except (ValueError, TypeError):
            error_msg = f"Invalid input for '{prompt}'. Expected type {type_caster.__name__}."
            print(error_msg)

class CalibrationApp:
    '''Manages the calibration workflow and user interaction.'''

    def __init__(self, calibrator):
        '''Initializes the application with a Calibrator object.'''
        self.calibrator = calibrator
        self.config = self.load_config()

    def load_config(self):
        '''Loads the configuration from the YAML file, or creates it if it doesn't exist.'''
        if not os.path.exists(CONFIG_FILE):
            logging.info(f"'{CONFIG_FILE}' not found. Creating a default config file.")
            return self.create_default_config()
        
        logging.info(f"Loading configuration from '{CONFIG_FILE}'.")
        with open(CONFIG_FILE, 'r') as f:
            return yaml.safe_load(f)

    def create_default_config(self):
        '''Creates a default configuration file.'''
        default_config = {
            'ring_search': {
                'q_start': 0.95,
                'q_stop': 0.995,
                'min_segment_len': 50,
                'I_threshold': 80.0,
                'r_max': 700,
                'r_step': 3,
                'peak_width': 60
            },
            'detector_geometry': {
                'dist': None,
                'wavelength': None,
                'pixel_size': [None, None], # Stored as a list in YAML
                'beam_center_x': None,
                'beam_center_y': None
            }
        }
        with open(CONFIG_FILE, 'w') as f:
            yaml.dump(default_config, f)
        return default_config

    def save_config(self):
        '''Saves the current configuration to the YAML file.'''
        with open(CONFIG_FILE, 'w') as f:
            yaml.dump(self.config, f)

    def load_data(self, image_path):
        '''Loads TIFF image data into the Calibrator object.'''
        self.calibrator.data = image.read_image_data(image_path)
        print(f"Successfully loaded data from {image_path}")

    def get_calibration_parameters(self):
        '''Prompts the user for calibration parameters.'''
        print("\n--- Enter Calibration Parameters ---")
        params = self.config['detector_geometry']
        
        px_size = params.get('pixel_size', [None, None])
        all_params_set = all(v is not None for k, v in params.items() if k != 'pixel_size') and all(v is not None for v in px_size)

        use_defaults = False
        if all_params_set:
            if get_user_input("Use default detector geometry parameters? (yes/no)", default="yes", type_caster=str).lower() == 'yes':
                use_defaults = True

        if use_defaults:
            dist = params['dist']
            wavelength = params['wavelength']
            pixel_size_y, pixel_size_x = params['pixel_size']
            beam_center_y = params['beam_center_y']
            beam_center_x = params['beam_center_x']
            print("Using default detector geometry parameters.")
        else:
            dist = get_user_input("Distance (in mm)", params.get('dist'), float)
            wavelength = get_user_input("Wavelength (in A)", params.get('wavelength'), float)
            
            px_defaults = params.get('pixel_size', [None, None])
            pixel_size_x = get_user_input("Pixel size Y (in um)", px_defaults[0], float)
            pixel_size_y = get_user_input("Pixel size X (in um)", px_defaults[1], float)
            
            center_auto = get_user_input("Find center coordinates automatically (yes/no)", 'yes', str)
            if center_auto == 'yes':
                beam_center_x = beam_center_y = None
            else:
                beam_center_y = get_user_input("Beam center Y (in pixels)", params.get('beam_center_x'), float)
                beam_center_x = get_user_input("Beam center X (in pixels)", params.get('beam_center_y'), float)

        self.config['detector_geometry'] = {
            'dist': dist,
            'wavelength': wavelength,
            'pixel_size': [pixel_size_y, pixel_size_x],
            'beam_center_y': beam_center_y,
            'beam_center_x': beam_center_x
        }
        self.save_config()

        self.calibrator.set_initial_point(
            dist=dist * 1.e-3,
            wavelength=wavelength * 1.e-10,
            pixel_size=[pixel_size_y * 1.e-6, pixel_size_x * 1.e-6],
            beam_center_y=beam_center_y,
            beam_center_x=beam_center_x
        )

    def get_ring_search_parameters(self):
        '''Prompts the user for ring search parameters or uses defaults.'''
        params = self.config['ring_search']
        
        if get_user_input("\nUse default ring search parameters? (yes/no)", default="yes", type_caster=str).lower() == 'yes':
            self.calibrator.set_ring_search(**params)
            print("Using default ring search parameters.")
            return

        print("\n--- Enter Ring Search Parameters ---")
        q_start = get_user_input("q_start", params.get('q_start'), float)
        q_stop = get_user_input("q_stop", params.get('q_stop'), float)
        min_segment_len = get_user_input("min_segment_len", params.get('min_segment_len'), int)
        i_threshold = get_user_input("I_threshold", params.get('I_threshold'), float)
        r_max = get_user_input("r_max", params.get('r_max'), int)
        r_step = get_user_input("r_step", params.get('r_step'), int)
        peak_width = get_user_input("peak_width", params.get('peak_width'), int)

        self.config['ring_search'] = {
            'q_start': q_start,
            'q_stop': q_stop,
            'min_segment_len': min_segment_len,
            'I_threshold': i_threshold,
            'r_max': r_max,
            'r_step': r_step,
            'peak_width': peak_width
        }
        self.save_config()

        self.calibrator.set_ring_search(**self.config['ring_search'])

    def run_calibration(self, calibrant_name):
        '''Runs the calibration and returns the results.'''
        logging.info(f"Starting calibration with calibrant: {calibrant_name}")
        return self.calibrator.refine(calibrant_name=calibrant_name)

    def visualize_results(self, calibrated_curve, q_theor):
        '''Visualizes the calibration results.'''
        if not calibrated_curve or q_theor is None:
            logging.warning("Cannot visualize results, data is missing.")
            return

        q_cal, i_cal = calibrated_curve

        plt.figure(figsize=(10, 6))
        cal_plot = plt.plot(q_cal, i_cal, label="Calibrated Curve")

        # Plot theoretical peak positions
        for q_val in q_theor:
            plt.axvline(x=q_val, color='r', linestyle='--', label='Theoretical Peaks')

        plt.xlim(0, np.max(q_cal))
        plt.xlabel("q (nm^-1)")
        plt.ylabel("Intensity")
        plt.title("Calibration Result")
        
        # Create a legend with unique labels
        handles, labels = plt.gca().get_legend_handles_labels()
        by_label = dict(zip(labels, handles))
        plt.legend(by_label.values(), by_label.keys())
        
        plt.grid(True)
        plt.show()

    def run(self):
        '''Main application loop.'''
        logging.info("Application run started.")
        try:
            calibrant_name = get_user_input("Enter calibrator name", default="AgBh")
            image_path = get_user_input("Enter the path to the TIFF image for calibration", type_caster=str)

            self.load_data(image_path)

            while True:
                self.get_calibration_parameters()
                self.get_ring_search_parameters()

                refined_params, calibrated_curve, q_theor = self.run_calibration(calibrant_name)

                if refined_params:
                    print("\nRefined Parameters:")
                    for key, value in refined_params.items():
                        print(f"  {key}: {value}")

                self.visualize_results(calibrated_curve, q_theor)

                should_continue = get_user_input("\nContinue calibration? (yes/no)", default="yes").lower()
                if should_continue != 'yes':
                    break
        except Exception as e:
            logging.exception("An unhandled exception occurred during the calibration process.")
            print(f"\nAn unexpected error occurred: {e}. See calibration_app.log for details.")
        
        logging.info("Application run finished.")

if __name__ == "__main__":
    try:
        calibrator = Calibrator()
        app = CalibrationApp(calibrator)
        app.run()
    except Exception as e:
        logging.exception("A critical error occurred on application startup.")
        print(f"A critical error occurred: {e}. See calibration_app.log for details.")
