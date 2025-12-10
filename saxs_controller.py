import yaml
from processor import *
from interface import *
from viewer import *
from context import Context
import os
import sys
import logging
import warnings
import json

import numpy as np
import pandas as pd
import matplotlib as mpl
import matplotlib.pyplot as plt
import seaborn as sns

from ase.io import read

sys.path.append(os.path.expanduser('~/LLM/LLMAssistant'))
sys.path.append(os.path.expanduser('~/LLM/LLMAssistant/aiAssistantFramework'))

# from aiAssistantFramework import lib as ai_lib
# from aiAssistantFramework.lib import llm, telegram
import controller as ai_controller
from gui import get_pipeline_spec_gui, choose_profiles

ATSAS_BIN_PREFIX = os.path.expanduser('~/ATSAS-3.2.1-1/bin')
# CONFIG_FILE = "calib_config.conf"
# CALIBRATED_GEOMETRY_PATH = 'calibrated_geometry.conf'
ROOT_DIR = os.path.expanduser('~/KurchatovCoop')
PROMPTS_DIR = os.path.join(ROOT_DIR, 'repos', 'prompts')
LATEST_STEPS_PATH = os.path.join(ROOT_DIR, 'temp', 'latest_steps.yml')
DEBUG = True

BODIES_SHAPES = {
    # radius r, height h
    'cylinder': {
        'r': 'radius',
        'h': 'height',
    },
    # radius of the first ball r1, radius of the second ball r2, their center-to-center distance d
    'dumbbell': {
        'r1': 'radius-1',
        'r2': 'radius-2',
        'd': 'center-to-center distance',
    },
    # semiaxes a, b, c
    'ellipsoid': {
        'a': 'semiaxis a',
        'b': 'semiaxis b',
        'c': 'semiaxis c',
    },
    # radii semiaxes a, c, height h
    'elliptic-cylinder': {
        'a': 'semiaxis a',
        'c': 'semiaxis c',
        'h': 'height',
    },
    # outer radius ro, inner radius ri, height h
    'hollow-cylinder': {
        'ro': 'outer radius',
        'ri': 'inner radius',
        'h': 'height',
    },
    # outer radius ro, inner radius ri
    'hollow-sphere': {
        'ro': 'outer radius',
        'ri': 'inner radius',
    },
    # sides a, b, c
    'parallelepiped': {
        'a': 'side a',
        'b': 'side b',
        'c': 'side c',
    },
    # semiaxes a, c
    'rotation-ellipsoid': {
        'a': 'semiaxis a',
        'c': 'semiaxis c',
    },
}

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


def save_latest_steps(pipeline_choice, steps):
    """
    Persist the last selected steps so they can be offered as a default later.
    """
    os.makedirs(os.path.dirname(LATEST_STEPS_PATH), exist_ok=True)
    with open(LATEST_STEPS_PATH, 'w') as f:
        yaml.safe_dump({'pipeline': pipeline_choice, 'steps': list(steps)}, f)


# class Block:
#     def __init__(self, interface: Interface, viewer: Viewer, required_paths: list):
#         self.interface = interface
#         self.viewer = viewer
#         self.required_paths = required_paths
    
#     def __call__(self, paths: Paths, dest_dir, config, *args, **kwargs):
#         raise NotImplementedError
    
#     def check_paths(self, paths: Paths):
#         return all(paths_group in paths.paths for paths_group in self.required_paths)


# class Autocalib(Block):
#     def __init__(self, *args, required_paths=None, **kwargs):
#         if required_paths is None:
#             required_paths = ['calibrant_2d', 'config']
#         Block.__init__(self, *args, required_paths=required_paths, **kwargs)

#     def __call__(self, paths: Paths, dest_dir, config, paths_mode, debug=False):
#         config_path = paths.get_paths('config', paths_mode)
#         calibrant_path = paths.get_paths('calibrant_2d', paths_mode)
        
#         # Check if calibration results exist in debug mode
#         calibration_results_file = os.path.join(dest_dir, 'calibration.png')
#         integrator_subd = os.path.join(dest_dir, 'integrator_params')
#         refined_config_exists = 'refined' in config
        
#         calibrant_name = config['calibrant_name']
#         calib_data = read_from_tiff(calibrant_path)
        
#         if debug and refined_config_exists and all(
#             os.path.exists(p) for p in (calibration_results_file, integrator_subd)
#         ):
#             self.interface.send_message('Debug mode: Skipping calibration (results already exist)')
#             refined = config['refined']
#             integrator = IntegratorExtended.from_disk(integrator_subd)
#             return {'integrator': integrator, 'refined': refined}
#         else:
#             self.interface.send_message('Autocalibration...')

#             center_ref_params = {k: config['center_refinement'][k] 
#                                 for k in ['q_start', 'q_stop', 'min_segment_len']}
#             self.interface.send_message('    Center search...')
#             center_step_ret = find_center(calib_data, **center_ref_params)
#             # self.viewer.view_center(calib_data, calibrant_path, **center_search_res)
            
#             d_geom = config['detector_geometry']
#             interring_dist_px = get_interring_dist_px(
#                 d_geom['dist'], d_geom['wavelength'], d_geom['pixel_size'][0]
#             )

#             ring_search_params = {k: config['ring_search'][k] 
#                                   for k in ['q_stop', 'ring_I_threshold', 'r_max_px', 'r_step_px']}
#             ring_search_params.update({
#                 'r_beam_px': config['r_beam_px'],
#                 'center_y_px': center_step_ret['center_y_px'],
#                 'center_x_px': center_step_ret['center_x_px'],
#                 'interring_dist_px': interring_dist_px
#             })
#             self.interface.send_message('    Rings identification...')
#             rings_step_ret = find_rings(calib_data, **ring_search_params)
#             # self.viewer.view_rings(calib_data, calibrant_path, rings=find_rings_res['rings'])
            
#             geometry_params = {k: config['detector_geometry'][k] 
#                                 for k in ['dist', 'wavelength', 'pixel_size', 'rot1', 'rot2', 'rot3']}
#             geometry_params.update({
#                 'r_beam_px': config['r_beam_px'],
#                 'center_y_px': center_step_ret['center_y_px'],
#                 'center_x_px': center_step_ret['center_x_px'],
#                 'calibrant_name': calibrant_name,
#             })
#             self.interface.send_message('    Geometry refinement...')
#             refine_step_ret = refine(calib_data, rings_step_ret['rings'], **geometry_params)
#             # self.viewer.view_refined_curve(refine_step_ret['curve_calibrated'], refine_step_ret['theoretical_peaks'])
            
#             refine_step_ret['integrator'].to_disk(integrator_subd)

#             self.viewer.view_calibration(
#                 img_data=calib_data, tiff_path=calibrant_path,
#                 show=False, plotFilePath=calibration_results_file,
#                 **center_step_ret, **rings_step_ret, **refine_step_ret)
#             refined = refine_step_ret['refined']
#             refined.update({'wavelength': config['detector_geometry']['wavelength']})
#             self.interface.send_message(
#                 f'\n-- Calibrated geometry parameters --\n' + '\n'.join(f'{p}: {v}' for p, v in refined.items())  + '\n'
#             )
#             self.interface.send_message('Finished calibration')
#             update_config(config, config_path, 'refined', values=refined)
#             return None, {k: refine_step_ret[k] for k in ('refined', 'integrator')}


# class Ingegration(Block):
#     def __init__(self, *args, **kwargs):
#         required_paths = ['calibrant_2d', 'config']
#         Block.__init__(self, *args, required_paths=required_paths, **kwargs)

#     def __call__(self, paths: Paths, dest_dir, config, paths_mode, debug=False):
#         pass


class Controller:
    """
    This class should combine an interface and a processor. In fact, it looks like the MVC model:
    Model is SAXSProcessor and Viewer is Interface
    """
    
    def __init__(self, interface: Interface, viewer: Viewer):
        self.interface = interface
        self.viewer = viewer
    
    def center_refinement_step(self, calib_data, visualize=True, calib_tiff_path='', **center_ref_params):
        center_search_res = find_center(calib_data, **center_ref_params)
        if visualize:
            self.viewer.view_center(calib_data, calib_tiff_path, 
                                    **center_search_res)
        return center_search_res
    
    def rings_refinement_step(self, calib_data, visualize=True, calib_tiff_path='', **ring_search_params):
        find_rings_res = find_rings(calib_data, **ring_search_params)
        if visualize:
            self.viewer.view_rings(calib_data, calib_tiff_path, rings=find_rings_res['rings'])
        return find_rings_res

    def geometry_refinement_step(self, calib_data, rings, visualize=True, **geometry_params):
        # print(f'geometry_refinement_step is called. Parameters are: {", ".join(geometry_params.keys())}')
        refine_res = refine(calib_data, rings, **geometry_params)
        if visualize:
            self.viewer.view_refined_curve(refine_res['curve_calibrated'], refine_res['theoretical_peaks'])
        return refine_res

    def autocalib(self, calibrant_path, context: Context, fast_forward=False):
        directory = context.directory
        
        # Check if calibration results exist in debug mode
        calibration_results_file = os.path.join(directory, 'calibration.png')
        integrator_subd = os.path.join(directory, 'integrator_params')
        refined_config_exists = 'refined' in context
        
        if fast_forward and refined_config_exists and all(
            os.path.exists(p) for p in (calibration_results_file, integrator_subd)
        ):
            self.interface.send_message('Fast-forward mode: Skipping calibration (results already exist)')
            refined = context['refined']
            integrator = IntegratorExtended.from_disk(integrator_subd)
            return {'integrator': integrator, 'refined': refined}
        else:
            if not calibrant_path:
                return {'integrator': None, 'refined': None}

            calibrant_name = context['calibrant_name']
            calib_data = read_from_tiff(calibrant_path)
            
            self.interface.send_message('Autocalibration...')

            center_ref_params = {k: context['center_refinement', k] 
                                for k in ['q_start', 'q_stop', 'min_segment_len']}
            self.interface.send_message('    Center search...')
            center_step_ret = self.center_refinement_step(calib_data, visualize=False, calib_tiff_path=calibrant_path, **center_ref_params)
            
            d_geom = context['detector_geometry']
            interring_dist_px = get_interring_dist_px(
                d_geom['dist'], d_geom['wavelength'], d_geom['pixel_size'][0]
            )

            ring_search_params = {k: context['ring_search', k] 
                                  for k in ['q_stop', 'ring_I_threshold', 'r_max_px', 'r_step_px']}
            ring_search_params.update({
                'r_beam_px': context['r_beam_px'],
                'center_y_px': center_step_ret['center_y_px'],
                'center_x_px': center_step_ret['center_x_px'],
                'interring_dist_px': interring_dist_px
            })
            self.interface.send_message('    Rings identification...')
            rings_step_ret = self.rings_refinement_step(calib_data, visualize=False, calib_tiff_path=calibrant_path, **ring_search_params)
            
            geometry_params = {k: context['detector_geometry', k] 
                                for k in ['dist', 'wavelength', 'pixel_size', 'rot1', 'rot2', 'rot3']}
            geometry_params.update({
                'r_beam_px': context['r_beam_px'],
                'center_y_px': center_step_ret['center_y_px'],
                'center_x_px': center_step_ret['center_x_px'],
                'calibrant_name': calibrant_name,
            })
            self.interface.send_message('    Geometry refinement...')
            refine_step_ret = self.geometry_refinement_step(
                calib_data, rings_step_ret['rings'], visualize=False, **geometry_params)
            
            refine_step_ret['integrator'].to_disk(integrator_subd)

            self.viewer.view_calibration(
                img_data=calib_data,
                tiff_path=calibrant_path,
                show_duration=None,
                plotFilePath=calibration_results_file,
                **center_step_ret,
                **rings_step_ret,
                **refine_step_ret,
            )
            refined = refine_step_ret['refined']
            refined.update({'wavelength': context['detector_geometry', 'wavelength']})
            self.interface.send_message(
                f'\n-- Calibrated geometry parameters --\n' + '\n'.join(f'{p}: {v}' for p, v in refined.items())  + '\n'
            )
            self.interface.send_message('Finished calibration')
            context['refined'] = refined
            context.update_config('refined', values=refined)
            return {k: refine_step_ret[k] for k in ('refined', 'integrator')}
        
    def integrate(self, ai, context: Context, to_int_path, dest_dir, metadata, 
                  fast_forward=False):
        int_path = ''
        
        if to_int_path:
            os.makedirs(dest_dir, exist_ok=True)

            root, fname = os.path.split(to_int_path)
            fname = os.path.splitext(fname)[0]
            int_path = os.path.join(dest_dir, f'int_{fname}.dat')
            
            # Check if integration results exist in debug mode
            if fast_forward and os.path.exists(int_path):
                self.interface.send_message(f'Debug mode: Skipping integration for {to_int_path} (results already exist)')
            else:
                integrate_2d_to_1d(ai, read_from_tiff(to_int_path), destpath=int_path,
                                    metadata=metadata)
        
        return int_path
    
    def subtract(self, context: Context, to_sub_path, buffer_path, dest_dir, fast_forward=False):
        sub_path, sub_plot_path = '', ''
        
        if to_sub_path:
            os.makedirs(dest_dir, exist_ok=True)

            q, sample, sigma_sample, _ = read_saxs(to_sub_path)
            root, basename = os.path.split(to_sub_path)
            basename, _ = os.path.splitext(basename)
            basename = basename.replace('int_', '', 1)
            sub_path = os.path.join(dest_dir, f"sub_{basename}.dat")
            diff_plot_path = os.path.join(dest_dir, f'diff_{basename}.png')
            sub_plot_path = os.path.join(dest_dir, f'sub_{basename}.png')
            
            # Check if subtraction results exist in debug mode
            if fast_forward and all(os.path.exists(p) for p in (sub_path, diff_plot_path, sub_plot_path)):
                self.interface.send_message(f'Debug mode: Skipping subtraction for {to_sub_path} (results already exist)')
                
            else:
                _, I_sub, I_buff_scaled, sigma_sub, sigma_buff_scaled = subtract_buffer(
                    buffer_path, to_sub_path, sub_path, 
                    match_tail_ops={
                        'q_range_rel': None, 
                        'q_range_abs': context['sub', 'q_range_abs'], 
                    })
                self.viewer.view_curves(
                    q, sample, 'sample',
                    q, I_buff_scaled, 'buffer scaled',
                    sigmas=(sigma_sample, sigma_buff_scaled),
                    legend=True,
                    plotFilePath=diff_plot_path,
                    save=False
                )
                self.viewer.view_curves(
                    q, I_sub, 'sample',
                    sigmas=(sigma_sub, ),
                    legend=True,
                    plotFilePath=sub_plot_path,
                    save=False
                )
        
        return sub_path, sub_plot_path
    
    def get_descriptors(self, context: Context, to_analyze_path, 
                        dest_dir, fast_forward=False,
                        ):
        results_file, gnom_file = '', ''

        if to_analyze_path:
            os.makedirs(dest_dir, exist_ok=True)

            root, basename = os.path.split(to_analyze_path)
            basename, _ = os.path.splitext(basename)
            results_file = os.path.join(dest_dir, f'{basename}_results.txt')
            gnom_file = os.path.join(dest_dir, f'{basename}.out')
            
            # Check if analysis results exist in debug mode
            if fast_forward and all(os.path.exists(pp) for pp in (results_file, gnom_file)):
                self.interface.send_message(f'Debug mode: Skipping analysis for {to_analyze_path} (results already exist)')
            
            else:            
                os.system(f'''INPUT_FILE={to_analyze_path}
BASENAME={os.path.join(root, basename)}
RESULTS_FILE="{results_file}"

# Create output file
echo "SAXS Analysis Results" > "$RESULTS_FILE"
echo "====================" >> "$RESULTS_FILE"
echo "Input file: $INPUT_FILE" >> "$RESULTS_FILE"
echo "Analysis date: $(date)" >> "$RESULTS_FILE"
echo "" >> "$RESULTS_FILE"

# Step 1: Calculate Rg and I(0) using AUTORG
AUTORG_OUTPUT=$({os.path.join(ATSAS_BIN_PREFIX, 'autorg')} "$INPUT_FILE")
RG_VALUE=$(echo "$AUTORG_OUTPUT" | grep "Rg   =" | awk '{{print $3}}')
I0_VALUE=$(echo "$AUTORG_OUTPUT" | grep "I(0) =" | awk '{{print $3}}')
QUALITY=$(echo "$AUTORG_OUTPUT" | grep "Quality:" | awk '{{print $2}}')

echo "AUTORG Results:" >> "$RESULTS_FILE"
echo "  Rg = $RG_VALUE nm" >> "$RESULTS_FILE"
echo "  I(0) = $I0_VALUE" >> "$RESULTS_FILE"
echo "  Quality = $QUALITY" >> "$RESULTS_FILE"
echo "" >> "$RESULTS_FILE"

# Step 1.5 Calculate P(R)
{os.path.join(ATSAS_BIN_PREFIX, 'datgnom')} "$INPUT_FILE" -r $RG_VALUE -o {gnom_file}

# Step 2: Calculate Porod invariant using DATPOROD
# DATPOROD_OUTPUT=$({os.path.join(ATSAS_BIN_PREFIX, 'datporod')} "$INPUT_FILE")
# POROD_INV=$(echo "$DATPOROD_OUTPUT" | grep "Porod invariant" | awk '{{print $4}}')
# POROD_VOL=$(echo "$DATPOROD_OUTPUT" | grep "Porod volume" | awk '{{print $4}}')

# echo "DATPOROD Results:" >> "$RESULTS_FILE"
# echo "  Porod invariant = $POROD_INV" >> "$RESULTS_FILE"
# echo "  Porod volume = $POROD_VOL nm^3" >> "$RESULTS_FILE"
# echo "" >> "$RESULTS_FILE"

# Step 3: Calculate molecular weight estimates
# Method 1: From I(0) and Porod volume
# MW = I(0) * N_A / (c * (Δρ)^2 * V_porod)
# This is a simplified formula; actual implementation depends on your sample conditions
# MW_POROD=$(echo "scale=2; $I0_VALUE * 6.022e23 / (1 * 2.82e23 * $POROD_VOL)" | bc -l | awk '{{printf "%.2e", $1}}')

# Method 2: From Rg (empirical relationship for globular proteins)
# MW = (Rg / 0.715)^3 * 1e3 (kDa)
MW_RG=$(echo "scale=2; ($RG_VALUE / 0.715)^3 * 1000" | bc -l | awk '{{printf "%.2f", $1}}')

echo "Molecular Weight Estimates:" >> "$RESULTS_FILE"
# echo "  From Porod volume: $MW_POROD g/mol" >> "$RESULTS_FILE"
echo "  From Rg (globular): $MW_RG kDa" >> "$RESULTS_FILE"
echo "" >> "$RESULTS_FILE"

# Print summary to console
echo ""
echo "===== Analysis Summary ====="
echo "Radius of gyration (Rg): $RG_VALUE nm"
echo "Forward scattering (I(0)): $I0_VALUE"
# echo "Porod invariant: $POROD_INV"
# echo "Porod volume: $POROD_VOL nm^3"
echo ""
echo "Molecular weight estimates:"
# echo "  From Porod volume: $MW_POROD g/mol"
echo "  From Rg (globular): $MW_RG kDa"
echo ""
echo "Plots"
echo "  - Guinier plot"
echo "  - Kratky plot"
echo ""
echo "Full results saved to: $RESULTS_FILE"
''')
        
        return results_file, gnom_file
    
    def plot(self, context: Context, to_plot_path, dest_dir, fast_forward=False):
        guinier_plot_path = kratky_plot_path = loglog_plot_path = ''
        
        if to_plot_path:
            os.makedirs(dest_dir, exist_ok=True)

            root, basename = os.path.split(to_plot_path)
            basename, _ = os.path.splitext(basename)
            # sub_plot_path = os.path.join(dest_dir, f'{basename}.png')
            guinier_plot_path = os.path.join(dest_dir, f'guinier_{basename}.png')
            kratky_plot_path = os.path.join(dest_dir, f'kratky_{basename}.png')
            loglog_plot_path = os.path.join(dest_dir, f'loglog_{basename}.png')
            
            if fast_forward and all(os.path.exists(p) for p in (
                # sub_plot_path, 
                guinier_plot_path, kratky_plot_path, loglog_plot_path)):
                self.interface.send_message(f'Debug mode: Skipping plots for {to_plot_path} (results already exist)')

            else:
                # plots
                q, I, _, _ = read_saxs(to_plot_path)

                # self.viewer.view_curves(
                #     q, I, 'I vs q',
                #     xlabel='q (nm-1)', ylabel='I (a.u.)',
                #     legend=True,
                #     plotFilePath=sub_plot_path,
                #     save=False
                # )

                write_data(
                    os.path.join(dest_dir, f'guinier_{basename}.dat'),
                    pd.DataFrame(np.stack([q*q, np.log(I)], axis=-1), columns=['q^2', 'log(I)']),
                    metadata={'type': 'guinier', 'parent': to_plot_path}
                )
                self.viewer.view_curves(
                    q*q, np.log(I), 'log(I) vs q^2',
                    xlabel='q^2 (nm-2)', ylabel='log(I) (a.u.)',
                    legend=True,
                    plotFilePath=guinier_plot_path,
                    save=False
                )

                write_data(
                    os.path.join(dest_dir, f'kratky_{basename}.dat'),
                    pd.DataFrame(np.stack([q, q * q * I], axis=-1), columns=['q', 'I * q^2']),
                    metadata={'type': 'kratky', 'parent': to_plot_path}
                )
                self.viewer.view_curves(
                    q, q * q * I, 'I * q^2 vs q',
                    xlabel='q (nm-1)', ylabel='I * q^2 (a.u.)',
                    legend=True,
                    plotFilePath=kratky_plot_path,
                    save=False
                )

                write_data(
                    os.path.join(dest_dir, f'loglog_{basename}.dat'),
                    pd.DataFrame(np.stack([np.log(q), np.log(I)], axis=-1), columns=['log(q)', 'log(q)']),
                    metadata={'type': 'loglog', 'parent': to_plot_path}
                )
                self.viewer.view_curves(
                    np.log(q), np.log(I), 'log(I) vs log(q)',
                    xlabel='log(q)', ylabel='log(I)',
                    legend=True,
                    plotFilePath=loglog_plot_path,
                    save=False
                )
        
        ret = [
            # sub_plot_path, 
            guinier_plot_path, kratky_plot_path, loglog_plot_path
        ]
        return ret
    
    def bodies_fit(self, context: Context, saxs_1d_path, dest_dir, fast_forward=False):
        if saxs_1d_path:
            self.interface.send_message('BODIES fit...')
            root, basename = os.path.split(saxs_1d_path)
            basename, _ = os.path.splitext(basename)
            
            bodies_subdir = os.path.join(dest_dir, f'bodies_{basename}')
            os.makedirs(bodies_subdir, exist_ok=True)
            bodies_call = os.path.join(ATSAS_BIN_PREFIX, 'bodies')
            bodies_prefix = os.path.join(bodies_subdir, 'bodies_fit')

            bodies_fits_png = os.path.join(bodies_subdir, f'{basename}_fits.png')
            
            exists_bodies = all(
                os.path.exists(os.path.join(bodies_subdir, f'bodies_fit-{shape}.fir'))
                for shape in BODIES_SHAPES
            )
            
            if fast_forward and exists_bodies and os.path.exists(bodies_fits_png):
                self.interface.send_message(f'Debug mode: Skipping BODIES fit for {saxs_1d_path} (results already exist)')

            else:
                q, I, sigma, _ = read_saxs(saxs_1d_path)
                
                first_nm, last_nm = context['bodies', 'q_range_nm']
                first_chnl, last_chnl = context['bodies', 'q_range_channels']
                if first_nm is not None and last_nm is not None:
                    assert first_chnl is None and last_chnl is None
                    first_chnl = np.argmin(np.abs(q - first_nm)) + 1
                    last_chnl = np.argmin(np.abs(q - last_nm)) + 1
                assert first_chnl is not None and last_chnl is not None

                print(f'DEBUG - how BODIES is called: {bodies_call} --prefix={bodies_prefix} --first={first_chnl} --last={last_chnl} {saxs_1d_path}')
                # os.system(f"{bodies_call} --prefix={bodies_prefix} {saxs_1d_path} --first={first_chnl} --last={last_chnl}")
                os.system(f"{bodies_call} --prefix={bodies_prefix} {saxs_1d_path}")

                to_plot = [q, I, {'label': 'exp', 'lw': 4}]
                for shape in BODIES_SHAPES:
                    fir_path = os.path.join(bodies_subdir, f'bodies_fit-{shape}.fir')
                    # cif_path = os.path.join(bodies_subdir, f'bodies_fit-{shape}-damstart.cif')
                    
                    with open(fir_path, 'r') as f:
                        first_line = f.readline().strip()
                        # Example line: 'elliptic-cylinder: a=2.20304, c=1.30633, h=2.43344, scale=0.488440'
                        import re
                        params_dict = {}
                        # Match pattern: <shape_name>: <param1>=<value1>, <param2>=<value2>, ...
                        match = re.match(r'^(?P<shape>[\w\-]+):\s*(?P<params>.+)$', first_line)
                        if match:
                            params_str = match.group('params')
                            # Split by comma, then extract param=value for each
                            for param_assignment in params_str.split(','):
                                param_assignment = param_assignment.strip()
                                kv_match = re.match(r'^(\w+)\s*=\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)$', param_assignment)
                                if kv_match:
                                    key, value = kv_match.group(1), kv_match.group(2)
                                    params_dict[key] = float(value)
                        else:
                            params_dict = {}

                    structure = (shape, params_dict)
                    
                    data = np.loadtxt(fir_path, skiprows=1, dtype=np.float64)
                    q_fit, I_fit, sigma_bodies = data[:, 0], data[:, 3], data[:, 2]
                    idx_intersection = (q <= q_fit[-1])
                    q_intersetcion, I_intersection = q[idx_intersection], I[idx_intersection]
                    sigma_interp = np.interp(q_intersetcion, q_fit, sigma_bodies)
                    I_fit_interp = np.interp(q_intersetcion, q_fit, I_fit)

                    chi2 = calc_chi2(I_intersection, I_fit_interp, sigma_interp)
                    to_plot.extend([q_intersetcion, I_fit_interp, f'{shape}; chi2: {chi2:.5f}'])

                    self.viewer.plot_3d_views_and_scattering(
                        structure, q_intersetcion, I_intersection, sigma_interp, I_fit_interp, 
                        plotFilePath=os.path.join(bodies_subdir, f'{shape}_view.png')
                    )
                    # atoms = read_bodies_cif(cif_path)
                    # self.viewer.plot_structure_and_scattering(
                    #     atoms, q_intersetcion, I_intersection, sigma_interp, I_fit_interp, 
                    #     plotFilePath=os.path.join(bodies_subdir, f'{shape}_view.png'))
                
                self.viewer.view_curves(*to_plot,
                                        sigmas=(sigma, ),
                                        title=f'Fits comparison for {basename}', xlabel='q (nm-1)', ylabel='I', legend=True,
                                        plotFilePath=os.path.join(bodies_subdir, f'{basename}_fits.png'))
        
        return bodies_subdir
    
    def dammif_fit(self, context: Context, saxs_1d_path, gnom_path, dest_dir, fast_forward=False):
        if gnom_path:
            self.interface.send_message('DAMMIF fit...')
            root, basename = os.path.split(gnom_path)
            basename, _ = os.path.splitext(basename)
            
            dammif_subdir = os.path.join(dest_dir, f'dammif_{basename}')
            os.makedirs(dammif_subdir, exist_ok=True)
            dammif_call = os.path.join(ATSAS_BIN_PREFIX, "dammif")
            dammif_prefix = os.path.join(dammif_subdir, 'dammif')
            dammif_reps_num = 2  # 5

            dammif_fits_png = os.path.join(dammif_subdir, f'{basename}_fits.png')
            
            exists_dammif = all(os.path.exists(os.path.join(dammif_subdir, f'dammif-{i}.fir')) for i in range(dammif_reps_num))
            
            if fast_forward and exists_dammif and os.path.exists(dammif_fits_png):
                self.interface.send_message(f'Debug mode: Skipping DAMMIF fit for {gnom_path} (results already exist)')
            
            else:
                os.system(f'for i in `seq 1 {dammif_reps_num}`; do {dammif_call} --prefix={dammif_prefix}-$i --mode=fast {gnom_path}; done')

                q, I, sigma, _ = read_saxs(saxs_1d_path)
                to_plot = [q, I, {'label': 'exp', 'lw': 4}]
                
                for i in range(dammif_reps_num):
                    fir_path = f'{dammif_prefix}-{i+1}.fir'
                    cif_path = f'{dammif_prefix}-{i+1}-1.cif'

                    data = np.loadtxt(fir_path, skiprows=1, dtype=np.float64)
                    q_fit, I_fit, sigma_dammif = data[:, 0], data[:, 3], data[:, 2]
                    q_fit = q_fit * 10.0  # from A^-1 to nm ^-1

                    # self.viewer.view_curves(q_fit, I_fit, 'fitted curve', 
                    #                         plotFilePath=os.path.join(dammif_subdir, f'{basename}_{i}_shit_here_0.png'))

                    idx_intersection = (q <= q_fit[-1])
                    q_intersetcion, I_intersection = q[idx_intersection], I[idx_intersection]
                    sigma_interp = np.interp(q_intersetcion, q_fit, sigma_dammif)
                    I_fit_interp = np.interp(q_intersetcion, q_fit, I_fit)

                    # self.viewer.view_curves(q_intersetcion, I_fit_interp, 'fitted curve', 
                    #                         plotFilePath=os.path.join(dammif_subdir, f'{basename}_{i}_shit_here_1.png'))

                    chi2 = calc_chi2(I_intersection, I_fit_interp, sigma_interp)
                    to_plot.extend([q_intersetcion, I_fit_interp, f'dammif-{i}; chi2: {chi2:.5f}'])

                    atoms = read_bodies_cif(cif_path)
                    # self.viewer.plot_structure_and_scattering(
                    #     atoms, q_intersetcion, I_intersection, sigma_interp, I_fit_interp, 
                    #     plotFilePath=os.path.join(dammif_subdir, f'dammif-{i}_view.png'))
                    self.viewer.plot_3d_views_and_scattering(
                        atoms, q_intersetcion, I_intersection, sigma_interp, I_fit_interp, 
                        plotFilePath=os.path.join(dammif_subdir, f'dammif-{i}_view.png'))
                
                self.viewer.view_curves(*to_plot,
                                        sigmas=(sigma, ), 
                                        title=f'Fits comparison for {basename}', xlabel='q (nm-1)', ylabel='I', legend=True,
                                        plotFilePath=os.path.join(dammif_subdir, f'{basename}_fits.png'))
        
        return dammif_subdir
    
    def ai_analysis(self, atsas_analysis_path, plot_paths, dest_dir,
                    text_model, vision_model,
                    fast_forward=False):
        answer = ''
        llm_answer_path = ''

        if atsas_analysis_path:
            os.makedirs(dest_dir, exist_ok=True)

            sub_plot_path, guinier_plot_path, kratky_plot_path, loglog_plot_path = plot_paths
            p, basename = os.path.split(sub_plot_path)
            basename, _ = os.path.splitext(basename)
            context_path = os.path.join(dest_dir, f'{basename}_context.txt')
            llm_answer_path = os.path.join(dest_dir, f'{basename}_llm_answer.txt')

            if fast_forward and os.path.exists(context_path):
                with open(context_path, 'r') as fread:
                    sample_context = fread.read()
                self.interface.send_message(f'Debug mode: Skipping visual analysis for {basename} (results already exist)')
            
            else:
                sample_context = []
                with open(atsas_analysis_path, 'r') as fread:
                    sample_context.append(f'{basename} sample analysis results:\n{fread.read()}')

                with open(os.path.join(PROMPTS_DIR, 'visual', 'saxs_1d.txt'), 'r') as fread:
                    saxs_prompt = fread.read()
                with open(os.path.join(PROMPTS_DIR, 'visual', 'guinier_plot.txt'), 'r') as fread:
                    guinier_prompt = fread.read()
                with open(os.path.join(PROMPTS_DIR, 'visual', 'kratky_plot.txt'), 'r') as fread:
                    kratky_prompt = fread.read()
                with open(os.path.join(PROMPTS_DIR, 'visual', 'loglog_plot.txt'), 'r') as fread:
                    loglog_prompt = fread.read()
                
                messages = get_image_messages(sub_plot_path, saxs_prompt)
                sub_description, _ = llm.send_request_to_llm(model=vision_model, messages=messages)
                sample_context.append(f'The description of 1d raw SAXS curve:\n{sub_description}')

                messages = get_image_messages(guinier_plot_path, guinier_prompt)
                guinier_description, _ = llm.send_request_to_llm(model=vision_model, messages=messages)
                sample_context.append(f'The description of Guinier plot:\n{guinier_description}')

                messages = get_image_messages(kratky_plot_path, kratky_prompt)
                kratky_description, _ = llm.send_request_to_llm(model=vision_model, messages=messages)
                sample_context.append(f'The description of Kratky plot:\n{kratky_description}')

                messages = get_image_messages(loglog_plot_path, loglog_prompt)
                loglog_description, _ = llm.send_request_to_llm(model=vision_model, messages=messages)
                sample_context.append(f'The description of log-log plot:\n{loglog_description}')

                sample_context = '\n\n'.join(sample_context)
                with open(context_path, 'w') as fwrite:
                    fwrite.write(sample_context)

            # LLM
            # I dont want to analyze each plot separately. If there are many plots, 
            # I would rather combine the information coming from them.
            if fast_forward and os.path.exists(llm_answer_path):
                with open(llm_answer_path, 'r') as fread:
                    answer = fread.read()
                self.interface.send_message(f'Debug mode: Skipping LLM analysis for {basename} (results already exist)')

            else:
                context = sample_context
                self.interface.send_message('Now the results of your data processing are sent to LLM for the intelligent analysis.')
                user_query = self.interface.ask_question('What is your query to LLM?')
                answer, _ = llm.send_request_to_llm(
                    model=text_model, 
                    messages=[
                        {'role': 'user', 'content': [{'type': 'text', 'text': f'{context}\n\nUser query: {user_query}'}]}
                    ],
                )
                with open(llm_answer_path, 'w') as fwrite:
                    fwrite.write(answer)
                self.interface.send_message(f'LLM asnwer:\n{answer}')
        
        return answer, llm_answer_path

    def pipeline_interactive(self, fast_forward=False):
        # TODO currently the pipeline is oriented on proteins. Since the pipeline for other samples is sort of similar, I think, there will be only one pipeline in the end

        model = 'GLM-4.6'
        # model = 'DeepSeek-V3.1'
        vision_model = 'GLM-4.5V'

        pipeline_choice, steps = get_pipeline_spec_gui()
        save_latest_steps(pipeline_choice, steps)

        descr, descr_path = get_pipeline_description(pipeline_choice)
        # print(descr)
        directory = self.interface.ask_for_file('Write a path to a directory for your data')

        config_path, = self.interface.wait_for_file(
            directory, 
            query='Upload config file config.conf to your directory',
            filepattern='config.conf',
            obligatory=True,
            skip_if_exists=True, allow_same_time=(1, 1)
        )

        context = Context(directory, descr_path, interface=self.interface)
        
        if 'calibration' in steps:
            calibrant_path, = self.interface.wait_for_file(
                directory, 
                query='Upload raw/*_calib.tif file with calibration data',
                filepattern='raw/*_calib.tif',
                skip_if_exists=True, allow_same_time=(1, 1)
            )
            res_calib = self.autocalib(
                calibrant_path, context=context, fast_forward=fast_forward)
            ai = res_calib['integrator']
        
        if 'integration' in steps and 'calibration' not in steps:
            ai_subdir = 'integrator_params'
            def exit_condition():
                return all(os.path.exists(os.path.join(directory, ai_subdir, p)) 
                for p in ['ai_params.json', 'detector_params.json', 'mask.npy']) 
            
            while not exit_condition(): 
                self.interface.send_message(
                    f'Integration requires calibrated geometry parameters and a mask.\n' 
                    f'Provide them by uploading directory named "{ai_subdir}" which contains:\n'
                    f'ai_params.json\n'
                    f'detector_params.json\n'
                    f'mask.npy\n'
                )
                self.interface.wait_for_file(
                    directory, 
                    query=f'Upload directory named "{ai_subdir}" to your working directory',
                    filepattern='integrator_params',
                    obligatory=True,
                    skip_if_exists=True, allow_same_time=(1, 1)
                )
                time.sleep(2.0)
                if not exit_condition():
                    self.interface.send_message(f'Wrong "{ai_subdir}" directory structure. Reupload')
            ai = IntegratorExtended.from_disk(os.path.join(directory, ai_subdir))

        run_load_cycle = True
        iteration_number = 0
        while run_load_cycle:
            buffer_path_1d = sample_path_1d = None
            basename_list = []
            if 'integration' in steps:
                buffer_paths = self.interface.wait_for_file(
                    directory,
                    query='Upload buffer 2d data to "raw" subdirectory raw/*_buffer.tif',
                    filepattern='raw/*_buffer.tif',
                    skip_if_exists=iteration_number==0)
                
                sample_paths = self.interface.wait_for_file(
                    directory, 
                    query='Upload sample 2d data to "raw" subdirectory raw/*_sample.tif',
                    filepattern='raw/*_sample.tif',
                    skip_if_exists=iteration_number==0)
                if sample_paths:
                    basename_list = [
                        os.path.splitext(os.path.split(sample_path)[1])[0]
                        for sample_path in sample_paths
                    ]
            
                buffer_paths_1d = [
                    self.integrate(
                        ai, context, buffer_path, 
                        dest_dir=os.path.join(directory, 'averaged'), metadata={'type': 'buffer'}, fast_forward=fast_forward)
                        for buffer_path in buffer_paths
                    ]
                sample_paths_1d = [
                    self.integrate(
                        ai, context, 
                        sample_path, dest_dir=os.path.join(directory, 'averaged'), 
                        metadata={'type': 'sample'}, 
                        fast_forward=fast_forward)
                        for sample_path in sample_paths
                    ]
                # print('DEBUG: integration finished')

            if 'subtraction' in steps and 'integration' not in steps:
                buffer_paths_1d = self.interface.wait_for_file(
                    directory,
                    query='Upload buffer 1d data to "averaged" subdirectory averaged/*_buffer.dat', 
                    filepattern='averaged/*_buffer.dat',
                    skip_if_exists=iteration_number==0
                    )            
                sample_paths_1d = self.interface.wait_for_file(
                    directory,
                    query='Upload sample 1d data to "averaged" subdirectory averaged/*_sample.dat', 
                    filepattern='averaged/*_sample.dat',
                    skip_if_exists=iteration_number==0)
                if sample_paths_1d:
                    basename_list = [
                        os.path.splitext(os.path.split(sample_path)[1])[0]
                        for sample_path in sample_paths_1d
                    ]            
            
            profile_paths = []
            profile_pic_paths = []
            if 'subtraction' in steps:
                # print('DEBUG: subtraction started')

                # Align sample and buffer 1D paths by base name, matching sample name containing buffer name
                # name convention - buffer path ends with "_buffer.data", sample path wiht "_sample.dat"
                aligned_pairs = []
                for sample_path in sample_paths_1d:
                    sample_base = os.path.basename(sample_path).replace('_sample.dat', '')
                    for buffer_path in buffer_paths_1d:
                        buffer_base = os.path.basename(buffer_path).replace('_buffer.dat', '')
                        if buffer_base in sample_base:
                            aligned_pairs.append((sample_path, buffer_path))
                    assert len(aligned_pairs) == len(sample_paths_1d)

                for s_p, b_p in aligned_pairs:
                    profile_path, profile_pic_path = self.subtract(
                        context, s_p, b_p, 
                        dest_dir=os.path.join(directory, 'subtracted'), fast_forward=fast_forward)
                    profile_paths.append(profile_path)
                    profile_pic_paths.append(profile_pic_path)
                # print('DEBUG: subtraction finished')
            else:
                # print('DEBUG: subtraction ommited')
                profile_paths = self.interface.wait_for_file(
                    directory, 
                    query='Upload sample data to "subtracted" subdirectory subtracted/*.dat', 
                    filepattern='subtracted/*.dat',
                    skip_if_exists=iteration_number==0
                    )
                if profile_paths:
                    for profile_path in profile_paths:
                        root, filename = os.path.split(profile_path)
                        basename, _ = os.path.splitext(filename)
                        profile_pic_path = os.path.join(root, f'{basename}.png')                
                        q, I, sigma, _ = read_saxs(profile_path)
                        self.viewer.view_curves(q, I, basename,
                                                sigmas=(sigma,),
                                                xlabel='q, (nm-1)', ylabel='I, (a.u.)',
                                                title=f'{basename} SAXS profile',
                                                show_duration=None, save=False,
                                                plotFilePath=profile_pic_path)
                        basename_list.append(basename)
                        profile_pic_paths.append(profile_pic_path)
                # print('DEBUG: profile loading and plotting finished')

            profiles_data = []
            for basename, (idx, profile_path), plot_path in zip(
                basename_list, enumerate(profile_paths), profile_pic_paths):
                q, I, _, metadata = read_saxs(profile_path)
                profiles_data.append(
                    {
                        'basename': basename,
                        'path': profile_path,
                        'q': q,
                        'I': I,
                        'metadata': metadata,
                        'plot_path': plot_path,
                    }
                )

            selected_profiles = choose_profiles(profiles_data)

            for basename, profile in selected_profiles.items():
                profile_path = profile['path']
                profile_pic_path = profile.get('plot_path')

                if 'simple_analysis' in steps:
                    atsas_res_path, gnom_path = self.get_descriptors(
                        context, profile_path, dest_dir=os.path.join(directory, 'descriptors'), fast_forward=fast_forward)
                if 'plots' in steps:
                    plot_paths = self.plot(context, profile_path, dest_dir=os.path.join(directory, 'plots'), fast_forward=fast_forward)
                    plot_paths = [profile_pic_path, ] + plot_paths
                if 'bodies' in steps:
                    self.bodies_fit(context, profile_path, dest_dir=os.path.join(directory, 'bodies'), fast_forward=fast_forward)
                if 'dammif' in steps:
                    assert 'simple_analysis' in steps
                    self.dammif_fit(
                        context, profile_path, gnom_path, dest_dir=os.path.join(directory, 'dammif'), fast_forward=fast_forward)
                # self.ai_analysis(atsas_res_path, plot_paths, directory, text_model=model, vision_model=vision_model)
            
            upload_more = self.interface.ask_question(
                'Upload more data? Type "no" to exit program, type Enter or "yes" to continue',
            )
            run_load_cycle = not upload_more.lower().startswith('n')
    
    def pipeline_batch(self, fast_forward=False):
        # TODO currently the pipeline is oriented on proteins. Since the pipeline for other samples is sort of similar, I think, there will be only one pipeline in the end

        model = 'GLM-4.6'
        # model = 'DeepSeek-V3.1'
        vision_model = 'GLM-4.5V'
        
        descr, descr_path = get_pipeline_description('protein_v0')
        # print(descr)
        directory = self.interface.ask_for_file('Write a path to a directory for your data')

        context = Context(directory, descr_path, interface=self.interface)
        
        if context['paths', 'calib_2d']:
            starting_point = 0
        elif context['paths', 'buffer_1d'] and context.paths['sample_1d']:
            starting_point = 1
        elif context['paths', 'sub'] and context['paths', 'sub_picture']:
            starting_point = 2
        else:
            raise RuntimeError('Data for pipeline was not provided')
        
        ai = None
        if starting_point < 1:
            assert context['paths', 'buffer_2d'] and context.paths['sample_2d']
            calibrant_path = context['paths', 'calib_2d', 0]
            res_calib = self.autocalib(
                calibrant_path, context=context, fast_forward=fast_forward)
            ai = res_calib['integrator']

            # TODO for now integrated go directly to "averaged" subdir, should be fixed in the future
            for p in context['paths', 'buffer_2d']:
                int_p = self.integrate(
                    ai, context, p, metadata={'type': 'buffer'}, 
                    dest_dir=os.path.join(directory, 'averaged'), 
                    fast_forward=fast_forward)
                context.append_path('buffer_1d', int_p)
            
            for p in context['paths', 'sample_2d']:
                int_p = self.integrate(
                    ai, context, p, metadata={'type': 'sample'}, 
                    dest_dir=os.path.join(directory, 'averaged'), 
                    fast_forward=fast_forward)
                context.append_path('sample_1d', int_p)
            
        if starting_point < 2:
            for b_path, s_path in zip(context['paths', 'buffer_1d'], context['paths', 'sample_1d']):
                sub_path, sub_pic_path = self.subtract(
                    context, s_path, b_path, 
                    dest_dir=os.path.join(directory, 'subtracted'), fast_forward=fast_forward
                )
                context.append_path('sub', sub_path)
                context.append_path('sub_picture', sub_pic_path)
        
        for p in context['paths', 'sub']:
            atsas_res_path, gnom_path = self.get_descriptors(
                context, p, 
                dest_dir=os.path.join(directory, 'descriptors'), fast_forward=fast_forward)
            context.append_path('astas_analysis', atsas_res_path)
            context.append_path('p(R)', gnom_path)
        
        for sub_path, sub_pic_path in zip(context['paths', 'sub'], context['paths', 'sub_picture']):
            plot_paths = self.plot(
                context, sub_path, dest_dir=os.path.join(directory, 'plots'), fast_forward=fast_forward)
            plot_paths = [sub_pic_path, ] + plot_paths
            context.append_path('plot', plot_paths)
        
        if context['fit_bodies']:
            for p in context['paths', 'sub']:
                self.bodies_fit(
                    context, p, os.path.join(directory, 'bodies'), fast_forward=fast_forward
                )
        
        if context['fit_dammif']:
            for sub_path, gnom_path in zip(context['paths', 'sub'], context['paths', 'p(R)']):
                self.dammif_fit(
                    context, sub_path, gnom_path, os.path.join(directory, 'dammif'),
                    fast_forward=fast_forward
                )
        
        # if context['analyze_with_ai']:
        #     self.ai_analysis(atsas_res_path, plot_paths, directory, text_model=model, vision_model=vision_model)
    
    # def protein_v0(self, fast_forward=False):
    #     model = 'GLM-4.6'
    #     # model = 'DeepSeek-V3.1'
    #     vision_model = 'GLM-4.5V'
        
    #     descr, descr_path = get_pipeline_description('protein_v0')
    #     # print(descr)
    #     directory = self.interface.ask_for_file('Write a path to a directory for your data')
    #     online_or_offline = self.interface.ask_question(
    #         'Do you want to run a pipeline in "online" or "offline" mode? Type 1 for "online" mode and "2" for "offline" mode'
    #     )
    #     data_load_mode = 'online' if online_or_offline.startswith('1') else 'offline'
    #     self.interface.send_message(f'Interaction mode is set to {data_load_mode}')

    #     context = Context(directory, descr_path, interface=self.interface)
        
    #     calibrant_path = context.get_path(
    #         'calib_2d', 
    #         query='Drop raw/*_calib.tif file with calibration data your directory',
    #         pattern='raw/*_calib.tif',
    #         interaction_mode=data_load_mode
    #     )
    #     res_calib = self.autocalib(
    #         calibrant_path, context=context, fast_forward=fast_forward)
    #     ai = res_calib['integrator']

    #     run_load_cycle = True
    #     buffer_loaded = False
    #     while run_load_cycle:
    #         buffer_path = context.get_path(
    #             'buffer_2d', 
    #             query='Drop buffer 2d data raw/*_buffer.tif to the directory',
    #             pattern='raw/*_buffer.tif',
    #             interaction_mode=data_load_mode)
    #         if not buffer_path and buffer_loaded:
    #             buffer_path = context.paths['buffer_2d'][-1]
    #         else:
    #             buffer_loaded = True
            
    #         sample_path = context.get_path(
    #             'sample_2d', query='Drop sample 2d data raw/*_sample.tif to the directory',
    #             pattern='raw/*_sample.tif',
    #             interaction_mode=data_load_mode)
    #         if sample_path:
    #             basename, _ = os.path.splitext(os.path.split(sample_path)[1])
            
    #         buffer_path_1d = self.integrate(
    #             ai, context, 
    #             buffer_path, dest_dir=os.path.join(directory, 'int'), 
    #             metadata={'type': 'buffer'}, 
    #             fast_forward=fast_forward)
    #         sample_path_1d = self.integrate(
    #             ai, context, 
    #             sample_path, dest_dir=os.path.join(directory, 'int'), 
    #             metadata={'type': 'sample'}, 
    #             fast_forward=fast_forward)
    #         if sample_path_1d and buffer_path_1d:
    #             # print('Paths are added to context')
    #             context.append_path('buffer_1d', buffer_path_1d)
    #             context.append_path('sample_1d', sample_path_1d)
    #             # print('Check paths', context.paths['buffer_1d'])
    #             # print('Check paths', context.paths['sample_1d'])
             
    #         # print('Check paths 0:', buffer_path_1d, sample_path_1d)

    #         load_mode_1d = 'online' if data_load_mode == 'online' and not sample_path_1d and not buffer_path_1d else 'offline'
    #         # print('Check iterators:', context.path_iterators)
    #         buffer_path_1d = context.get_path(
    #             'buffer_1d', query='Drop buffer 1d data int/*_buffer.dat to the directory', pattern='int/*_buffer.dat',
    #             interaction_mode=load_mode_1d)            
    #         sample_path_1d = context.get_path(
    #             'sample_1d', query='Drop sample 1d data int/*_sample.dat to the directory', pattern='int/*_sample.dat',
    #             interaction_mode=load_mode_1d)            
    #         # print('Check paths 1', buffer_path_1d, sample_path_1d)
    #         profile_path, profile_pic_path = self.subtract(
    #             context, sample_path_1d, buffer_path_1d, 
    #             directory, fast_forward=fast_forward)
    #         if profile_path:
    #             context.append_path('sub', profile_path)

    #         # profile_path = self.scale(...)
            
    #         load_mode_1d = 'online' if data_load_mode == 'online' and not profile_path else 'offline'
    #         profile_path = context.get_path(
    #             'sub', query='Drop sample data sub/*.dat to the directory', pattern='sub/*.dat',
    #             interaction_mode=load_mode_1d)

    #         if_file_is_good = 'yes'
    #         if data_load_mode == 'online':
    #             q, I, _ = read_saxs(profile_path)
    #             self.viewer.view_curves(q, I, basename,
    #                                     xlabel='q, (nm-1)', ylabel='I, (a.u.)',
    #                                     title=f'{basename} SAXS profile',
    #                                     show=True)
    #             if_file_is_good = self.interface.ask_question(
    #                 f'Should I continue to analyze {basename} SAXS profile? type Enter to proceed, type "No" to skip'
    #             )
            
    #         if not if_file_is_good.lower().startswith('n'):
    #             atsas_res_path, gnom_path = self.get_descriptors(context, profile_path, directory, fast_forward=fast_forward)
    #             context.append_path('atsas_analysis', atsas_res_path)
    #             context.append_path('p(R)', gnom_path)
                
    #             plot_paths = self.plot(context, profile_path, directory, fast_forward=fast_forward)
    #             plot_paths = [profile_pic_path, ] + plot_paths
    #             context.append_path('plots', plot_paths)
                
    #             self.fit_geometry(context, profile_path, gnom_path, directory, fast_forward=fast_forward)
    #             # self.ai_analysis(atsas_res_path, plot_paths, directory, text_model=model, vision_model=vision_model)
        
        # # self.interface.send_message('Integration...')
        # paths['buffer_1d'], = self.integrate(ai, [paths['buffer_2d'], ], directory, [{'type': 'buffer'}, ], debug=debug)
        # paths['sample_1d'] = self.integrate(
        #     ai, paths['sample_2d'], directory, [{'type': 'sample'} for _ in range(len(paths['sample_2d']))], debug=debug)
        
        # # self.interface.send_message('Subtraction...')
        # paths['sample_sub'] = self.subtract(paths['sample_1d'], paths['buffer_1d'], dest_dir=directory,
        #                                     config_sub=config['sub'])
        
        # # TODO scaling step

        # # self.interface.send_message('Calculating the parameters...')
        # paths['atsas_analysis'], paths['p(R)']  = self.get_descriptors(paths['sample_sub'], dest_dir=directory, debug=debug)

        # paths['plots'] = self.plot(paths['sample_sub'], dest_dir=directory, debug=debug)

        # self.fit_geometry(paths['sample_sub'], paths['p(R)'], dest_dir=directory, debug=debug)

        # self.ai_analysis(paths['atsas_analysis'], paths['plots'], dest_dir=directory, 
        #                  text_model=model, vision_model=vision_model, debug=debug)
    
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
    controller = Controller(CLIInterface(), PLTViewer())
    # controller.pipeline()
    # directory path for pipeline0: debug/protein_v0, debug/protein_v0_interactive
    # LLM query: It is known that the subject of the investigation is a protein dissolved in water. Which protein it could be based on available information?
    # controller.protein_v0(fast_forward=True)
    controller.pipeline_interactive(fast_forward=True)
    # controller.pipeline_batch(fast_forward=True)

