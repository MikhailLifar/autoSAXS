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
from polydispfit import polydispfit
from processor import *
from cli_interface import CLIInterface, PipelineInterrupt
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


# def sasmodels_fit_test():
#     data_path = 'debug/data/subtracted/sub_ihs27_sample.dat'
#     model = 'sphere'

#     # fitted, raw_output = run_primus_fit(data_path, model, q_min=0.01, q_max=0.4)
#     fitted = run_bumps_fit(model, data_path, q_min=0.01, q_max=0.4)

#     # print(f'Raw output:', raw_output)
#     print(f'Fit results:', fitted)


def polydispfit_test():
    """
    Fit a 1D SAXS dataset with a polydisperse sphere model and visualize results,
    including the resulting distribution of sizes.
    Uses the polydispfit function defined in polydispfit.py.
    """
    data_path = 'debug/data/subtracted/sub_ihs27_sample.dat'
    model_name = 'sphere'
    q_range = (0.01, 5.0)

    # Gaussian radius distribution as a reasonable starting point
    distribution = {
        "name": "gaussian",
        "params": {"mean": 3.0, "std": 0.5},
        "bounds": {"mean": (0.5, 10.0), "std": (0.05, 3.0)},
    }

    fit_res = polydispfit(data_path, model_name, distribution, q_range)

    q = fit_res["q"]
    I = fit_res["intensity"]
    sigma = fit_res["sigma"]
    model_I = fit_res["model"]

    # Plot the fitted SAXS profile
    plt.figure()
    if sigma is not None:
        plt.errorbar(q, I, yerr=sigma, fmt='o', ms=3, lw=1, label='Data')
    else:
        plt.plot(q, I, 'o', ms=3, label='Data')
    plt.plot(q, model_I, '-', lw=2, label='Polydisperse fit')
    plt.xlabel('q (1/Å)')
    plt.ylabel('Intensity (a.u.)')
    plt.title('Polydisperse sphere fit')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()

    # --- Visualize the resulting radius distribution ---
    dist_info = fit_res["distribution"]
    dist_name = dist_info["name"].lower()
    dist_params = dist_info["params"]

    # Choose plotting range based on fit
    mean = dist_params.get("mean") or dist_params.get("r_mean") or dist_params.get("mu")
    std = dist_params.get("std") or dist_params.get("sigma") or 0.2
    R_min = max(0.01, mean - 4 * std)
    R_max = mean + 4 * std
    R = np.linspace(R_min, R_max, 300)

    # Define resulting 1D PDF using same logic as in polydispfit.py
    if dist_name in ("gaussian", "normal"):
        pdf = np.exp(-0.5 * ((R - dist_params["mean"]) / dist_params["std"]) ** 2) / (dist_params["std"] * np.sqrt(2 * np.pi))
    elif dist_name in ("lognormal", "log-normal"):
        safe_R = np.maximum(R, np.finfo(float).tiny)
        pdf = np.exp(-(np.log(safe_R) - dist_params["mu"]) ** 2 / (2 * dist_params["sigma"] ** 2)) / (
            safe_R * dist_params["sigma"] * np.sqrt(2 * np.pi)
        )
    elif dist_name in ("schulz", "schultz", "gamma"):
        z = dist_params["z"]
        r_mean = dist_params.get("mean", dist_params.get("r_mean"))
        safe_R = np.maximum(R, np.finfo(float).tiny)
        from scipy.special import gamma as gammafn
        prefactor = ((z + 1) ** (z + 1)) / (r_mean * gammafn(z + 1))
        pdf = prefactor * (safe_R / r_mean) ** z * np.exp(-(z + 1) * safe_R / r_mean)
    else:
        pdf = np.full_like(R, np.nan)
        print("Unknown distribution type for visualization.")

    # Plot the fitted distribution
    plt.figure()
    plt.plot(R, pdf, label=f'{dist_name.capitalize()} fit')
    plt.xlabel('Radius')
    plt.ylabel('Probability density')
    plt.title('Fitted radius distribution')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()

    print("Fit summary:")
    print(f"  scale:       {fit_res['scale']:.4g}")
    print(f"  background:  {fit_res['background']:.4g}")
    print(f"  chi2:        {fit_res['chi2']:.4g}")
    print(f"  distribution params: {fit_res['distribution']}")


if __name__ == '__main__':
    # visual_model_test()
    # view_3d_projections_test()
    # automask_test()
    polydispfit_test()
