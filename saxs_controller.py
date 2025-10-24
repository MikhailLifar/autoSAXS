import yaml
from processor import *
from interface import *
from viewer import *
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

from aiAssistantFramework import lib as ai_lib
from aiAssistantFramework.lib import llm, telegram
import controller as ai_controller

ATSAS_BIN_PREFIX = os.path.expanduser('~/ATSAS-3.2.1-1/bin')
# CONFIG_FILE = "calib_config.conf"
# CALIBRATED_GEOMETRY_PATH = 'calibrated_geometry.conf'
ROOT_DIR = os.path.expanduser('~/KurchatovCoop')
PROMPTS_DIR = os.path.join(ROOT_DIR, 'repos', 'prompts')
DEBUG = True

BODIES_SHAPES = (
    'cylinder', 'dumbbell', 'ellipsoid', 
    'elliptic-cylinder', 'hollow-cylinder', 'hollow-sphere',
    'parallelepiped', 'rotation-ellipsoid'
)

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

    def autocalib(self, directory, calibrant_path, config, config_path, debug=False):
        # Check if calibration results exist in debug mode
        calibration_results_file = os.path.join(directory, 'calibration.png')
        integrator_subd = os.path.join(directory, 'integrator_params')
        refined_config_exists = 'refined' in config
        
        calibrant_name = config['calibrant_name']
        calib_data = read_from_tiff(calibrant_path)
        
        if debug and refined_config_exists and all(
            os.path.exists(p) for p in (calibration_results_file, integrator_subd)
        ):
            self.interface.send_message('Debug mode: Skipping calibration (results already exist)')
            refined = config['refined']
            integrator = IntegratorExtended.from_disk(integrator_subd)
            return {'integrator': integrator, 'refined': refined}
        else:
            self.interface.send_message('Autocalibration...')

            center_ref_params = {k: config['center_refinement'][k] 
                                for k in ['q_start', 'q_stop', 'min_segment_len']}
            self.interface.send_message('    Center search...')
            center_step_ret = self.center_refinement_step(calib_data, visualize=False, calib_tiff_path=calibrant_path, **center_ref_params)
            
            d_geom = config['detector_geometry']
            interring_dist_px = get_interring_dist_px(
                d_geom['dist'], d_geom['wavelength'], d_geom['pixel_size'][0]
            )

            ring_search_params = {k: config['ring_search'][k] 
                                  for k in ['q_stop', 'ring_I_threshold', 'r_max_px', 'r_step_px']}
            ring_search_params.update({
                'r_beam_px': config['r_beam_px'],
                'center_y_px': center_step_ret['center_y_px'],
                'center_x_px': center_step_ret['center_x_px'],
                'interring_dist_px': interring_dist_px
            })
            self.interface.send_message('    Rings identification...')
            rings_step_ret = self.rings_refinement_step(calib_data, visualize=False, calib_tiff_path=calibrant_path, **ring_search_params)
            
            geometry_params = {k: config['detector_geometry'][k] 
                                for k in ['dist', 'wavelength', 'pixel_size', 'rot1', 'rot2', 'rot3']}
            geometry_params.update({
                'r_beam_px': config['r_beam_px'],
                'center_y_px': center_step_ret['center_y_px'],
                'center_x_px': center_step_ret['center_x_px'],
                'calibrant_name': calibrant_name,
            })
            self.interface.send_message('    Geometry refinement...')
            refine_step_ret = self.geometry_refinement_step(
                calib_data, rings_step_ret['rings'], visualize=False, **geometry_params)
            
            refine_step_ret['integrator'].to_disk(integrator_subd)

            self.viewer.view_calibration(
                img_data=calib_data, tiff_path=calibrant_path,
                show=False, plotFilePath=calibration_results_file,
                **center_step_ret, **rings_step_ret, **refine_step_ret)
            refined = refine_step_ret['refined']
            refined.update({'wavelength': config['detector_geometry']['wavelength']})
            self.interface.send_message(
                f'\n-- Calibrated geometry parameters --\n' + '\n'.join(f'{p}: {v}' for p, v in refined.items())  + '\n'
            )
            self.interface.send_message('Finished calibration')
            update_config(config, config_path, 'refined', values=refined)
            return {k: refine_step_ret[k] for k in ('refined', 'integrator')}
        
    def integrate(self, ai, to_int_paths, dest_dir, metadata, debug=False):
        integrated_paths = []
        for p, meta in zip(to_int_paths, metadata):
            root, fname = os.path.split(p)
            fname = os.path.splitext(fname)[0]
            destpath = os.path.join(dest_dir, f'int_{fname}.dat')
            
            # Check if integration results exist in debug mode
            if debug and os.path.exists(destpath):
                self.interface.send_message(f'Debug mode: Skipping integration for {p} (results already exist)')
                integrated_paths.append(destpath)
                continue
                
            integrate_2d_to_1d(ai, read_from_tiff(p), destpath=destpath,
                               metadata={'type': 'sample'})
            integrated_paths.append(destpath)
        
        return integrated_paths
    
    def subtract(self, to_sub_paths, buffer_path, dest_dir, config_sub, debug=False):
        subtracted_paths = []
        for p in to_sub_paths:
            q, sample, _ = read_saxs(p)
            root, basename = os.path.split(p)
            basename, _ = os.path.splitext(basename)
            basename = basename.replace('int_', '', 1)
            destpath = os.path.join(dest_dir, f"sub_{basename}.dat")
            diff_plot_path = os.path.join(dest_dir, f'diff_{basename}.png')
            sub_plot_path = os.path.join(dest_dir, f'sub_{basename}.png')
            
            # Check if subtraction results exist in debug mode
            if debug and all(os.path.exists(p) for p in (destpath, diff_plot_path, sub_plot_path)):
                self.interface.send_message(f'Debug mode: Skipping subtraction for {p} (results already exist)')
                subtracted_paths.append(destpath)
                continue
                
            _, I_sub, I_buff_scaled = subtract_buffer(
                buffer_path, p, destpath, match_tail_ops={'q_range_rel': None, 'q_range_abs': config_sub['q_range_abs'], })
            self.viewer.view_curves(
                q, sample, 'sample',
                q, I_buff_scaled, 'buffer scaled',
                legend=True,
                plotFilePath=diff_plot_path,
                save=False
            )
            self.viewer.view_curves(
                q, I_sub, 'sample',
                legend=True,
                plotFilePath=sub_plot_path,
                save=False
            )
            subtracted_paths.append(destpath)
        return subtracted_paths
    
    def get_descriptors(self, to_analyze_paths, dest_dir, debug=False):
        analyzis_res_paths = []
        for p in to_analyze_paths:
            root, basename = os.path.split(p)
            basename, _ = os.path.splitext(basename)
            results_file = os.path.join(dest_dir, f'{basename}_results.txt')
            
            # Check if analysis results exist in debug mode
            if debug and os.path.exists(results_file):
                self.interface.send_message(f'Debug mode: Skipping analysis for {p} (results already exist)')
                analyzis_res_paths.append(results_file)
                continue
                
            os.system(f'''INPUT_FILE={p}
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
            analyzis_res_paths.append(results_file)
        
        return analyzis_res_paths
    
    def plots(self, to_plot_paths, dest_dir, debug=False):
        plot_paths = []
        for p in to_plot_paths:
            root, basename = os.path.split(p)
            basename, _ = os.path.splitext(basename)
            sub_plot_path = os.path.join(root, f'{basename}.png')
            guinier_plot_path = os.path.join(dest_dir, f'guinier_{basename}.png')
            kratky_plot_path = os.path.join(dest_dir, f'kratky_{basename}.png')
            loglog_plot_path = os.path.join(dest_dir, f'loglog_{basename}.png')
            
            if debug and all(os.path.exists(p) for p in (sub_plot_path, guinier_plot_path, kratky_plot_path, loglog_plot_path)):
                self.interface.send_message(f'Debug mode: Skipping plots for {p} (results already exist)')
                continue

            # plots
            q, I, _ = read_saxs(p)

            self.viewer.view_curves(
                q*q, np.log(I), 'log(I) vs q^2',
                xlabel='q^2 (nm-2)', ylabel='log(I) (a.u.)',
                legend=True,
                plotFilePath=guinier_plot_path,
                save=False
            )

            self.viewer.view_curves(
                q, q * q * I, 'I * q^2 vs q',
                xlabel='q (nm-1)', ylabel='I * q^2 (a.u.)',
                legend=True,
                plotFilePath=kratky_plot_path,
                save=False
            )

            self.viewer.view_curves(
                np.log(q), np.log(I), 'log(I) vs log(q)',
                xlabel='log(q)', ylabel='log(I)',
                legend=True,
                plotFilePath=loglog_plot_path,
                save=False
            )

            plot_paths.append([sub_plot_path, guinier_plot_path, kratky_plot_path, loglog_plot_path])
        
        return plot_paths
    
    def fit_geometry(self, to_fit_paths, dest_dir, debug=False):
        self.interface.send_message('Fitting with shapes...')
        for p in to_fit_paths:
            root, basename = os.path.split(p)
            basename, _ = os.path.splitext(basename)
            bodies_subdir = os.path.join(dest_dir, f'bodies_{basename}')
            os.makedirs(bodies_subdir, exist_ok=True)
            bodies_call = os.path.join(ATSAS_BIN_PREFIX, 'bodies')
            file_prefix = os.path.join(bodies_subdir, 'bodies_fit')
            
            if debug and all(os.path.exists(os.path.join(bodies_subdir, f'bodies_fit-{shape}.fir')) for shape in BODIES_SHAPES):
                self.interface.send_message(f'Debug mode: Skipping BODIES fit for {p} (results already exist)')
                continue

            os.system(f"{bodies_call} --prefix={file_prefix} {p}")

            q, I, _ = read_saxs(p)
            to_plot = [q, I, {'label': 'exp', 'lw': 4}]
            for shape in BODIES_SHAPES:
                fir_path = os.path.join(bodies_subdir, f'bodies_fit-{shape}.fir')
                # cif_path = os.path.join(bodies_subdir, f'bodies_fit-{shape}-damstart.cif')

                data = np.loadtxt(fir_path, skiprows=1, dtype=np.float64)
                q_fit, I_fit, sigma_exp = data[:, 0], data[:, 3], data[:, 2]
                idx_intersection = (q <= q_fit[-1])
                q_intersetcion, I_intersection = q[idx_intersection], I[idx_intersection]
                I_fit_interp = np.interp(q_intersetcion, q_fit, I_fit)
                sigma_interp = np.interp(q_intersetcion, q_fit, sigma_exp)

                chi2 = calc_chi2(I_intersection, I_fit_interp, sigma_interp)
                to_plot.extend([q_intersetcion, I_fit_interp, f'{shape}; chi2: {chi2:.5f}'])

                # atoms = read_bodies_cif(cif_path)
                # self.viewer.plot_structure_and_scattering(
                #     atoms, q_intersetcion, I_intersection, sigma_interp, I_fit_interp, 
                #     plotFilePath=os.path.join(bodies_subdir, f'{shape}_view.png'))
            
            self.viewer.view_curves(*to_plot,
                                    title=f'Fits comparison for {basename}', xlabel='q (nm-1)', ylabel='I', legend=True,
                                    plotFilePath=os.path.join(bodies_subdir, f'{basename}_fits.png'))
    
    def ai_analysis(self, atsas_analysis_paths, plots_paths, dest_dir,
                    text_model, vision_model,
                    debug=False):
        context = []
        for results_file, plots in zip(atsas_analysis_paths, plots_paths):
            sub_plot_path, guinier_plot_path, kratky_plot_path, loglog_plot_path = plots
            p, basename = os.path.split(sub_plot_path)
            basename, _ = os.path.splitext(basename)
            
            sample_context = []
            with open(results_file, 'r') as fread:
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
            with open(os.path.join(dest_dir, f'{basename}_context.txt'), 'w') as fwrite:
                fwrite.write(sample_context)
            context.append(sample_context)

        # LLM
        # I dont want to analyze each plot separately. If there are many plots, 
        # I would rather combine the information coming from them.
        if len(context) == 1:  
            context, = context
            self.interface.send_message('Now the results of your data processing are sent to LLM for the intelligent analysis.')
            user_query = self.interface.ask_question('What is your query to LLM?')
            answer, _ = llm.send_request_to_llm(
                model=text_model, 
                messages=[
                    {'role': 'user', 'content': [{'type': 'text', 'text': f'{context}\n\nUser query: {user_query}'}]}
                ],
            )
            with open(os.path.join(dest_dir, f'{basename}_llm_answer.txt'), 'w') as fwrite:
                fwrite.write(answer)
            self.interface.send_message(f'LLM asnwer:\n{answer}')

    def pipeline0(self, debug=False):
        model = 'GLM-4.5'
        # model = 'DeepSeek-V3.1'
        vision_model = 'GLM-4.5V'
        
        descr, descr_path = get_pipeline_description('pipeline0')
        print(descr)
        directory = self.interface.ask_for_file('Write a path to a directory for your data')
        paths = get_necessary_paths(descr_path, directory)
        config = load_config(paths['config'])

        res_calib = self.autocalib(directory, paths['calib_2d'], config=config, config_path=paths['config'], debug=debug)
        ai = res_calib['integrator']
        
        self.interface.send_message('Integration...')
        paths['buffer_1d'], = self.integrate(ai, [paths['buffer_2d'], ], directory, [{'type': 'buffer'}, ], debug=debug)
        paths['sample_1d'] = self.integrate(
            ai, paths['sample_2d'], directory, [{'type': 'sample'} for _ in range(len(paths['sample_2d']))], debug=debug)
        
        self.interface.send_message('Subtraction...')
        paths['sample_sub'] = self.subtract(paths['sample_1d'], paths['buffer_1d'], dest_dir=directory,
                                            config_sub=config['sub'])
        
        # TODO scaling step

        self.interface.send_message('Calculating the parameters...')
        paths['atsas_analysis']  = self.get_descriptors(paths['sample_sub'], dest_dir=directory, debug=debug)

        paths['plots'] = self.plots(paths['sample_sub'], dest_dir=directory, debug=debug)

        self.fit_geometry(paths['sample_sub'], dest_dir=directory, debug=debug)

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
    # directory path for pipeline0: debug/pipeline0
    # LLM query: It is known that the subject of the investigation is a protein dissolved in water. Which protein it could be based on available information?
    controller.pipeline0(debug=True)

