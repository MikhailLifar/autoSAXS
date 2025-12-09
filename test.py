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

sys.path.append(os.path.expanduser('~/LLM/LLMAssistant'))
sys.path.append(os.path.expanduser('~/LLM/LLMAssistant/aiAssistantFramework'))

from aiAssistantFramework import lib as ai_lib
from aiAssistantFramework.lib import llm, telegram
import controller as ai_controller

from processor import *
from viewer import *


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
    data = read_from_tiff('debug/data/raw/0001_AgBh700_96.9_calib.tif')
    center_y_px, center_x_px = 0.06070 / 1.e-4, 0.04949 / 1.e-4
    r_beam_px = 35

    mask = calc_beam_abnormal_mask(
        data, center_y_px, center_x_px, r_beam_px=r_beam_px
    )

    fig, ax = plt.subplots()
    im = ax.imshow(mask, cmap='gray')
    ax.set_title("Beam + Abnormal Pixels Mask")
    # plt.show()
    fig.savefig('debug/mask_debug.png', dpi=400)


if __name__ == '__main__':
    # visual_model_test()
    # view_3d_projections_test()
    automask_test()
