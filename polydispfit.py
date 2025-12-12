from typing import Tuple, Union, Dict, Optional
import yaml
import pandas as pd
import numpy as np
from io import StringIO
import os
import sys
import re
import glob
import itertools

from scipy.integrate import quad_vec
from scipy.interpolate import RegularGridInterpolator
from scipy.special import gamma
from scipy import optimize

from utils import *


def calculate_polydisperse_profile(model_name, q_fitted, distribution_func, *distribution_args):
    """
    Evaluate the polydisperse scattering profile for a given model and distribution.

    Steps:
    1) Load the precomputed lookup table (q grid, parameter grids, form factor grid, volume grid).
    2) Restrict both the experimental q range and the table to their overlap.
    3) Interpolate the form factor on the combined (q, parameters) grid.
    4) Compute numerator = ∫ D(param) V(param)^2 P(q,param) dparam.
       Compute denominator = ∫ D(param) V(param)^2 dparam.
       Return numerator/denominator for each q.
    """
    file_path = os.path.join(GLOBALS_DIR, 'tabular', f'{model_name}.npz')
    with np.load(file_path, allow_pickle=True) as data:
        P_precalc = data['form_factor_data']
        q_precalc = data['q_values']
        param_grids = [data[f'param_{i+1}_values'] for i in range(len(data.files) - 3)] # Adjusted index
        volume_grid = data['volume_grid'] # <-- CORRECTLY LOADED

    q_fitted_min, q_fitted_max = q_fitted.min(), q_fitted.max()
    q_precalc_min, q_precalc_max = q_precalc.min(), q_precalc.max()

    q_min_new = max(q_fitted_min, q_precalc_min)
    q_max_new = min(q_fitted_max, q_precalc_max)

    assert q_min_new < q_max_new, "No overlap between fitted and precalculated q ranges!"

    q_mask_fitted = (q_fitted >= q_min_new) & (q_fitted <= q_max_new)
    q_mask_precalc = (q_precalc >= q_min_new) & (q_precalc <= q_max_new)

    q_fitted_shrunk = q_fitted[q_mask_fitted]
    q_precalc_shrunk = q_precalc[q_mask_precalc]
    P_shrunk = P_precalc[q_mask_precalc]

    # Use the shrunk q-range for everything downstream
    q_fitted = q_fitted_shrunk

    # Interpolator over q + parameter grids
    interpolator = RegularGridInterpolator(
        (q_precalc_shrunk, *param_grids),
        P_shrunk,
        method='linear',
        bounds_error=False,
        fill_value=0.0,
    )

    # Build full mesh for interpolation: shape (len(q), *param_grid_shapes)
    mesh = np.meshgrid(q_fitted, *param_grids, indexing='ij')
    points = np.stack([m.ravel() for m in mesh], axis=-1)
    P_q_interp_grid = interpolator(points).reshape(mesh[0].shape)

    # Distribution evaluated on parameter grids
    D_grid = distribution_func(*param_grids, *distribution_args)
    V_grid_sq = volume_grid**2

    # Denominator: ∫ D * V^2 dparams
    integrand_denominator = D_grid * V_grid_sq
    denominator = integrand_denominator
    for i in range(len(param_grids)):
        denominator = np.trapz(denominator, param_grids[i], axis=0)

    # Numerator: ∫ D * V^2 * P(q, params) dparams
    integrand_numerator = D_grid * V_grid_sq * P_q_interp_grid
    numerator = integrand_numerator
    for i in range(len(param_grids)):
        numerator = np.trapz(numerator, param_grids[i], axis=1)

    return numerator / denominator


def polydispfit(data_path, model_name, distribution: Dict, q_fit_range: Tuple[float, float]):
    """
    Fit 1D SAXS data by optimizing polydisperse distribution parameters.

    Expected ``distribution`` shape:
    {
        "name": "gaussian" | "lognormal" | "schulz",
        "params": {"mean": ..., "std": ...}  # keys depend on distribution
        "bounds": {"mean": (low, high), "std": (low, high)}  # optional
    }
    For Schulz, use keys ``z`` and ``mean`` (or ``r_mean``). For lognormal, use
    ``mu`` and ``sigma``.
    """
    q_data, intensity, sigma, metadata = read_saxs(data_path)

    q_min, q_max = q_fit_range
    mask = (q_data >= q_min) & (q_data <= q_max)
    if not np.any(mask):
        raise ValueError("q_fit_range excludes all data points.")

    q_fit = q_data[mask]
    intensity_fit = intensity[mask]
    sigma_fit = sigma[mask] if sigma is not None else None

    model_path = os.path.join(GLOBALS_DIR, 'tabular', f'{model_name}.npz')
    with np.load(model_path, allow_pickle=True) as data:
        q_precalc = data["q_values"]

    q_overlap_min = max(q_fit.min(), q_precalc.min())
    q_overlap_max = min(q_fit.max(), q_precalc.max())
    overlap_mask = (q_fit >= q_overlap_min) & (q_fit <= q_overlap_max)
    if not np.any(overlap_mask):
        raise ValueError("No overlap between data q-range and model lookup table.")

    q_fit = q_fit[overlap_mask]
    intensity_fit = intensity_fit[overlap_mask]
    sigma_fit = sigma_fit[overlap_mask] if sigma_fit is not None else None

    name = distribution.get("name", "").lower()
    param_dict = distribution.get("params") or {}
    bounds_dict = distribution.get("bounds") or {}

    def _one_dim_pdf(grid, params):
        if name in ("gaussian", "normal"):
            mean = params["mean"]
            std = params["std"]
            return np.exp(-0.5 * ((grid - mean) / std) ** 2) / (std * np.sqrt(2 * np.pi))
        if name in ("lognormal", "log-normal"):
            mu = params["mu"]
            sigma_param = params["sigma"]
            safe_grid = np.maximum(grid, np.finfo(float).tiny)
            return np.exp(-(np.log(safe_grid) - mu) ** 2 / (2 * sigma_param ** 2)) / (
                safe_grid * sigma_param * np.sqrt(2 * np.pi)
            )
        if name in ("schulz", "schultz", "gamma"):
            z = params["z"]
            r_mean = params.get("mean", params.get("r_mean"))
            safe_grid = np.maximum(grid, np.finfo(float).tiny)
            prefactor = ((z + 1) ** (z + 1)) / (r_mean * gamma(z + 1))
            return prefactor * (safe_grid / r_mean) ** z * np.exp(-(z + 1) * safe_grid / r_mean)
        raise ValueError(f"Unsupported distribution: {name}")

    # Build parameter vector and bounds
    if name in ("gaussian", "normal"):
        order = ["mean", "std"]
    elif name in ("lognormal", "log-normal"):
        order = ["mu", "sigma"]
    elif name in ("schulz", "schultz", "gamma"):
        order = ["z", "mean"]
    else:
        raise ValueError(f"Unsupported distribution: {name}")

    try:
        x0 = np.array([param_dict[k] for k in order], dtype=float)
    except KeyError as exc:
        raise ValueError(f"Missing initial parameter '{exc.args[0]}' for distribution '{name}'.")

    lower = []
    upper = []
    for key in order:
        b = bounds_dict.get(key, (-np.inf, np.inf))
        lower.append(b[0])
        upper.append(b[1])
    bounds = (np.array(lower, dtype=float), np.array(upper, dtype=float))

    def _distribution_func_factory(params):
        def dist_func(*param_grids):
            if len(param_grids) != 1:
                # multi-parameter grids not supported in this simplified fitter
                raise ValueError("Expected a single parameter grid for distribution.")
            return _one_dim_pdf(param_grids[0], params)
        return dist_func

    def _eval_model(params_vec):
        params = dict(zip(order, params_vec))
        distribution_func = _distribution_func_factory(params)
        profile = calculate_polydisperse_profile(model_name, q_fit, distribution_func)
        return np.asarray(profile).squeeze()

    def _residuals(params_vec):
        model_profile = _eval_model(params_vec)
        design = np.vstack([model_profile, np.ones_like(model_profile)]).T
        if sigma_fit is not None:
            weights = 1.0 / np.maximum(sigma_fit, np.finfo(float).tiny)
            design_w = design * weights[:, None]
            target_w = intensity_fit * weights
        else:
            design_w = design
            target_w = intensity_fit
        coeffs, *_ = np.linalg.lstsq(design_w, target_w, rcond=None)
        scale, background = coeffs
        fitted = scale * model_profile + background
        if sigma_fit is not None:
            return (intensity_fit - fitted) / np.maximum(sigma_fit, np.finfo(float).tiny)
        return intensity_fit - fitted

    opt = optimize.least_squares(_residuals, x0, bounds=bounds, method="trf")
    opt_params = dict(zip(order, opt.x))
    final_profile = _eval_model(opt.x)

    design = np.vstack([final_profile, np.ones_like(final_profile)]).T
    if sigma_fit is not None:
        weights = 1.0 / np.maximum(sigma_fit, np.finfo(float).tiny)
        design_w = design * weights[:, None]
        target_w = intensity_fit * weights
    else:
        design_w = design
        target_w = intensity_fit
    coeffs, *_ = np.linalg.lstsq(design_w, target_w, rcond=None)
    scale, background = coeffs
    fitted_intensity = scale * final_profile + background
    residuals = intensity_fit - fitted_intensity
    from utils import calc_chi2
    if sigma_fit is not None:
        chi2 = float(calc_chi2(intensity_fit, fitted_intensity, sigma_fit))
    else:
        # Assume errors are 3% of intensity values (avoid divide by zero)
        assumed_sigma = np.clip(0.03 * np.maximum(np.abs(intensity_fit), 1e-9), 1e-12, None)
        chi2 = float(calc_chi2(intensity_fit, fitted_intensity, assumed_sigma))

    return {
        "q": q_fit,
        "intensity": intensity_fit,
        "sigma": sigma_fit,
        "model": fitted_intensity,
        "scale": float(scale),
        "background": float(background),
        "chi2": chi2,
        "distribution": {"name": name, "params": opt_params, "bounds": bounds_dict},
        "metadata": metadata,
        "optimizer_info": {"success": opt.success, "message": opt.message, "nfev": opt.nfev},
    }


def precalculate_form_factor(form_factor, volume_func, q_space, *parameter_spaces, save_path):
    """
    Calculates and saves a multi-dimensional form factor lookup table with metadata.
    This version includes pre-calculating and saving the volume grid.
    """
    q = np.linspace(*q_space)
    param_arrays = [np.linspace(*space) for space in parameter_spaces]
    if not param_arrays:
        raise ValueError("At least one parameter space must be provided.")
        
    param_grids = np.meshgrid(*param_arrays, indexing='ij')

    print(f"Calculating form factor for a grid of shape: {[grid.shape for grid in param_grids]}...")
    P_q_params = form_factor(q, *param_grids)
    print("Form factor calculation complete.")

    print("Calculating volume grid...")
    volume_grid = volume_func(*param_grids)
    print("Volume grid calculation complete.")

    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    
    save_dict = {
        'form_factor_data': P_q_params, 
        'q_values': q,
        'volume_grid': volume_grid  # <-- ADD THIS
    }
    for i, param_array in enumerate(param_arrays):
        save_dict[f'param_{i+1}_values'] = param_array
        
    np.savez_compressed(save_path, **save_dict)
    print(f"Lookup table and metadata saved to {save_path}")

    return P_q_params


def sphere_form_factor_vectorized(q, R_grid):
    """
    Vectorized form factor for a sphere.
    Calculates P(q, R) for all combinations of q and R.

    Args:
        q (np.ndarray): 1D array of q-values.
        R_grid (np.ndarray): Grid of radius values from np.meshgrid.

    Returns:
        np.ndarray: 2D array of form factors P(q, R).
    """
    # Use broadcasting to compute q*R for all combinations
    # q shape: (N_q, 1), R_grid shape: (N_R,)
    # Resulting qr shape: (N_q, N_R)
    qr = q[:, np.newaxis] * R_grid
    
    # Handle the singularity at qr=0 using np.where
    amplitude = np.where(qr < 1e-8, 1.0, 3 * (np.sin(qr) - qr * np.cos(qr)) / qr**3)
    
    return amplitude**2


def sphere_volume(R_grid):
    return (4.0/3.0) * np.pi * R_grid**3


def spheroid_form_factor_vectorized(q, a_grid, c_grid):
    """
    Vectorized form factor for a spheroid (a=b).
    Calculates P(q, a, c) for all combinations of q, a, and c.
    
    Note: The angular integration is performed in a loop over the 'a' parameter,
    as full vectorization over multiple parameters is non-trivial with scipy.integrate.
    """
    # We will build the result array step-by-step
    P_q_a_c = np.zeros((len(q), a_grid.shape[0], a_grid.shape[1]))

    # Iterate over the 'a' dimension of the grid
    for i in range(a_grid.shape[0]):
        for j in range(a_grid.shape[1]):
            a_val = a_grid[i, j]
            c_val = c_grid[i, j]

            def integrand(theta, q_array):
                R = np.sqrt((a_val * np.sin(theta))**2 + (c_val * np.cos(theta))**2)
                x = q_array * R
                amplitude = np.where(x < 1e-8, 1.0, 3 * (np.sin(x) - x * np.cos(x)) / x**3)
                return amplitude**2 * np.sin(theta)

            # Use quad_vec for fast integration over the q-vector
            result, _ = quad_vec(integrand, 0, np.pi/2, args=(q,))
            P_q_a_c[:, i, j] = result
            
    return P_q_a_c


def spheroid_volume(a_grid, c_grid):
    return (4.0/3.0) * np.pi * a_grid**2 * c_grid


if __name__ == '__main__':
    # --- Define parameters for the pre-calculation ---
    q_space = (0.01, 10.0, 1000)  # q from 0.01 to 1.0, 100 points
    radius_space = (0.01, 10.0, 1000)   # Radius from 10 to 50, 5 points
    save_path_sphere = os.path.join(GLOBALS_DIR, 'tabular', 'sphere.npz')

    # --- Run the pre-calculation ---
    sphere_table = precalculate_form_factor(
        sphere_form_factor_vectorized, sphere_volume,
        q_space, radius_space, # Note: this is a single tuple
        save_path=save_path_sphere
    )

    # --- Verification ---
    print("\n--- Verification ---")
    print(f"Shape of the resulting sphere table: {sphere_table.shape}")
    print(f"Expected shape: (len(q), len(R)) = ({len(np.linspace(*q_space))}, {len(np.linspace(*radius_space))})")

    # --- DEMONSTRATION: Loading and Using the Table ---
    print("\n--- Loading and Verifying the .npz file ---")
    # Load the .npz file. It behaves like a dictionary.
    loaded_data = np.load(save_path_sphere)

    # Access the arrays using the keys we defined
    q_loaded = loaded_data['q_values']
    radius_loaded = loaded_data['param_1_values']
    sphere_table_loaded = loaded_data['form_factor_data']

    print(f"Loaded q-array shape: {q_loaded.shape}")
    print(f"Loaded radius array shape: {radius_loaded.shape}")
    print(f"Loaded data table shape: {sphere_table_loaded.shape}")

    # Verify that the loaded data matches the original in-memory data
    if np.allclose(q_loaded, np.linspace(*q_space)):
        print("Verification successful: Loaded q-values match the original.")
    else:
        print("Verification failed: Loaded q-values do not match.")

    # Example of using the loaded data: Plot P(q) for the largest radius
    import matplotlib.pyplot as plt

    plt.figure()
    for row in sphere_table_loaded.T[::20]:
        plt.plot(q_loaded, row)
    plt.title(f"SAXS Profils for R = {'; '.join(str(r) for r in radius_space)}")
    plt.xlabel("q (nm-1)")
    plt.ylabel("P(q)")
    plt.grid(True)
    plt.show()
