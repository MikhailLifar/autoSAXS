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

ATSAS_BIN_PREFIX = os.path.expanduser('~/ATSAS-3.2.1-1/bin')
# CONFIG_FILE = "calib_config.conf"
CALIBRATED_GEOMETRY_PATH = 'calibrated_geometry.conf'
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
        self.config = {}
    
    def load_config(self, config_file):
        '''Loads the configuration from the YAML file, or creates it if it doesn't exist.'''
        if not os.path.exists(config_file):
            return self.create_default_config(config_file)
        
        with open(config_file, 'r') as f:
            return yaml.safe_load(f)
    
    def save_config(self, config_file):
        with open(config_file, 'w') as f:
            yaml.dump(self.config, f)
    
    def create_default_config(self, config_file):
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
                'r_min': 60,
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
        with open(config_file, 'w') as f:
            yaml.dump(default_config, f)
        return default_config
    
    def update_config(self, config_file, *keys, values: dict):
        keys = list(keys)

        conf = self.config
        for k in keys:
            if k not in conf:
                conf[k] = {}
            conf = conf[k]
        
        conf.update(values)
        self.save_config(config_file)
    
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
        pc.set_detector_parameters(**geometry_params)
        refine_res = pc.refine()
        
        if visualize:
            self.viewer.view_refined_curve(refine_res['curve_calibrated'], refine_res['theoretical_peaks'])
        
        return refine_res
    
    # def calibration_block(self, fast_forward=True):
    #     pc = self.processor

    #     if fast_forward:
    #         self.processor.calibrant_name = "AgBh"
            
    #         image_path = self.interface.ask_for_file("Enter the path to the TIFF image for calibration")
    #         self.processor.set_calib_data(image_path)

    #         center_ref_params = {k: self.config['center_refinement'][k] 
    #                              for k in ['q_start', 'q_stop', 'min_segment_len']}
    #         self.interface.send_message('Center search...')
    #         center_step_ret = self.center_refinement_step(visualize=False, **center_ref_params)
            
    #         ring_search_params = {k: self.config['ring_search'][k] 
    #                               for k in ['q_stop', 'I_threshold', 'r_max', 'r_step', 'peak_width']}
    #         self.interface.send_message('Rings identification...')
    #         rings_step_ret = self.rings_refinement_step(visualize=False, **ring_search_params)
            
    #         geometry_params = {k: self.config['detector_geometry'][k] 
    #                            for k in ['dist', 'wavelength', 'pixel_size', 'rot1', 'rot2', 'rot3']}
    #         self.interface.send_message('Geometry refinement...')
    #         refine_step_ret = self.geometry_refinement_step(visualize=False, **geometry_params)

    #     else:
    #         self.processor.calibrant_name = self.interface.ask_for_parameter(
    #             'calibrant_name', str, query="Enter calibrator name", default="AgBh")
            
    #         image_path = self.interface.ask_for_file("Enter the path to the TIFF image for calibration")
    #         self.processor.set_calib_data(image_path)

    #         # print(self.config)
    #         center_ref_params = self.interface.ask_for_multiple(
    #             ['q_start', 'q_stop', 'min_segment_len'],
    #             group_name='center refinement',
    #             types=[float, float, int],
    #             defaults=self.config['center_refinement']
    #         )
    #         self.update_config('center_refinement', values=center_ref_params)
    #         center_step_ret = self.center_refinement_step(visualize = True, **center_ref_params)
            
    #         if_adjust = self.interface.ask_question(
    #             'Do you wish to adjust the center search parameters? (yes/no, default no) ')
    #         if if_adjust.lower().startswith('y'):
    #             center_ref_params, center_step_ret = self.interface.interactive(
    #                 center_ref_params,
    #                 types=[float, float, int],
    #                 func=self.center_refinement_step
    #             )
    #             self.update_config('center_refinement', values=center_ref_params)
            
    #         ring_search_params = self.interface.ask_for_multiple(
    #             ['q_stop', 'I_threshold', 'r_max', 'r_step', 'peak_width'],
    #             group_name='ring search',
    #             types=[float, float, int, int, int],
    #             defaults=self.config['ring_search']
    #         )
    #         self.update_config('ring_search', values=ring_search_params)
    #         rings_step_ret = self.rings_refinement_step(visualize = True, **ring_search_params)
            
    #         if_adjust = self.interface.ask_question(
    #             'Do you wish to adjust the ring search parameters? (yes/no, default no) ')
    #         if if_adjust.lower().startswith('y'):
    #             ring_search_params, rings_step_ret = self.interface.interactive(
    #                 ring_search_params,
    #                 types=[float, float, int, int, int],
    #                 func=self.rings_refinement_step
    #             )
    #             self.update_config('ring_search', values=ring_search_params)
            
    #         geometry_params = self.interface.ask_for_multiple(
    #             ['dist', 'wavelength', 'pixel_size', 'rot1', 'rot2', 'rot3'],
    #             group_name='detector geometry',
    #             types=[float, float, json_type_caster, float, float, float],
    #             defaults=self.config['detector_geometry']
    #         )
    #         self.update_config('detector_geometry', values=geometry_params)
    #         refine_step_ret = self.geometry_refinement_step(**geometry_params)
            
    #         if_adjust = self.interface.ask_question(
    #             'Do you wish to adjust the detecotr geometry parameters? (yes/no, default no) ')
    #         if if_adjust.lower().startswith('y'):
    #             geometry_params, refine_step_ret = self.interface.interactive(
    #                 geometry_params,
    #                 types=[float, float, json_type_caster, float, float, float],
    #                 func=self.geometry_refinement_step
    #             )
    #             self.update_config('detector_geometry', values=geometry_params)
            
    #     self.viewer.view_calibration(
    #         img_data=self.processor._calib_data, tiff_path=self.processor._calib_tiff_path,
    #         **center_step_ret, **rings_step_ret, **refine_step_ret)
    #     refined = refine_step_ret['refined']
    #     refined.update({'wavelength': pc.wavelength})
    #     self.interface.send_message(
    #         f'\n-- Calibrated geometry parameters --\n' + '\n'.join(f'{p}: {v}' for p, v in refined.items())  + '\n'
    #     )
    #     with open(CALIBRATED_GEOMETRY_PATH, 'w') as f:
    #         yaml.dump(refined, f)
    
    # def concentration_series(self):
    #     self.interface.send_message('The concentration series begins')
    #     self.interface.send_message('Do not forget to upload 2d data for the buffer')
    #     self.interface.send_message('Recommended concentrations for concentration series are: 2.5, 1., 0.5, 0.25, 0.1 mg/ml')
    #     data_path = self.interface.ask_for_file(
    #         'Please provide the path to the base directory where /2d subdirectory with .tiff files for concentration series is placed. ' \
    #         'The last part (parts seprated by "_") of the name of the file should be the corresponding sample concentration. ' \
    #         'There should also be a .tiff with buffer 2d data which name should end with "_buff"')
        
    #     self.interface.send_message('Started processing concentration series...')
    #     data_2d_path = os.path.join(data_path, '2d')
    #     data_1d_path = os.path.join(data_path, '1d')
    #     for f in os.listdir(data_2d_path):
    #         c = os.path.splitext(f)[0].split('_')[-1]
    #         basename = os.path.basename(f)
    #         saxs_2d = self.processor.read_from_tiff(os.path.join(data_2d_path, f))
    #         if c == 'buff':
    #             metadata = {'type': 'buffer'}
    #         else:
    #             metadata = {'type': 'sample', 'concentration': float(c)}
    #         q, I = self.processor.integrate_2d_to_1d(
    #             saxs_2d, os.path.join(data_1d_path, f'{basename}.dat'), 
    #             metadata=metadata)
    #     self.processor.subtract_buffer(data_1d_path)
    
    def pipeline0(self):
        pc = self.processor
        
        print(get_pipeline_description('pipeline0'))

        directory = self.interface.ask_for_file('Write a path to a directory for your data')

        buffer_path = calibration_path = config_path = ''
        sample_paths = []
        for f in os.listdir(directory):
            if f.endswith('.conf'):
                config_path = os.path.join(directory, f)
            elif f.endswith('_calib.tif'):
                calibration_path = os.path.join(directory, f)
            elif f.endswith('_buf.tif'):
                buffer_path = os.path.join(directory, f)
            elif f.endswith('.tif'):
                sample_paths.append(os.path.join(directory, f))
        
        assert min(len(p) for p in (buffer_path, calibration_path, config_path, sample_paths)) > 0, 'The requirements for pipeline input are not satisfied. Please reveiw your folder structure'
        
        self.config = self.load_config(config_path)

        pc.calibrant_name = self.config['calibrant_name']
        pc.set_calib_data(calibration_path)

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
            img_data=pc._calib_data, tiff_path=pc._calib_tiff_path,
            show=False, plotFilePath=os.path.join(directory, 'calibration.png'),
            **center_step_ret, **rings_step_ret, **refine_step_ret)
        refined = refine_step_ret['refined']
        refined.update({'wavelength': pc.wavelength})
        self.interface.send_message(
            f'\n-- Calibrated geometry parameters --\n' + '\n'.join(f'{p}: {v}' for p, v in refined.items())  + '\n'
        )
        self.interface.send_message('Finished calibration')
        self.update_config(config_path, 'refined', values=refined)

        self.interface.send_message('Integration...')
        new_sample_paths = []
        for p in sample_paths + [buffer_path, ]:
            root, fname = os.path.split(p)
            fname = os.path.splitext(fname)[0]
            destpath = os.path.join(root, f'int_{fname}.dat')
            if p == buffer_path:
                pc.integrate_2d_to_1d(pc.read_from_tiff(p), destpath=destpath,
                                    metadata={'type': 'buffer'})
                buffer_path = destpath
            else:
                pc.integrate_2d_to_1d(pc.read_from_tiff(p), destpath=destpath,
                                    metadata={'type': 'sample'})
                new_sample_paths.append(destpath)
        sample_paths = new_sample_paths
        
        self.interface.send_message('Subtraction...')
        new_sample_paths = []
        for p in sample_paths:
            q, sample, _ = read_saxs(p)
            root, basename = os.path.split(p)
            destpath = os.path.join(root, basename.replace('int_', 'sub_', 1))
            _, _, I_buff_scaled = pc.subtract_buffer(
                buffer_path, p, destpath, match_tail_ops={'q_range_rel': None, 'q_range_abs': self.config['sub']['q_range_abs'], })
            self.viewer.view_curves(
                q, sample, 'sample',
                q, I_buff_scaled, 'buffer scaled',
                legend=True,
                plotFilePath=os.path.join(root, f'diff_{basename}.png'),
                save=False
            )
            new_sample_paths.append(destpath)
        sample_paths = new_sample_paths
        
        # TODO scaling step

        self.interface.send_message('Fitting with shapes...')
        for p in sample_paths:
            bodies_call = os.path.join(ATSAS_BIN_PREFIX, 'bodies')
            os.system(f"{bodies_call} --body=ellipsoid --prefix=ellipsoid_fit {p}")
    
    # def pipeline(self):
    #     try:
    #         self.load_config('calib_config.conf')
            
    #         if os.path.exists(CALIBRATED_GEOMETRY_PATH):
    #             if_calibrate = self.interface.ask_question(
    #                 f'Should the detector geometry be calibrated or the parameters from {CALIBRATED_GEOMETRY_PATH} should be used?',
    #                 options={'c': 'calibrate', 'u': 'use existent'}
    #             )
    #         else:
    #             self.interface.send_message(
    #                 'Calibrated geometry file does not exists. You need to calibrate the geometry of the detector first'
    #                 )
    #             if_calibrate = 'c'
                
    #         if if_calibrate == 'c':
    #             self.calibration_block(fast_forward=True)
    #             if_satisfied = self.interface.ask_question(
    #                 'Are calibration results fine or the parameters should be adjusted?',
    #                 options={'f': 'fine', 'a': 'adjust'}, default_op='f')
                
    #             while if_satisfied == 'a':
    #                 self.calibration_block(fast_forward=False)
    #                 if_satisfied = self.interface.ask_question(
    #                     'Are calibration results fine or the parameters should be adjusted?',
    #                     options={'f': 'fine', 'a': 'adjust'}, default_op='f')

    #         if_liquid = self.interface.ask_question(
    #             'Your sample is in the liquid on in the powder form? (l-liquid/p-powder) ',
    #             options={'l': 'liquid', 'p': 'powder'}, default_op='l')
            
    #         dispersity = self.interface.ask_question(
    #                 'Is your sample monodisperse or polydisperse or it is unkown?',
    #                 options={'m': 'monodisperse', 'p': 'polydisperse', 'u': 'unknown'}
    #             )
            
    #         if if_liquid == 'l':
    #             if dispersity == 'm':
    #                 concentration = self.interface.ask_for_parameter(
    #                     'concentration', float, query='Enter the concentration of the substance of interest, mg/ml ',
    #                 )
                    
    #                 if concentration > 5.:
    #                     do_conc_series = self.interface.ask_question(
    #                         'Since your sample is of high substance concentration, it is recommended for you to proceed with concentration series. '
    #                         'Start concentration series? (yes/no, default yes) ',
    #                         default_op='y'
    #                         )
    #                     if do_conc_series.lower().startswith('y'):
    #                         self.concentration_series()
    #             else:
    #                 raise RuntimeError('There is yet no pipeline for samples which are monodisperse')
    #         elif if_liquid == 'p':
    #             raise RuntimeError('There is no pipeline for powder sample analysis yet')
        
    #         self.interface.send_message('The processing of SAXS data is finished. Good luck!')
            
    #     except Exception as e:
    #         logging.exception("An unhandled exception occurred and interrupted the work of the app.")
    #         self.interface.send_message(f"\nAn unexpected error occurred and interrupted the work of the app: {e}. See calibration_app.log for details.")


if __name__ == '__main__':
    # calib image file path for debug: AgBh/100225_doubling/test/0003_AgBh1000old_or_107.3.tif
    controller = Controller(SAXSProcessor(), CLIInterface(), PLTViewer())
    # controller.pipeline()
    # directory path for pipeline0: debug/pipeline0
    controller.pipeline0()

