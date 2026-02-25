from typing import Tuple, Union, Dict, Optional
import time
import warnings
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

from .utils import *

# Sasmodels for polydisperse sphere (required)
from sasmodels.core import load_model
from sasmodels.direct_model import call_kernel

# Optional: Bayesian optimization (pip install scikit-optimize)
try:
    from skopt import gp_minimize
    from skopt.space import Real
    _SKOPT_AVAILABLE = True
except ImportError:
    _SKOPT_AVAILABLE = False


def _polydisperse_sphere_profile_sasmodels(
    q_nm: np.ndarray, dist_name: str, params: Dict
) -> np.ndarray:
    """
    Polydisperse sphere I(q) profile via sasmodels (scale=1, background=0).
    q_nm in 1/nm; params in nm (mean, std or mu, sigma or z, mean).
    """
    q_A = np.asarray(q_nm, dtype=float) / 10.0
    model = load_model("sphere")
    kernel = model.make_kernel([q_A])
    dist_name = dist_name.lower()
    if dist_name in ("gaussian", "normal"):
        mean_nm = params["mean"]
        std_nm = params["std"]
        radius_A = mean_nm * 10.0
        radius_pd = std_nm / mean_nm if mean_nm > 0 else 0.0
        pd_type = "gaussian"
    elif dist_name in ("lognormal", "log-normal"):
        mu = params["mu"]
        sigma = params["sigma"]
        radius_A = np.exp(mu) * 10.0
        radius_pd = sigma
        pd_type = "lognormal"
    elif dist_name in ("schulz", "schultz", "gamma"):
        mean_nm = params.get("mean", params.get("r_mean"))
        z = params["z"]
        radius_A = mean_nm * 10.0
        radius_pd = 1.0 / np.sqrt(z + 1.0) if z > -1 else 0.0
        pd_type = "schulz"
    else:
        raise ValueError(f"Unsupported distribution for sasmodels: {dist_name}")
    pars = {
        "radius": float(radius_A),
        "radius_pd": float(radius_pd),
        "radius_pd_type": pd_type,
        "radius_pd_n": 40,
        "radius_pd_nsigma": 3,
        "scale": 1.0,
        "background": 0.0,
        "sld": 1.0,
        "sld_solvent": 0.0,
    }
    Iq = call_kernel(kernel, pars)
    return np.asarray(Iq).squeeze()


def polydispfit(
    data_path,
    model_name,
    distribution: Dict,
    q_fit_range: Tuple[float, float],
    optimizer: Union[str, Dict] = "sobol_trf",
):
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

    optimizer: Optimization method. Default is "sobol_trf".
      - "dual_annealing" or "da": scipy dual annealing (global, efficient for 2D).
      - "sobol_trf": Multi-start TRF with Sobol-sampled starting points. No limit on number of starts: each phase
        (linear, log) runs until chi2 < chi2_stop (default 1) or its time budget is spent. Default time_budget_linear=60 s,
        time_budget_log=60 s.
      - "bo" or "bayesian": Bayesian optimization (requires scikit-optimize).
      - "global" or "differential_evolution": differential evolution.
      - "trf": Trust Region Reflective (fast but local).
      Can also be a dict, e.g. {"method": "dual_annealing", "maxiter": 400}.
    """
    q_data, intensity, sigma, metadata = read_saxs(data_path)

    q_min, q_max = q_fit_range
    mask = (q_data >= q_min) & (q_data <= q_max)
    if not np.any(mask):
        raise ValueError("q_fit_range excludes all data points.")

    q_fit = q_data[mask]
    intensity_fit = intensity[mask]
    sigma_fit = sigma[mask] if sigma is not None else None

    name = distribution.get("name", "").lower()
    use_sasmodels = model_name == "sphere"
    if not use_sasmodels:
        # Deprecated: internal lookup table and .npz (only non-sphere models)
        warnings.warn(
            "Internal pure-Python polydisperse curve and .npz lookup are deprecated; "
            "only sphere model is supported (via sasmodels).",
            DeprecationWarning,
            stacklevel=2,
        )
        ensure_tabular_model(model_name)
        model_path = os.path.join(GLOBALS_DIR, "tabular", f"{model_name}.npz")
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

    # Log-space reparameterization: which params are optimized in log (positive scale params only; mu stays linear)
    _LOG_PARAMS = ("mean", "std", "z", "sigma", "r_mean")
    loggable_mask = np.array([key in _LOG_PARAMS for key in order], dtype=bool)

    def _optim_to_physical(x_optim):
        """Map optimizer variable to physical params. Loggable: x_phys = exp(x_optim); else identity."""
        p = np.asarray(x_optim, dtype=float).copy()
        p[loggable_mask] = np.exp(p[loggable_mask])
        return p

    def _physical_to_optim(physical):
        """Map physical params to optimizer variable (for log space)."""
        p = np.asarray(physical, dtype=float).copy()
        p[loggable_mask] = np.log(np.maximum(p[loggable_mask], np.finfo(float).tiny))
        return p

    # Bounds and x0 in log-space (for the log run)
    lower_log = np.array(lower, dtype=float).copy()
    upper_log = np.array(upper, dtype=float).copy()
    for i in range(len(order)):
        if loggable_mask[i]:
            lo, hi = lower[i], upper[i]
            lo = max(lo, np.finfo(float).tiny) if np.isfinite(lo) else 1e-10
            hi = max(hi, np.finfo(float).tiny) if np.isfinite(hi) else 1e10
            lower_log[i] = np.log(lo)
            upper_log[i] = np.log(hi)
    bounds_log = (lower_log, upper_log)
    x0_log = _physical_to_optim(x0)

    def _distribution_func_factory(params):
        def dist_func(*param_grids):
            if len(param_grids) != 1:
                # multi-parameter grids not supported in this simplified fitter
                raise ValueError("Expected a single parameter grid for distribution.")
            return _one_dim_pdf(param_grids[0], params)
        return dist_func

    def _eval_model(params_vec):
        params = dict(zip(order, params_vec))
        if use_sasmodels:
            profile = _polydisperse_sphere_profile_sasmodels(q_fit, name, params)
        else:
            distribution_func = _distribution_func_factory(params)
            profile = calculate_polydisperse_profile(
                model_name, q_fit, distribution_func, use_precalculated=True
            )
        return np.asarray(profile).squeeze()

    def _residuals(params_vec):
        try:
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
        except (np.linalg.LinAlgError, ValueError, FloatingPointError, OverflowError):
            return np.full_like(intensity_fit, 1e10)

    def _scalar_objective(params_vec):
        """L2 norm of (data - model): sum of squared unweighted residuals. Smoother than chi² for BO/DE."""
        try:
            model_profile = _eval_model(params_vec)
            design = np.vstack([model_profile, np.ones_like(model_profile)]).T
            coeffs, *_ = np.linalg.lstsq(design, intensity_fit, rcond=None)
            scale, background = coeffs
            fitted = scale * model_profile + background
            return float(np.sum((intensity_fit - fitted) ** 2))
        except (np.linalg.LinAlgError, ValueError, FloatingPointError, OverflowError):
            return 1e20

    def _residuals_log(x_optim):
        """Residuals with optimizer in log-space; convert to physical then call linear residuals."""
        return _residuals(_optim_to_physical(x_optim))

    def _scalar_objective_log(x_optim):
        """Scalar objective with optimizer in log-space."""
        return _scalar_objective(_optim_to_physical(x_optim))

    def _compute_chi2_score(physical_params):
        """Given physical param vector, return chi2, score (0.5*sum(weighted_res^2)), scale, background, fitted."""
        from .utils import calc_chi2
        model_profile = _eval_model(physical_params)
        design = np.vstack([model_profile, np.ones_like(model_profile)]).T
        if sigma_fit is not None:
            weights = 1.0 / np.maximum(sigma_fit, np.finfo(float).tiny)
            design_w = design * weights[:, None]
            target_w = intensity_fit * weights
            coeffs, *_ = np.linalg.lstsq(design_w, target_w, rcond=None)
            scale, background = coeffs
            fitted = scale * model_profile + background
            res = (intensity_fit - fitted) / np.maximum(sigma_fit, np.finfo(float).tiny)
            score = 0.5 * float(np.sum(res ** 2))
            chi2_val = float(calc_chi2(intensity_fit, fitted, sigma_fit))
        else:
            coeffs, *_ = np.linalg.lstsq(design, intensity_fit, rcond=None)
            scale, background = coeffs
            fitted = scale * model_profile + background
            res = intensity_fit - fitted
            score = 0.5 * float(np.sum(res ** 2))
            assumed_sigma = np.clip(0.03 * np.maximum(np.abs(intensity_fit), 1e-9), 1e-12, None)
            chi2_val = float(calc_chi2(intensity_fit, fitted, assumed_sigma))
        return chi2_val, score, scale, background, fitted

    # Resolve optimizer option
    opt_method = "sobol_trf"
    opt_kwargs = {}
    if isinstance(optimizer, dict):
        opt_method = optimizer.get("method", "sobol_trf")
        opt_kwargs = {k: v for k, v in optimizer.items() if k != "method"}
    else:
        opt_method = str(optimizer).strip().lower()

    n_starts_linear_run, n_starts_log_run = None, None
    time_budget_linear, time_budget_log = None, None
    elapsed_linear_sec, elapsed_log_sec, elapsed_sec = None, None, None

    if opt_method in ("trf", "least_squares"):
        opt_linear = optimize.least_squares(_residuals, x0, bounds=bounds, method="trf", **opt_kwargs)
        opt_log = optimize.least_squares(_residuals_log, x0_log, bounds=bounds_log, method="trf", **opt_kwargs)
        physical_linear = opt_linear.x
        physical_log = _optim_to_physical(opt_log.x)
        chi2_linear, score_linear, s_lin, b_lin, f_lin = _compute_chi2_score(physical_linear)
        chi2_log, score_log, s_log, b_log, f_log = _compute_chi2_score(physical_log)
        if chi2_linear <= chi2_log:
            opt_x = physical_linear
            scale, background, fitted_intensity = s_lin, b_lin, f_lin
            chi2 = chi2_linear
            opt_success, opt_message, opt_nfev = opt_linear.success, opt_linear.message, opt_linear.nfev
            parameterization_used = "linear"
        else:
            opt_x = physical_log
            scale, background, fitted_intensity = s_log, b_log, f_log
            chi2 = chi2_log
            opt_success, opt_message, opt_nfev = opt_log.success, opt_log.message, opt_log.nfev
            parameterization_used = "log"
    elif opt_method in ("sobol_trf", "sobol_trf_multistart", "multistart_trf"):
        from scipy.stats import qmc
        _MAX_SIZE_NM = 100.0
        _LARGE = 1e10
        lower_f = np.array(lower, dtype=float)
        upper_f = np.array(upper, dtype=float)
        for i, key in enumerate(order):
            if not np.isfinite(lower_f[i]):
                lower_f[i] = 1e-10 if key not in ("mean", "r_mean") else 0.01
            if not np.isfinite(upper_f[i]):
                upper_f[i] = _MAX_SIZE_NM if key in ("mean", "r_mean") else _LARGE
        lower_log_f = np.array(lower_log, dtype=float).copy()
        upper_log_f = np.array(upper_log, dtype=float).copy()
        for i in range(len(order)):
            if not np.isfinite(lower_log_f[i]):
                lower_log_f[i] = -10.0
            if not np.isfinite(upper_log_f[i]):
                upper_log_f[i] = 10.0
        opt_kwargs.pop("n_starts", None)  # no longer used; time budget only
        seed = opt_kwargs.pop("seed", 42)
        chi2_stop = opt_kwargs.pop("chi2_stop", 1.0)  # stop phase when chi2 < this
        time_budget_linear = float(opt_kwargs.pop("time_budget_linear", 60.0))  # seconds for linear phase
        time_budget_log = float(opt_kwargs.pop("time_budget_log", 60.0))  # seconds for log phase
        n_pts = len(intensity_fit)
        dof = max(1, n_pts - 1)
        sampler = qmc.Sobol(d=len(order), scramble=True, seed=seed)

        # Linear run: keep trying Sobol starts until time budget spent or chi2 < chi2_stop
        start_linear = time.perf_counter()
        best_cost_lin = np.inf
        best_opt_linear = None
        total_nfev_linear = 0
        n_starts_linear_run = 0
        while True:
            if start_linear + time_budget_linear <= time.perf_counter():
                break
            n_starts_linear_run += 1
            x0_start = qmc.scale(sampler.random(n=1), lower_f, upper_f)[0]
            opt = optimize.least_squares(
                _residuals, x0_start, bounds=(lower_f, upper_f), method="trf", **opt_kwargs
            )
            total_nfev_linear += opt.nfev
            cost = 0.5 * np.sum(opt.fun ** 2)
            chi2_current = np.sum(opt.fun ** 2) / dof
            if cost < best_cost_lin:
                best_cost_lin = cost
                best_opt_linear = opt
            if chi2_current < chi2_stop:
                break
        elapsed_linear_sec = time.perf_counter() - start_linear
        physical_linear = best_opt_linear.x
        chi2_linear, score_linear, s_lin, b_lin, f_lin = _compute_chi2_score(physical_linear)

        # Log run: same, separate time budget
        start_log = time.perf_counter()
        best_cost_log = np.inf
        best_opt_log = None
        total_nfev_log = 0
        n_starts_log_run = 0
        while True:
            if start_log + time_budget_log <= time.perf_counter():
                break
            n_starts_log_run += 1
            x0_start = qmc.scale(sampler.random(n=1), lower_log_f, upper_log_f)[0]
            opt = optimize.least_squares(
                _residuals_log, x0_start, bounds=(lower_log_f, upper_log_f), method="trf", **opt_kwargs
            )
            total_nfev_log += opt.nfev
            cost = 0.5 * np.sum(opt.fun ** 2)
            chi2_current = np.sum(opt.fun ** 2) / dof
            if cost < best_cost_log:
                best_cost_log = cost
                best_opt_log = opt
            if chi2_current < chi2_stop:
                break
        elapsed_log_sec = time.perf_counter() - start_log
        physical_log = _optim_to_physical(best_opt_log.x)
        chi2_log, score_log, s_log, b_log, f_log = _compute_chi2_score(physical_log)
        elapsed_sec = elapsed_linear_sec + elapsed_log_sec

        if chi2_linear <= chi2_log:
            opt_x = physical_linear
            scale, background, fitted_intensity = s_lin, b_lin, f_lin
            chi2 = chi2_linear
            opt_success, opt_message = best_opt_linear.success, getattr(best_opt_linear, "message", str(best_opt_linear))
            opt_nfev = total_nfev_linear
            parameterization_used = "linear"
        else:
            opt_x = physical_log
            scale, background, fitted_intensity = s_log, b_log, f_log
            chi2 = chi2_log
            opt_success, opt_message = best_opt_log.success, getattr(best_opt_log, "message", str(best_opt_log))
            opt_nfev = total_nfev_log
            parameterization_used = "log"
    else:
        # Finite bounds for global/BO: cap size parameters at 100 nm for SAXS; run both linear and log
        _MAX_SIZE_NM = 100.0
        _LARGE = 1e10
        lower_f = np.array(lower, dtype=float)
        upper_f = np.array(upper, dtype=float)
        for i, key in enumerate(order):
            if not np.isfinite(lower_f[i]):
                lower_f[i] = 1e-10 if key not in ("mean", "r_mean") else 0.01
            if not np.isfinite(upper_f[i]):
                upper_f[i] = _MAX_SIZE_NM if key in ("mean", "r_mean") else _LARGE
        bounds_list = list(zip(lower_f, upper_f))
        lower_log_f = np.array(lower_log, dtype=float).copy()
        upper_log_f = np.array(upper_log, dtype=float).copy()
        for i in range(len(order)):
            if not np.isfinite(lower_log_f[i]):
                lower_log_f[i] = -10.0
            if not np.isfinite(upper_log_f[i]):
                upper_log_f[i] = 10.0
        bounds_list_log = list(zip(lower_log_f, upper_log_f))

        if opt_method in ("dual_annealing", "da"):
            maxiter = opt_kwargs.pop("maxiter", 80)
            seed = opt_kwargs.pop("seed", 42)
            res_linear = optimize.dual_annealing(
                _scalar_objective, bounds_list, maxiter=maxiter, seed=seed, **opt_kwargs
            )
            res_log = optimize.dual_annealing(
                _scalar_objective_log, bounds_list_log, maxiter=maxiter, seed=seed + 1, **opt_kwargs
            )
            physical_linear = res_linear.x
            physical_log = _optim_to_physical(res_log.x)
            chi2_linear, score_linear = _compute_chi2_score(physical_linear)[:2]
            chi2_log, score_log = _compute_chi2_score(physical_log)[:2]
            if chi2_linear <= chi2_log:
                opt_x = physical_linear
                opt_success, opt_message = res_linear.success, getattr(res_linear, "message", str(res_linear))
                opt_nfev = getattr(res_linear, "nfev", None)
                parameterization_used = "linear"
            else:
                opt_x = physical_log
                opt_success, opt_message = res_log.success, getattr(res_log, "message", str(res_log))
                opt_nfev = getattr(res_log, "nfev", None)
                parameterization_used = "log"
        elif opt_method in ("bo", "bayesian", "gp_minimize"):
            if not _SKOPT_AVAILABLE:
                raise RuntimeError(
                    "Bayesian optimization requires scikit-optimize. Install with: pip install scikit-optimize"
                )
            dimensions = [Real(float(lower_f[i]), float(upper_f[i])) for i in range(len(order))]
            dimensions_log = [Real(float(lower_log_f[i]), float(upper_log_f[i])) for i in range(len(order))]
            n_calls = opt_kwargs.pop("n_calls", 120)
            n_initial_points = opt_kwargs.pop("n_initial_points", 22)
            random_state = opt_kwargs.pop("random_state", 42)
            acq_func = opt_kwargs.pop("acq_func", "EI")
            xi = opt_kwargs.pop("xi", 0.05)
            kappa = opt_kwargs.pop("kappa", 1.96)
            callback = opt_kwargs.pop("callback", None)
            pbar = None
            if callback is None:
                try:
                    from tqdm import tqdm
                    pbar = tqdm(total=2 * n_calls, desc="BO", leave=False)
                    def _progress_cb(res, _pbar=pbar):
                        _pbar.n = len(res.func_vals) if hasattr(res, "func_vals") else _pbar.n + 1
                        _pbar.refresh()
                    callback = _progress_cb
                except ImportError:
                    pass
            res_linear = gp_minimize(
                _scalar_objective, dimensions,
                n_calls=n_calls, n_initial_points=n_initial_points,
                random_state=random_state, acq_func=acq_func, xi=xi, kappa=kappa,
                callback=callback, **opt_kwargs,
            )
            res_log = gp_minimize(
                _scalar_objective_log, dimensions_log,
                n_calls=n_calls, n_initial_points=n_initial_points,
                random_state=random_state + 1, acq_func=acq_func, xi=xi, kappa=kappa,
                callback=callback, **opt_kwargs,
            )
            if pbar is not None:
                pbar.close()
            physical_linear = np.array(res_linear.x)
            physical_log = _optim_to_physical(res_log.x)
            chi2_linear, score_linear = _compute_chi2_score(physical_linear)[:2]
            chi2_log, score_log = _compute_chi2_score(physical_log)[:2]
            if chi2_linear <= chi2_log:
                opt_x = physical_linear
                opt_success, opt_message = True, "Bayesian optimization completed"
                opt_nfev = len(res_linear.func_vals) if hasattr(res_linear, "func_vals") else n_calls
                parameterization_used = "linear"
            else:
                opt_x = physical_log
                opt_success, opt_message = True, "Bayesian optimization completed"
                opt_nfev = len(res_log.func_vals) if hasattr(res_log, "func_vals") else n_calls
                parameterization_used = "log"
        elif opt_method in ("global", "differential_evolution"):
            kwargs = {"maxiter": 200, "popsize": 10, "seed": 42, "polish": True, "atol": 1e-6, "tol": 1e-6, **opt_kwargs}
            res_linear = optimize.differential_evolution(_scalar_objective, bounds_list, **kwargs)
            res_log = optimize.differential_evolution(_scalar_objective_log, bounds_list_log, **kwargs)
            physical_linear = res_linear.x
            physical_log = _optim_to_physical(res_log.x)
            chi2_linear, score_linear = _compute_chi2_score(physical_linear)[:2]
            chi2_log, score_log = _compute_chi2_score(physical_log)[:2]
            if chi2_linear <= chi2_log:
                opt_x = physical_linear
                opt_success, opt_message = res_linear.success, res_linear.message if hasattr(res_linear, "message") else str(res_linear)
                opt_nfev = getattr(res_linear, "nfev", None)
                parameterization_used = "linear"
            else:
                opt_x = physical_log
                opt_success, opt_message = res_log.success, res_log.message if hasattr(res_log, "message") else str(res_log)
                opt_nfev = getattr(res_log, "nfev", None)
                parameterization_used = "log"
        else:
            raise ValueError(
                f"Unsupported optimizer: {optimizer}. Use 'dual_annealing', 'sobol_trf', 'global', 'bo', 'trf', or a dict with 'method'."
            )

    opt_params = dict(zip(order, opt_x))
    final_profile = _eval_model(opt_x)

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
    from .utils import calc_chi2
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
        "chi2_linear": chi2_linear,
        "chi2_log": chi2_log,
        "score_linear": score_linear,
        "score_log": score_log,
        "parameterization_used": parameterization_used,
        "distribution": {"name": name, "params": opt_params, "bounds": bounds_dict},
        "metadata": metadata,
        "optimizer_info": {
            "method": opt_method,
            "success": opt_success,
            "message": opt_message,
            "nfev": opt_nfev,
            "parameterization_used": parameterization_used,
            "chi2_linear": chi2_linear,
            "chi2_log": chi2_log,
            "score_linear": score_linear,
            "score_log": score_log,
            "n_starts_linear_run": n_starts_linear_run,
            "n_starts_log_run": n_starts_log_run,
            "time_budget_linear_sec": time_budget_linear,
            "time_budget_log_sec": time_budget_log,
            "elapsed_linear_sec": elapsed_linear_sec,
            "elapsed_log_sec": elapsed_log_sec,
            "elapsed_sec": elapsed_sec,
        },
    }


def calculate_polydisperse_profile(
    model_name, q_fitted, distribution_func, *distribution_args,
    use_precalculated=True,
    ):
    """
    Evaluate the polydisperse scattering profile for a given model and distribution.

    .. deprecated::
        Pure-Python curve and .npz lookup are deprecated. Use sasmodels for
        sphere (polydispfit uses sasmodels when available).

    Steps:
    1) Load the precomputed lookup table (q grid, parameter grids, form factor grid, volume grid).
    2) Restrict both the experimental q range and the table to their overlap.
    3) Interpolate the form factor on the combined (q, parameters) grid.
    4) Compute numerator = ∫ D(param) V(param)^2 P(q,param) dparam.
       Compute denominator = ∫ D(param) V(param)^2 dparam.
       Return numerator/denominator for each q.
    """
    warnings.warn(
        "calculate_polydisperse_profile and .npz lookup are deprecated; "
        "use sasmodels for polydisperse sphere (polydispfit does this when sasmodels is installed).",
        DeprecationWarning,
        stacklevel=2,
    )
    assert model_name == 'sphere', 'Currently only sphere model is supported'
    file_path = os.path.join(GLOBALS_DIR, 'tabular', f'{model_name}.npz')
    if use_precalculated and os.path.exists(file_path):
        with np.load(file_path, allow_pickle=True) as data:
            P_precalc = data['form_factor_data']
            q_precalc = data['q_values']
            param_grids = [data[f'param_{i+1}_values'] for i in range(len(data.files) - 3)] # Adjusted index
            volume_grid = data['volume_grid'] # <-- CORRECTLY LOADED
    else:
        q_space = (0.01, 10.0, 1000)
        if model_name == 'sphere':
            form_factor = sphere_form_factor_vectorized
            volume_func = sphere_volume
            parameter_spaces = [(0.01, 10.0, 1000), ]
        # elif model_name == 'ellipsoid':
        #     form_factor = sphere_form_factor_vectorized
        #     volume_func = sphere_volume
        #     parameter_spaces = []
        else:
            raise ValueError(f'Unsupported model name: "{model_name}"')
        P_precalc, volume_grid, q_precalc, param_grids = calculate_form_factor(
            form_factor, volume_func, q_space, *parameter_spaces, save_path=file_path)

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

    result = numerator / denominator

    # RegularGridInterpolator with fill_value=0 returns 0 for q outside the grid.
    # Experimental q can be in [q_min_new, q_max_new] but below the first grid point
    # (e.g. q_precalc_shrunk[0]) or above the last, giving spurious zeros. Use the
    # integrated profile at the boundary q for those points.
    q_lo, q_hi = float(q_precalc_shrunk[0]), float(q_precalc_shrunk[-1])
    out_lo = q_fitted < q_lo
    out_hi = q_fitted > q_hi
    if np.any(out_lo):
        integrand_lo = D_grid * V_grid_sq * np.asarray(P_shrunk[0, ...]).reshape(-1)
        r_vals = np.asarray(param_grids[0]).reshape(-1)
        num_lo = np.trapz(integrand_lo, r_vals)
        result[out_lo] = num_lo / denominator
    if np.any(out_hi):
        integrand_hi = D_grid * V_grid_sq * np.asarray(P_shrunk[-1, ...]).reshape(-1)
        r_vals = np.asarray(param_grids[0]).reshape(-1)
        num_hi = np.trapz(integrand_hi, r_vals)
        result[out_hi] = num_hi / denominator

    return result


def calculate_form_factor(form_factor, volume_func, q_space, *parameter_spaces, save_path=None):
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

    if save_path is not None:
        warnings.warn(
            "Saving form-factor lookup to .npz is deprecated; "
            "polydispfit uses sasmodels for sphere when available.",
            DeprecationWarning,
            stacklevel=2,
        )
        assert save_path.endswith('.npz')
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

    return P_q_params, volume_grid, q, param_arrays


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


def ensure_tabular_model(model_name: str) -> None:
    """
    Regenerate the tabular .npz lookup table for the given model if it is absent.
    No user prompt; regeneration is automatic when the file is missing.

    .. deprecated::
        .npz caching is deprecated; polydispfit uses sasmodels for sphere when available.
    """
    warnings.warn(
        "ensure_tabular_model and .npz tabular cache are deprecated; "
        "use sasmodels for polydisperse sphere.",
        DeprecationWarning,
        stacklevel=2,
    )
    model_path = os.path.join(GLOBALS_DIR, 'tabular', f'{model_name}.npz')
    if os.path.isfile(model_path):
        return
    if model_name == 'sphere':
        q_space = (0.01, 10.0, 1000)
        radius_space = (0.01, 10.0, 1000)
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        calculate_form_factor(
            sphere_form_factor_vectorized, sphere_volume,
            q_space, radius_space,
            save_path=model_path
        )
        return
    raise ValueError(f"Unknown model for tabular regeneration: {model_name}")


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
    sphere_table = calculate_form_factor(
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
