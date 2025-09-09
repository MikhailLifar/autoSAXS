import yaml
from processor import *
from interface import *
import os
import logging
import warnings
import json

import numpy as np
import pandas as pd
import matplotlib as mpl
import matplotlib.pyplot as plt
import seaborn as sns

CONFIG_FILE = "calib_config.conf"
DEBUG = True

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='calibration_app.log',
    filemode='w'
)
logging.captureWarnings(True)


def json_type_caster(s):
    try:
        return json.loads(s)
    except:
        raise ValueError('Incorrect JSON passed')


class Controller:
    """
    This class should combine an interface and a processor. In fact, it looks like the MVC model:
    Model is SAXSProcessor and Viewer is Interface
    """
    
    def __init__(self, processor: SAXSProcessor, interface: Interface):
        self.processor = processor
        self.interface = interface
        self.config = self.load_config()
    
    def load_config(self):
        '''Loads the configuration from the YAML file, or creates it if it doesn't exist.'''
        if not os.path.exists(CONFIG_FILE):
            return self.create_default_config()
        
        with open(CONFIG_FILE, 'r') as f:
            return yaml.safe_load(f)
    
    def create_default_config(self):
        '''Creates a default configuration file.'''
        default_config = {
            'center_refinement': {
                'q_start': 0.95,
                'q_stop': 0.995,
                'min_segment_len': 50,
            },
            'ring_search': {
                'q_stop': 0.995,
                'I_threshold': 80.0,
                'r_max': 700,
                'r_step': 3,
                'peak_width': 60
            },
            'detector_geometry': {
                'dist': None,
                'wavelength': 1.445e-10,
                'pixel_size': [1.e-4, 1.e-4], # Stored as a list in YAML
                'beam_center_x': None,
                'beam_center_y': None
            }
        }
        with open(CONFIG_FILE, 'w') as f:
            yaml.dump(default_config, f)
        return default_config
    
    def center_refinement_step(self, visualize=True, **center_ref_params):
        pc = self.processor
        pc.set_center_search(**center_ref_params)
        center_y, center_x, clusters = pc.find_and_set_center()

        if visualize:
            img_data = pc._calib_data
            ylim, xlim = img_data.shape
            fig, axs = plt.subplots(1, 2, figsize=(16, 6))
            
            im = axs[0].imshow(np.log1p(img_data), cmap='viridis', origin='lower')
            # plt.colorbar(im, ax=axs[0], label='Log(Intensity + 1)')
            axs[0].set_title(f"2D SAXS Data: {os.path.basename(pc._calib_tiff_path)}")
            axs[0].set_xlabel("Pixel X")
            axs[0].set_ylabel("Pixel Y")

            axs[1].imshow(np.log1p(img_data), cmap='viridis', origin='lower')
            scatter_data = pd.DataFrame(data=clusters, columns=['y', 'x', 'cluster'])
            sns.scatterplot(data=scatter_data, y='y', x='x', hue='cluster', ax=axs[1],
                            palette=get_bright_fire_cmap()[0])
            axs[1].plot(center_x, center_y, 'r*')
            axs[1].set_xlim(0, xlim)
            axs[1].set_ylim(0, ylim)
            axs[1].set_title(f"Apparent rings and the center")
            axs[1].set_xlabel("Pixel X")
            axs[1].set_ylabel("Pixel Y")

            plt.show()
    
    def rings_refinement_step(self, visualize=True, **ring_search_params):
        pc = self.processor
        pc.set_ring_search(**ring_search_params)
        rings, _, _ = pc.find_and_set_rings()

        if visualize:
            img_data = pc._calib_data
            ylim, xlim = img_data.shape
            fig, axs = plt.subplots(1, 2, figsize=(16, 6))
            
            im = axs[0].imshow(np.log1p(img_data), cmap='viridis', origin='lower')
            # plt.colorbar(im, ax=axs[0], label='Log(Intensity + 1)')
            axs[0].set_title(f"2D SAXS Data: {os.path.basename(pc._calib_tiff_path)}")
            axs[0].set_xlabel("Pixel X")
            axs[0].set_ylabel("Pixel Y")

            axs[1].imshow(np.log1p(img_data), cmap='viridis', origin='lower')
            scatter_data = pd.DataFrame(data=rings, columns=['y', 'x', 'ring_number'])
            sns.scatterplot(data=scatter_data, y='y', x='x', hue='ring_number', ax=axs[1],
                            palette=get_bright_fire_cmap()[0])
            axs[1].set_xlim(0, xlim)
            axs[1].set_ylim(0, ylim)
            axs[1].set_title(f"Apparent rings, refined")
            axs[1].set_xlabel("Pixel X")
            axs[1].set_ylabel("Pixel Y")

            plt.show()

    def geometry_refinement_step(self, visualize=True, **geometry_params):
        # print(f'geometry_refinement_step is called. Parameters are: {", ".join(geometry_params.keys())}')
        pc = self.processor
        pc.set_initial_point(**geometry_params)
        refined_params, curve_calibrated, theoretical_peaks = pc.refine()
        
        if visualize:
            q_cal, i_cal = curve_calibrated

            plt.figure(figsize=(10, 6))
            cal_plot = plt.plot(q_cal, i_cal, label="Calibrated Curve")

            # Plot theoretical peak positions
            for q_val in theoretical_peaks:
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

    
    def pipeline(self):
        try:
            self.processor.calibrant_name, exec_msg = self.interface.ask_for_parameter(
                'calibrant_name', str, query="Enter calibrator name", default="AgBh")
            if exec_msg != 'ok':
                raise RuntimeError
            
            image_path, exec_msg = self.interface.ask_for_file("Enter the path to the TIFF image for calibration")
            if exec_msg != 'ok':
                raise RuntimeError
            
            self.processor.set_calib_data(image_path)

            # print(self.config)
            center_ref_params, exec_msg = self.interface.ask_for_multiple(
                ['q_start', 'q_stop', 'min_segment_len'],
                group_name='center refinement',
                types=[float, float, int],
                defaults=self.config['center_refinement']
            )
            if exec_msg != 'ok':
                raise RuntimeError

            self.center_refinement_step(visualize = False, **center_ref_params)
            if_adjust, exec_msg = self.interface.ask_question(
                'Do you wish to adjust the center search parameters? (yes/no, default no) ')
            if exec_msg != 'ok':
                raise RuntimeError
            if if_adjust.lower().startswith('y'):
                self.interface.interactive(
                    center_ref_params,
                    types=[float, float, int],
                    func=self.center_refinement_step
                )
            
            ring_search_params, exec_msg = self.interface.ask_for_multiple(
                ['q_stop', 'I_threshold', 'r_max', 'r_step', 'peak_width'],
                group_name='ring search',
                types=[float, float, int, int, int],
                defaults=self.config['ring_search']
            )
            if exec_msg != 'ok':
                raise RuntimeError
            self.rings_refinement_step(visualize = False, **ring_search_params)
            if_adjust, exec_msg = self.interface.ask_question(
                'Do you wish to adjust the ring search parameters? (yes/no, default no) ')
            if exec_msg != 'ok':
                raise RuntimeError
            if if_adjust.lower().startswith('y'):
                self.interface.interactive(
                    ring_search_params,
                    types=[float, float, int, int, int],
                    func=self.rings_refinement_step
                )
            
            geometry_params, exec_msg = self.interface.ask_for_multiple(
                ['dist', 'wavelength', 'pixel_size', 'rot1', 'rot2', 'rot3'],
                group_name='detector geometry',
                types=[float, float, json_type_caster, float, float, float],
                defaults=self.config['detector_geometry']
            )
            if exec_msg != 'ok':
                raise RuntimeError
            self.geometry_refinement_step(**geometry_params)
            if_adjust, exec_msg = self.interface.ask_question(
                'Do you wish to adjust the detecotr geometry parameters? (yes/no, default no) ')
            if exec_msg != 'ok':
                raise RuntimeError
            if if_adjust.lower().startswith('y'):
                self.interface.interactive(
                    geometry_params,
                    types=[float, float, json_type_caster, float, float, float],
                    func=self.geometry_refinement_step
                )
            
            self.interface.send_message('The processing of SAXS data was finished. Good luck!')
            
        except Exception as e:
            logging.exception("An unhandled exception occurred during the calibration process.")
            self.interface.send_message(f"\nAn unexpected error occurred: {e}. See calibration_app.log for details.")


if __name__ == '__main__':
    # image file path for debug: AgBh/100225_doubling/test/0003_AgBh1000old_or_107.3.tif
    controller = Controller(SAXSProcessor(), CLIInterface())
    controller.pipeline()
