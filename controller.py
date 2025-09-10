import yaml
from processor import *
from interface import *
from viewer import *
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
    
    def __init__(self, processor: SAXSProcessor, interface: Interface, viewer: Viewer):
        self.processor = processor
        self.interface = interface
        self.viewer = viewer
        self.config = self.load_config()
    
    def load_config(self):
        '''Loads the configuration from the YAML file, or creates it if it doesn't exist.'''
        if not os.path.exists(CONFIG_FILE):
            return self.create_default_config()
        
        with open(CONFIG_FILE, 'r') as f:
            return yaml.safe_load(f)
    
    def save_config(self):
        with open(CONFIG_FILE, 'w') as f:
            yaml.dump(self.config, f)
    
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
                # 'beam_center_x': None,
                # 'beam_center_y': None,
                'rot1': 0.,
                'rot2': 0.,
                'rot3': 0.,
            }
        }
        with open(CONFIG_FILE, 'w') as f:
            yaml.dump(default_config, f)
        return default_config
    
    def update_config(self, *keys, values: dict):
        keys = list(keys)

        if not keys:
            self.config.update(values)
            self.save_config()
            return
        
        conf = self.config
        if len(keys) > 1:
            for k in keys[:-1]:
                conf = conf[k]
        
        conf[keys[-1]].update(values)
        self.save_config()
    
    def center_refinement_step(self, visualize=True, **center_ref_params):
        pc = self.processor
        pc.set_center_search(**center_ref_params)
        center_search_res = pc.find_and_set_center()

        if visualize:
            self.viewer.view_center(pc._calib_data, pc._calib_tiff_path, 
                                    **center_search_res)
        
        return center_search_res
    
    def rings_refinement_step(self, visualize=True, **ring_search_params):
        pc = self.processor
        pc.set_ring_search(**ring_search_params)
        rings = pc.find_and_set_rings()['rings']

        if visualize:
            self.viewer.view_rings(pc._calib_data, pc._calib_tiff_path, rings=rings)

        return {'rings': rings}

    def geometry_refinement_step(self, visualize=True, **geometry_params):
        # print(f'geometry_refinement_step is called. Parameters are: {", ".join(geometry_params.keys())}')
        pc = self.processor
        pc.set_initial_point(**geometry_params)
        refine_res = pc.refine()
        
        if visualize:
            self.viewer.view_refined_curve(refine_res['curve_calibrated'], refine_res['theoretical_peaks'])
        
        return refine_res
    
    def calibration_block(self, fast_forward=True):
        if fast_forward:
            self.processor.calibrant_name = "AgBh"
            
            image_path, exec_msg = self.interface.ask_for_file("Enter the path to the TIFF image for calibration")
            if exec_msg != 'ok':
                raise RuntimeError
            
            self.processor.set_calib_data(image_path)

            center_ref_params = {k: self.config['center_refinement'][k] 
                                 for k in ['q_start', 'q_stop', 'min_segment_len']}
            self.interface.send_message('Center search...')
            center_step_ret = self.center_refinement_step(visualize=False, **center_ref_params)
            
            ring_search_params = {k: self.config['ring_search'][k] 
                                  for k in ['q_stop', 'I_threshold', 'r_max', 'r_step', 'peak_width']}
            self.interface.send_message('Rings identification...')
            rings_step_ret = self.rings_refinement_step(visualize=False, **ring_search_params)
            
            geometry_params = {k: self.config['detector_geometry'][k] 
                               for k in ['dist', 'wavelength', 'pixel_size', 'rot1', 'rot2', 'rot3']}
            self.interface.send_message('Geometry refinement...')
            refine_step_ret = self.geometry_refinement_step(visualize=False, **geometry_params)

            self.viewer.view_calibration(
                img_data=self.processor._calib_data, tiff_path=self.processor._calib_tiff_path,
                **center_step_ret, **rings_step_ret, **refine_step_ret
            )
        
        else:
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
            self.update_config('center_refinement', values=center_ref_params)
            center_step_ret = self.center_refinement_step(visualize = False, **center_ref_params)
            if_adjust, exec_msg = self.interface.ask_question(
                'Do you wish to adjust the center search parameters? (yes/no, default no) ')
            if exec_msg != 'ok':
                raise RuntimeError
            if if_adjust.lower().startswith('y'):
                center_ref_params, center_step_ret, exec_msg = self.interface.interactive(
                    center_ref_params,
                    types=[float, float, int],
                    func=self.center_refinement_step
                )
                if exec_msg != 'ok':
                    raise RuntimeError
                self.update_config('center_refinement', values=center_ref_params)
            
            ring_search_params, exec_msg = self.interface.ask_for_multiple(
                ['q_stop', 'I_threshold', 'r_max', 'r_step', 'peak_width'],
                group_name='ring search',
                types=[float, float, int, int, int],
                defaults=self.config['ring_search']
            )
            if exec_msg != 'ok':
                raise RuntimeError
            self.update_config('ring_search', values=ring_search_params)
            rings_step_ret = self.rings_refinement_step(visualize = False, **ring_search_params)
            if_adjust, exec_msg = self.interface.ask_question(
                'Do you wish to adjust the ring search parameters? (yes/no, default no) ')
            if exec_msg != 'ok':
                raise RuntimeError
            if if_adjust.lower().startswith('y'):
                ring_search_params, rings_step_ret, exec_msg = self.interface.interactive(
                    ring_search_params,
                    types=[float, float, int, int, int],
                    func=self.rings_refinement_step
                )
                if exec_msg != 'ok':
                    raise RuntimeError
                self.update_config('ring_search', values=ring_search_params)
            
            geometry_params, exec_msg = self.interface.ask_for_multiple(
                ['dist', 'wavelength', 'pixel_size', 'rot1', 'rot2', 'rot3'],
                group_name='detector geometry',
                types=[float, float, json_type_caster, float, float, float],
                defaults=self.config['detector_geometry']
            )
            if exec_msg != 'ok':
                raise RuntimeError
            self.update_config('detector_geometry', values=geometry_params)
            refine_step_ret = self.geometry_refinement_step(**geometry_params)
            if_adjust, exec_msg = self.interface.ask_question(
                'Do you wish to adjust the detecotr geometry parameters? (yes/no, default no) ')
            if exec_msg != 'ok':
                raise RuntimeError
            if if_adjust.lower().startswith('y'):
                geometry_params, refine_step_ret, exec_msg = self.interface.interactive(
                    geometry_params,
                    types=[float, float, json_type_caster, float, float, float],
                    func=self.geometry_refinement_step
                )
                if exec_msg != 'ok':
                    raise RuntimeError
                self.update_config('detector_geometry', values=geometry_params)
            
            self.viewer.view_calibration(
                img_data=self.processor._calib_data, tiff_path=self.processor._calib_tiff_path,
                **center_step_ret, **rings_step_ret, **refine_step_ret)
    
    def pipeline(self):
        try:
            self.calibration_block(fast_forward=True)
            self.interface.send_message('The processing of SAXS data is finished. Good luck!')
            
        except Exception as e:
            logging.exception("An unhandled exception occurred during the calibration process.")
            self.interface.send_message(f"\nAn unexpected error occurred: {e}. See calibration_app.log for details.")


if __name__ == '__main__':
    # image file path for debug: AgBh/100225_doubling/test/0003_AgBh1000old_or_107.3.tif
    controller = Controller(SAXSProcessor(), CLIInterface(), PLTViewer())
    controller.pipeline()
