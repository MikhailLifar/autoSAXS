import yaml
import os
import sys
import logging
import warnings
import json
import itertools as it

import numpy as np
import pandas as pd
import matplotlib as mpl
import matplotlib.pyplot as plt
import seaborn as sns

sys.path.append(os.path.expanduser('~/LLM/LLMAssistant'))
sys.path.append(os.path.expanduser('~/LLM/LLMAssistant/aiAssistantFramework'))

from aiAssistantFramework import lib as ai_lib
from aiAssistantFramework.lib import llm, telegram
import controller as ai_controller

from utils import timer
from processor import *
from interface import *
from viewer import *


setPlotDefaults()


def visual_model_test():
    model = 'GLM-4.5V'
    image_path = 'debug/pipeline0/sub_0002_ihs27_95.9.png'
    text = 'Describe the shape of the 1D SAXS data in this plot. Focus on the overall curve characteristics, peak positions, and any notable features.'

    messages = get_image_messages(image_path, text)
    answer, tokens = llm.send_request_to_llm(model=model, messages=messages)

    print(answer)


def view_3d_projections_test():
    saxs_1d_path = 'debug/protein_v0/sub_0002_ihs27_95.9.dat'
    dammif_prefix = 'debug/protein_v0/dammif_sub_0002_ihs27_95.9/dammif'
    fir_path = f'{dammif_prefix}-1.fir'
    cif_path = f'{dammif_prefix}-1-1.cif'

    viewer = PLTViewer()

    q, I, _ = read_saxs(saxs_1d_path)

    data = np.loadtxt(fir_path, skiprows=1, dtype=np.float64)
    q_fit, I_fit, sigma_exp = data[:, 0], data[:, 3], data[:, 2]
    q_fit = q_fit * 10.0  # from A^-1 to nm ^-1

    # self.viewer.view_curves(q_fit, I_fit, 'fitted curve', 
    #                         plotFilePath=os.path.join(dammif_subdir, f'{basename}_{i}_shit_here_0.png'))

    idx_intersection = (q <= q_fit[-1])
    q_intersetcion, I_intersection = q[idx_intersection], I[idx_intersection]
    I_fit_interp = np.interp(q_intersetcion, q_fit, I_fit)
    sigma_interp = np.interp(q_intersetcion, q_fit, sigma_exp)

    # self.viewer.view_curves(q_intersetcion, I_fit_interp, 'fitted curve', 
    #                         plotFilePath=os.path.join(dammif_subdir, f'{basename}_{i}_shit_here_1.png'))

    chi2 = calc_chi2(I_intersection, I_fit_interp, sigma_interp)

    atoms = read_bodies_cif(cif_path)
    # self.viewer.plot_structure_and_scattering(
    #     atoms, q_intersetcion, I_intersection, sigma_interp, I_fit_interp, 
    #     plotFilePath=os.path.join(dammif_subdir, f'dammif-{i}_view.png'))
    viewer.plot_3d_views_and_scattering_with_isosurface(
        atoms, q_intersetcion, I_intersection, sigma_interp, I_fit_interp, 
        plotFilePath=os.path.join('debug', '3d_view.png'))


def automask_test():
    calib_data = read_from_tiff('debug/data/raw/0001_AgBh700_96.9_calib.tif')
    sample_data = read_from_tiff('0002_ihs27_95.9_sample.tif')
    integrator = IntegratorExtended.from_disk(os.path.join('data', 'debug', 'integrator_params'))
    
    center_y_px, center_x_px = 318, 598
    r_beam_px = 35

    windows = [7, 9, 15, 21]
    tols = [1.5, 2.0, 3.0]
    times = [[] for _ in windows]
    for window, tol in it.product(windows, tols):
        with timer("mask calculation") as t:
            mask = calc_beam_abnormal_mask(
                calib_data, center_y_px, center_x_px, r_beam_px=r_beam_px, window_size=window, iqr_tol=tol,
            )

        times[windows.index(window)].append(t['elapsed'])
        basic_imshow(
            mask, cmap='gray', 
            xlabel='X',ylabel='Y', title=f"Beam + Abnormal Pixels Mask\nwindow: {window}; tol: {tol}", 
            plotFilePath=os.path.join('debug', 'test_automask', f'{window}_{tol}.png'),
            save=False)
    
    times = [np.mean(row) for row in times]
    plotLines(
        windows, times, 'exec time vs window size',
        title='Mask calculation time vs window size', 
        plotFilePath=os.path.join('debug', 'test_automask', f'exec_time_vs_window_size.png'),
        save=False)


if __name__ == '__main__':
    # visual_model_test()
    # view_3d_projections_test()
    automask_test()
