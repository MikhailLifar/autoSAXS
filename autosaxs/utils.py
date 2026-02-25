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

from ase import Atoms

from pyFAI.io import image

import base64
from scipy.ndimage import gaussian_filter
from scipy.spatial.distance import cdist

import time
from contextlib import contextmanager

_autosaxs_dir = os.path.dirname(os.path.abspath(__file__))
LATEST_STEPS_PATH = os.path.join(_autosaxs_dir, "temp", "latest_steps.yml")

from .foreign.supervised_ml.whittaker_smooth import whittaker_smooth

GLOBALS_DIR = os.path.join(_autosaxs_dir, 'global')
TEMPLATES_DIR = os.path.join(GLOBALS_DIR, 'templates')
with open(os.path.join(GLOBALS_DIR, 'env.yml'), 'r') as fread:
    ENV = yaml.safe_load(fread)
ATSAS_BIN_PREFIX = ENV["ATSAS_BIN_PREFIX"]

# Pipeline units convention: q in nm^-1 (inverse nanometers), Rg and lengths in nm.
# PyFAI integrate1d must be called with unit='q_nm^-1'. ATSAS (autorg, datgnom, etc.)
# accept .dat with q in nm^-1 and return Rg, Dmax in nm; use write_saxs_atsas_format for input.


@contextmanager
def timer(name="Timer"):
    """
    Context manager to precisely measure the execution time of a code block.

    Usage:
        with timer("block name") as t:
            # code
        print(t["elapsed"])

    Returns:
        dict: Holds the elapsed time in seconds under key ``"elapsed"`` after
        the block finishes.
    """
    start = time.perf_counter()
    metrics = {}
    try:
        yield metrics
    finally:
        end = time.perf_counter()
        elapsed = end - start
        metrics["elapsed"] = elapsed
        print(f"[{name}] Elapsed time: {elapsed:.6f} seconds")


##### READING/WRITING FUNCTIONS ######


def read_from_tiff(tiff_path) -> np.ndarray:
    return image.read_image_data(tiff_path)


def write_saxs(filename, wavenumber, intensity, sigma, metadata):
    """
    Write SAXS data and metadata to a file using YAML for metadata and CSV for data.
    
    Parameters:
    filename (str): Output file path
    wavenumber (numpy.ndarray): Array of q-values (wavenumber)
    intensity (numpy.ndarray): Array of intensity values
    metadata (dict): Dictionary containing metadata
    """
    # Create a DataFrame for the data
    df = pd.DataFrame({
        'q': wavenumber,
        'intensity': intensity,
    })
    if sigma is not None:
        df['sigma'] = sigma

    # Delegate actual writing to generic helper
    write_data(filename, df, metadata)


def write_saxs_atsas_format(filename: str, q: np.ndarray, I: np.ndarray, sigma: Optional[np.ndarray] = None) -> None:
    """
    Write SAXS data in ATSAS .dat format: plain 3 columns (q, intensity, errors),
    no headers or YAML. Pipeline convention: q in nm^-1; ATSAS returns Rg/Dmax in nm when given nm^-1.

    If sigma is None, errors are set to 4% of I (ATSAS convention when errors absent).
    """
    q = np.asarray(q)
    I = np.asarray(I)
    if sigma is None or not np.all(np.isfinite(sigma)) or np.any(sigma <= 0):
        sigma = 0.04 * np.maximum(I, 1e-300)
    else:
        sigma = np.asarray(sigma)
    with open(filename, 'w') as f:
        for i in range(len(q)):
            f.write(f"{q[i]}\t{I[i]}\t{sigma[i]}\n")


def read_saxs(filename):
    """
    Read SAXS data and metadata from a file with YAML metadata and CSV data.
    
    Parameters:
    filename (str): Input file path
    
    Returns:
    tuple: (wavenumber, intensity, metadata)
        wavenumber (numpy.ndarray): Array of q-values
        intensity (numpy.ndarray): Array of intensity values
        metadata (dict): Dictionary containing metadata
    """
    df, _, metadata = read_data(filename)

    # Extract arrays
    wavenumber = df['q'].to_numpy()
    intensity = df['intensity'].to_numpy()
    
    sigma = None
    if 'sigma' in df:
        sigma = df['sigma'].to_numpy()
    
    return wavenumber, intensity, sigma, metadata


def read_chi(filename: str) -> Tuple[np.ndarray, np.ndarray]:
    """
    Read q and intensity from a .chi file (two-column format with header).
    Returns (q, intensity) arrays.
    """
    data_lines = []
    header_passed = False
    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            if len(parts) == 2:
                try:
                    float(parts[0])
                    float(parts[1])
                    header_passed = True
                except ValueError:
                    pass
            if header_passed and len(parts) == 2:
                data_lines.append(line)
    if not data_lines:
        raise ValueError(f"No valid data found in .chi file: {filename}")
    q, I = np.loadtxt(data_lines, unpack=True)
    return q, I


def ensure_q_nm(
    q: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """
    Ensure q is in nm^-1 (pipeline convention). If q looks like Å^-1 (typical max < 0.6),
    convert by q_nm = q_angstrom * 10. Returns (q, I, sigma) unchanged or with q converted.
    """
    q = np.asarray(q, dtype=float)
    q_max = np.max(q)
    # q in nm^-1 is typically ~0.05–5; q in Å^-1 is ~0.005–0.5
    if q_max > 0 and q_max < 0.6:
        q = q * 10.0
    return q, np.asarray(I, dtype=float), sigma


def load_saxs_1d_any(filename: str) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """
    Load q, I and optionally sigma from a SAXS 1D file.
    Tries read_saxs (YAML+CSV), then two-column .chi-style, then any two-column numeric.
    Returns (q, I, sigma); sigma is None if not available.
    """
    try:
        q, I, sigma, _ = read_saxs(filename)
        return np.asarray(q), np.asarray(I), np.asarray(sigma) if sigma is not None else None
    except (ValueError, KeyError):
        pass
    try:
        q, I = read_chi(filename)
        return np.asarray(q), np.asarray(I), None
    except (ValueError, Exception):
        pass
    data_lines = []
    with open(filename, 'r') as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2:
                try:
                    a, b = float(parts[0]), float(parts[1])
                    data_lines.append((a, b))
                except ValueError:
                    pass
    if len(data_lines) < 3:
        raise ValueError(f"Cannot load 1D SAXS data from {filename}")
    arr = np.array(data_lines)
    q, I = np.sort(arr[:, 0]), arr[:, 1][np.argsort(arr[:, 0])]
    return q, I, None


def find_guinier_region(
    q: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray] = None,
    n_min: int = 5,
    qrg_max: float = 1.3,
    r2_min: float = 0.9,
    max_pts: int = 80,
    try_sliding: bool = True,
) -> Optional[Dict]:
    """
    Find the Guinier region and fit Rg, I(0).
    ln(I) = ln(I0) - (Rg²/3)*q²; valid for q*Rg < ~1.3.

    Strategy (aligned with AUTORG where possible):
    - Tries contiguous ranges: when try_sliding is True, considers intervals that may not
      start at the first point (AUTORG does this: e.g. "Points 52 to 132").
    - Among all fits with q_max*Rg < qrg_max and R² >= r2_min, selects the one with
      the *largest* number of points (largest acceptable Guinier range), not best R²,
      to avoid favouring too-short ranges.

    Returns dict with keys: rg, i0, q_min, q_max, r_squared, n_points, sigma_rg, sigma_i0;
    or None if no valid fit. sigma_rg/sigma_i0 are from the fit covariance (underestimate
    vs AUTORG, which also uses variation across intervals).
    """
    q = np.asarray(q, dtype=float)
    I = np.asarray(I, dtype=float)
    if sigma is not None:
        sigma = np.asarray(sigma, dtype=float)
    idx = np.argsort(q)
    q, I = q[idx], I[idx]
    if sigma is not None:
        sigma = sigma[idx]
    valid = I > 0
    if np.sum(valid) < n_min:
        return None
    q, I = q[valid], I[valid]
    if sigma is not None:
        sigma = sigma[valid]
    n = len(q)
    best = None
    best_n_pts = -1

    def fit_interval(i_start: int, n_pts: int):
        if i_start + n_pts > n:
            return None
        q_sub = q[i_start : i_start + n_pts]
        I_sub = I[i_start : i_start + n_pts]
        sig_sub = sigma[i_start : i_start + n_pts] if sigma is not None else None
        x = q_sub ** 2
        y = np.log(I_sub)
        if sig_sub is not None and np.all(sig_sub > 0):
            w = (I_sub / sig_sub) ** 2
        else:
            w = None
        try:
            if w is not None:
                coeffs = np.polyfit(x, y, 1, w=w)
            else:
                coeffs = np.polyfit(x, y, 1)
        except Exception:
            return None
        slope, intercept = coeffs[0], coeffs[1]
        if slope >= 0:
            return None
        rg = np.sqrt(-3.0 * slope)
        i0 = np.exp(intercept)
        if q_sub[-1] * rg > qrg_max:
            return None
        y_fit = intercept + slope * x
        ss_res = np.sum((y - y_fit) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
        if r2 < r2_min:
            return None
        # Uncertainty from fit: var(slope) and var(intercept) via residual variance
        dof = n_pts - 2
        if dof > 0 and ss_res >= 0:
            res_var = ss_res / dof
            x_mean = np.mean(x)
            sxx = np.sum((x - x_mean) ** 2)
            if sxx > 0:
                var_slope = res_var / sxx
                var_intercept = res_var * (1.0 / n_pts + x_mean ** 2 / sxx)
                # Rg = sqrt(-3*slope) => dRg/d(slope) = -3/(2*sqrt(-3*slope)) = -3/(2*Rg) up to sign
                sigma_rg = 0.5 * (3.0 / rg) * (var_slope ** 0.5) if slope != 0 else np.nan
                sigma_i0 = i0 * (var_intercept ** 0.5)
            else:
                sigma_rg = sigma_i0 = np.nan
        else:
            sigma_rg = sigma_i0 = np.nan
        return {
            'rg': float(rg),
            'i0': float(i0),
            'q_min': float(q_sub[0]),
            'q_max': float(q_sub[-1]),
            'r_squared': float(r2),
            'n_points': n_pts,
            'sigma_rg': float(sigma_rg) if not np.isnan(sigma_rg) else None,
            'sigma_i0': float(sigma_i0) if not np.isnan(sigma_i0) else None,
        }

    starts = [0] if not try_sliding else range(0, max(1, n - n_min + 1))
    for i_start in starts:
        for n_pts in range(n_min, min(n - i_start, max_pts) + 1):
            cand = fit_interval(i_start, n_pts)
            if cand is not None and (best is None or n_pts > best_n_pts):
                best = cand
                best_n_pts = n_pts
    return best


def find_porod_region(
    q: np.ndarray,
    I: np.ndarray,
    n_min: int = 5,
    slope_nominal: float = -4.0,
    slope_tol: float = 0.5,
    max_pts: int = 60,
    Rg: Optional[float] = None,
    qR_min: float = 2.0,
) -> Optional[Dict]:
    """
    Find the Porod region at high q where I(q) ∝ q^(-4).

    When Rg is provided, uses the theoretical high-q condition: Porod's law is valid
    for q*R >> 1; we require q >= qR_min/Rg (default qR_min=2, so q >= 2/Rg).
    Only points in this range are used. If the data do not extend into this range
    (q_max < qR_min/Rg), no fit is performed and a dict with
    theoretical_range_absent=True is returned so callers can flag it.

    When Rg is None, the search uses the rightmost points (legacy behavior) and
    the result will have theoretical_range_checked=False.

    Fits log(I) vs log(q); slope should be ≈ -4.
    Assumes q and Rg in consistent units (e.g. q in 1/nm, Rg in nm).

    Returns:
        On success: dict with slope, K (I = K*q^slope), q_min, q_max, n_points,
            and theoretical_range_used=True when Rg was given.
        When Rg given but data don't reach theoretical range: dict with
            theoretical_range_absent=True, q_min_required, q_max_data, Rg.
        When no valid slope found: None.
    """
    q = np.asarray(q, dtype=float)
    I = np.asarray(I, dtype=float)
    valid = (q > 0) & (I > 0)
    if np.sum(valid) < n_min:
        return None
    q, I = q[valid], I[valid]
    idx = np.argsort(q)
    q, I = q[idx], I[idx]
    n = len(q)
    q_max_data = float(q[-1])

    if Rg is not None and Rg > 0:
        q_min_theory = qR_min / Rg
        if q_max_data < q_min_theory:
            return {
                'theoretical_range_absent': True,
                'q_min_required': q_min_theory,
                'q_max_data': q_max_data,
                'Rg': Rg,
            }
        mask = q >= q_min_theory
        q_porod = q[mask]
        I_porod = I[mask]
        n_porod = len(q_porod)
        if n_porod < n_min:
            return {
                'theoretical_range_absent': True,
                'q_min_required': q_min_theory,
                'q_max_data': q_max_data,
                'Rg': Rg,
            }
        q, I, n = q_porod, I_porod, n_porod
    else:
        q_min_theory = None

    best = None
    best_err = np.inf
    for n_pts in range(n_min, min(n, max_pts) + 1):
        q_sub = q[-n_pts:]
        I_sub = I[-n_pts:]
        x = np.log(q_sub)
        y = np.log(I_sub)
        try:
            coeffs = np.polyfit(x, y, 1)
        except Exception:
            continue
        slope, logK = coeffs[0], coeffs[1]
        if abs(slope - slope_nominal) > slope_tol:
            continue
        err = abs(slope - slope_nominal)
        if err < best_err:
            best_err = err
            best = {
                'slope': float(slope),
                'K': float(np.exp(logK)),
                'q_min': float(q_sub[0]),
                'q_max': float(q_sub[-1]),
                'n_points': n_pts,
            }
            if Rg is not None and Rg > 0:
                best['theoretical_range_used'] = True
                best['q_min_required'] = q_min_theory
            else:
                best['theoretical_range_checked'] = False
    return best


def read_reference_sub_dat(filename: str) -> Tuple[np.ndarray, np.ndarray, str]:
    """
    Read reference subtracted .dat file (format: header with Parent(s): sample.chi buffer.chi, then two-column q I).
    Returns (q, intensity, sample_chi_basename) where sample_chi_basename is the stem of the sample .chi (e.g. 0002_ihs27_95.9).
    """
    sample_basename = ""
    data_lines = []
    header_passed = False
    parent_re = re.compile(r"Parent\s*\(\s*s\s*\)\s*:\s*(.+)", re.IGNORECASE)  # "Parent(s): path1 path2"
    with open(filename, "r") as f:
        for line in f:
            line_strip = line.strip()
            m = parent_re.match(line_strip)
            if m:
                # Paths can be space-separated; first is sample .chi, second is buffer .chi
                paths = m.group(1).strip().split()
                if paths:
                    sample_path = paths[0].replace("\\", "/")
                    sample_basename = os.path.basename(sample_path)
                    if sample_basename.lower().endswith(".chi"):
                        sample_basename = sample_basename[:-4]
            parts = line.split()
            if len(parts) == 2:
                try:
                    float(parts[0])
                    float(parts[1])
                    header_passed = True
                    data_lines.append(line)  # only append lines that are actually two numbers (skip e.g. "range-from: 1")
                except ValueError:
                    pass
    if not data_lines:
        raise ValueError(f"No valid data found in reference sub .dat file: {filename}")
    if not sample_basename:
        raise ValueError(f"No Parent(s) line with sample .chi path in: {filename}")
    q, I = np.loadtxt(data_lines, unpack=True)
    return q, I, sample_basename


def integration_comparison_metric(
    q1: np.ndarray, I1: np.ndarray,
    q2: np.ndarray, I2: np.ndarray,
    q_min: Optional[float] = None,
    q_max: Union[float, str] = 6.0,
    eps: float = 1.0e-0,
) -> float:
    """
    Compute int_{q0}^{q_max} 2 * |I1(q) - I2(q)| / (|I1(q)|*|I2(q)| + eps) on a common q grid.
    One curve is interpolated to the other's q; integration uses trapezoidal rule.
    q_max: upper limit for integration (default 6.0). Use "auto" for old behavior: min of the two curves' max q.
    """
    if q_min is None:
        q_min = max(np.min(q1), np.min(q2))
    if q_max == "auto":
        q_max_val = min(np.max(q1), np.max(q2))
    else:
        q_max_val = float(q_max)
    q_common = np.sort(np.unique(np.concatenate([q1, q2])))
    q_common = q_common[(q_common >= q_min) & (q_common <= q_max_val)]
    if len(q_common) < 2:
        return np.nan
    I1_interp = np.interp(q_common, q1, I1)
    I2_interp = np.interp(q_common, q2, I2)
    denom = np.abs(I1_interp) * np.abs(I2_interp) + eps
    integrand = 2.0 * np.abs(I1_interp - I2_interp) / denom
    return float(np.trapz(integrand, q_common))


def _make_yaml_safe(obj):
    """Convert numpy/types to native Python so yaml.dump produces clean YAML."""
    if isinstance(obj, dict):
        return {k: _make_yaml_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_yaml_safe(x) for x in obj]
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def write_data(filename, data: pd.DataFrame, metadata):
    """
    Write generic tabular data and metadata to a file using YAML for metadata and CSV for data.

    Parameters:
    filename (str): Output file path
    data (pd.DataFrame): Tabular data to be written as CSV
    metadata (dict): Dictionary containing metadata
    """
    metadata = _make_yaml_safe(metadata)
    with open(filename, 'w') as f:
        # Write metadata in YAML format
        f.write("# Data File\n")
        f.write("# Metadata in YAML format\n")
        f.write("---\n")
        yaml.dump(metadata, f, default_flow_style=False)
        f.write("...\n")

        # Write data in CSV format
        f.write("\n# Data in CSV format\n")
        data.to_csv(f, index=False)


def read_data(filename):
    """
    Read generic tabular data and metadata from a file with YAML metadata and CSV data.

    Parameters:
    filename (str): Input file path

    Returns:
    tuple: (data, columns_as_arrays, metadata)
        data (pd.DataFrame): Tabular data
        columns_as_arrays (tuple): Tuple of NumPy arrays corresponding to DataFrame columns
        metadata (dict): Dictionary containing metadata
    """
    with open(filename, 'r') as f:
        content = f.read()

    # Split content into YAML and CSV sections
    yaml_start = content.find("---\n") + 4
    yaml_end = content.find("\n...\n")

    if yaml_start == 3 or yaml_end == -1:
        raise ValueError("Invalid file format: YAML delimiters not found")

    # Extract and parse YAML metadata
    yaml_text = content[yaml_start:yaml_end]
    metadata = yaml.safe_load(yaml_text)

    # Extract and parse CSV data
    csv_start = content.find("\n# Data in CSV format\n") + len("\n# Data in CSV format\n")
    csv_text = content[csv_start:]

    # Use pandas to read CSV data
    df = pd.read_csv(StringIO(csv_text))

    # Return DataFrame and tuple of its columns as NumPy arrays
    columns_as_arrays = tuple(df[col].to_numpy() for col in df.columns)

    return df, columns_as_arrays, metadata


def get_pipeline_description(pipeline_name: str) -> str:
    """
    Read and parse a pipeline description from a .txt file in the pipelines directory.

    Parameters:
    pipeline_name (str): Name of the pipeline (without .txt extension)

    Returns:
    str: A readable description of the pipeline in natural language

    Raises:
    FileNotFoundError: If the pipeline description file doesn't exist
    """
    pipeline_file = os.path.join(_autosaxs_dir, 'pipelines', f'{pipeline_name}.txt')

    if not os.path.exists(pipeline_file):
        raise FileNotFoundError(f"Pipeline description file not found: {pipeline_file}")

    with open(pipeline_file, 'r') as f:
        content = f.read()

    # Parse the structured content
    description_parts = []

    # Extract Description section if present
    desc_match = re.search(r'<Description>\s*(.*?)\s*</Description>', content, re.DOTALL)
    if desc_match:
        desc_part = desc_match.group(1).strip()
        if desc_part:
            description_parts.append(f"Description: {desc_part}")

    # Extract Purpose section if present
    purpose_match = re.search(r'<Purpose>\s*(.*?)\s*</Purpose>', content, re.DOTALL)
    if purpose_match:
        description_parts.append(f"The purpose of the pipeline: {purpose_match.group(1).strip()}")

    # Extract Steps section if present
    steps_match = re.search(r'<Steps>\s*(.*?)\s*</Steps>', content, re.DOTALL)
    if steps_match:
        steps = steps_match.group(1).strip()
        description_parts.append(f"Processing Steps:\n{steps}")

    # Helper function to parse file structure sections
    def parse_file_structure(text):
        lines = [line.strip() for line in text.strip().split('\n') if line.strip()]
        parsed_lines = []

        for line in lines:
            parts = line.split('#', 1)
            file_part = parts[0].strip()
            desc_part = parts[1].strip() if len(parts) > 1 else None

            # Parse the notation
            if file_part.endswith(' inf'):
                count_desc = "an arbitrary number of"
                file_part = file_part[:-4].strip()
            elif any(char.isdigit() for char in file_part.split()[-1]):
                # Extract number from the end
                file_parts = file_part.split()
                if file_parts[-1].isdigit():
                    count = int(file_parts[-1])
                    count_desc = f"exactly {count}"
                    file_part = ' '.join(file_parts[:-1])
                else:
                    count_desc = "a specific number of"
            else:
                count_desc = "a single"

            file_or_files = 'file' if 'single' in count_desc else 'files'
            
            if desc_part:
                parsed_lines.append(f"{count_desc} {file_part} {file_or_files} ({desc_part})")
            else:
                parsed_lines.append(f"{count_desc} {file_part} {file_or_files}")

        return parsed_lines
    
    description_parts.append(
        'For the pipeline to work as expected you should provide an algorithm with an input directory containing your data, structure of which should strictly follow the requirements which are listed below.'
    )

    # Extract Input Structure section if present
    input_match = re.search(r'<Input Structure>\s*(.*?)\s*</Input Structure>', content, re.DOTALL)
    if input_match:
        input_desc = parse_file_structure(input_match.group(1))
        input_desc = '\n'.join(input_desc)
        description_parts.append(f"Input Requirements:\n{input_desc}")

    description_parts.append(
        'Some outputs are generated as a product of the pipeline work.'
    )
    
    # Extract Output Structure section if present
    output_match = re.search(r'<Output Structure>\s*(.*?)\s*</Output Structure>', content, re.DOTALL)
    if output_match:
        output_desc = parse_file_structure(output_match.group(1))
        output_desc = '\n'.join(output_desc)
        description_parts.append(f"Output Files:\n{output_desc}")

    # If no structured content found, return the raw content
    if not description_parts:
        return content.strip()

    return "\n\n".join(description_parts), pipeline_file


def get_pipeline_paths(description_file, directory_path, check=True):
    """
    Check if a directory satisfies the structure described in a file and return matching file paths.
    
    Args:
        description_file: Path to the file containing the structure description
        directory_path: Path to the directory to check
        
    Raises:
        AssertionError: If the directory doesn't satisfy the description or the description file has an invalid format
        
    Returns:
        Dictionary with path names as keys and lists of matching file paths as values
    """
    # Read the description file
    with open(description_file, 'r') as f:
        content = f.read()
    
    # Extract the content between <Input Structure> tags
    match = re.search(r'<Input Structure>(.*?)</Input Structure>', content, re.DOTALL)
    if not match:
        raise AssertionError("No <Input Structure> tags found in the description file")
    
    lines = match.group(1).strip().split('\n')
    result = {}
    
    # Process each line in the description
    for line in lines:
        # Skip empty lines
        line = line.strip()
        if not line:
            continue
        
        # Split the line into parts, removing comments
        parts = line.split('#', 1)[0].strip()
        if not parts:
            continue
            
        parts = parts.split()
        if len(parts) < 2:
            raise AssertionError(f"Invalid line format: {line}")
            
        # Extract path name and file mask
        path_name = parts[0]
        file_mask = parts[1]
        
        # Extract count specification if present
        count_spec = None
        if len(parts) > 2:
            count_spec = parts[2]
        
        # Determine expected count range
        if count_spec is None:
            min_count, max_count = 1, 1
        elif count_spec == '*':
            min_count, max_count = 0, float('inf')
        elif count_spec == '+':
            min_count, max_count = 1, float('inf')
        elif count_spec.startswith('[') and count_spec.endswith(']'):
            # Range format [n, m]
            range_match = re.match(r'\[(\d+),\s*(\d+)\]', count_spec)
            if range_match:
                min_count, max_count = int(range_match.group(1)), int(range_match.group(2))
            else:
                raise AssertionError(f"Invalid range specification: {count_spec}")
        else:
            # Single integer
            try:
                min_count = max_count = int(count_spec)
            except ValueError:
                raise AssertionError(f"Invalid count specification: {count_spec}")
        
        # Find files matching the mask in the directory
        pattern = os.path.join(directory_path, file_mask)
        matching_files = glob.glob(pattern)
        actual_count = len(matching_files)
        
        # Check if the actual count is within the expected range
        if check and not (min_count <= actual_count <= max_count):
            raise AssertionError(f"Directory structure mismatch: Expected {min_count}-{max_count} files matching '{file_mask}', found {actual_count}")
        
        # Add to result dictionary with the list of matching files
        result[path_name] = [matching_files, (min_count, max_count), pattern]
    
    # some files may be present in several groups simultaneously
    # if a smaller group is included to a larger group then it should be deleted from the larger group
    # if two groups have an intersection or both groups are singular and coincide - throw an error
    for group_name_0, group_name_1 in itertools.combinations(result.keys(), 2):
        entry0, entry1 = result[group_name_0][0], result[group_name_1][0]
        
        entry0_set, entry1_set = set(entry0), set(entry1)
        assert entry0_set < entry1_set or entry1_set < entry0_set or not (entry0_set & entry1_set), 'Description contains overlapping masks!'
        
        if entry1_set < entry0_set:
            for e1 in entry1:
                entry0.remove(e1)
        elif entry0_set < entry1_set:
            for e0 in entry0:
                entry1.remove(e0)
        
        result[group_name_0][0] = entry0
        result[group_name_1][0] = entry1
    
    for group_name in result:
        entry, (_, max_count), _ = result[group_name]
        actual_count = len(entry)
        if actual_count > max_count:
            raise AssertionError(f"Directory structure mismatch: Expected at maximum {max_count} files matching '{file_mask}', found {actual_count}")
        
    return result


def load_config(src_path):
    with open(src_path, 'r') as f:
        return yaml.safe_load(f)


def save_config(config, dest_path):
    with open(dest_path, 'w') as f:
        yaml.dump(config, f)


def update_config(config, config_file, *keys, values: dict):
    keys = list(keys)

    conf = config
    for k in keys:
        if k not in conf:
            conf[k] = {}
        conf = conf[k]
    
    conf.update(values)
    save_config(config, config_file)


def read_bodies_cif(src_path):
    coords = []
    with open(src_path, 'r') as fread:
        lines = fread.readlines()

    # Find the start of the _atom_site loop
    in_atom_site = False
    for line in lines:
        if line.startswith('_atom_site.Cartn_x'):
            in_atom_site = True
            continue
        if in_atom_site and (line.strip() == '' or line.startswith('#')):
            break
        if in_atom_site and line.startswith('_'):
            continue
        if in_atom_site and line.strip():
            parts = line.strip().split()
            if len(parts) >= 12:  # Ensure enough columns
                x, y, z = float(parts[-7]), float(parts[-6]), float(parts[-5])
                coords.append([x, y, z])

    coords = np.array(coords)
    print(f"Loaded {len(coords)} dummy atoms.")

    # Create ASE Atoms object with dummy atoms (e.g., all Carbon)
    atoms = Atoms('C' * len(coords), positions=coords)

    return atoms


##### LLM UTILS ######


def encode_image(image_path):
    """Encode image to base64 string"""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')


def get_image_messages(image_path, text):
    base64_image = encode_image(image_path)
    messages = [
        {'role': 'user',
         'content': [{
             'type': 'text',
             'text': text
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{base64_image}"
                }
            }]
        }
    ]

    return messages


##### MATH UTILS ######


def calc_chi2(I0, I1, sigma_exp):
    return 1 / (I0.shape[0] - 1) * np.sum( ((I0 - I1) / sigma_exp) ** 2 )


# Volume per dummy atom (Å³) for DAM models; used in compute_dammif_descriptors.
V_ATOM_DAM = 20.0


def compute_dammif_descriptors(atoms: Atoms) -> Dict[str, float]:
    """
    Compute Rg, Dmax, V, and f_ratio from a DAM CIF (ASE Atoms).
    Coords assumed in Å. Rg and Dmax returned in nm; V in Å³.
    """
    pos = np.asarray(atoms.get_positions(), dtype=np.float64)
    n = len(pos)
    if n == 0:
        return {"Rg": np.nan, "Dmax": np.nan, "V": np.nan, "f_ratio": np.nan}
    com = pos.mean(axis=0)
    r_sq = np.sum((pos - com) ** 2, axis=1)
    rg_ang = np.sqrt(np.mean(r_sq))
    rg_nm = rg_ang / 10.0
    dists = cdist(pos, pos, metric="euclidean")
    dmax_ang = float(np.max(dists))
    dmax_nm = dmax_ang / 10.0
    v = n * V_ATOM_DAM
    f_ratio = rg_nm / dmax_nm if dmax_nm > 0 else np.nan
    return {"Rg": rg_nm, "Dmax": dmax_nm, "V": v, "f_ratio": f_ratio}


##### STRING UTILS ######


def map_sample_files_to_buffer_files(sample_paths, buffer_paths):
    """
    Align sample and buffer 1D paths by base name, matching sample name containing buffer name
    name convention - buffer path ends with "_buffer*.ext*", sample path with "_sample*.ext*"
    """
    aligned_pairs = []
    not_paired = []
    overlapped = []
    for s_p in sample_paths:
        _, s_ext = os.path.splitext(os.path.basename(s_p))
        s_base = os.path.basename(s_p).replace(f'_sample{s_ext}', '')
        for b_p in buffer_paths:
            _, b_ext = os.path.splitext(os.path.basename(b_p))
            b_base = os.path.basename(b_p).replace(f'_buffer{b_ext}', '')
            if b_base in s_base:
                if aligned_pairs:
                    prev_s_p, _ = aligned_pairs[-1]
                    if prev_s_p == s_p and s_p not in overlapped:
                        overlapped.append(s_p)
                aligned_pairs.append((s_p, b_p))
        if not aligned_pairs or aligned_pairs[-1][0] != s_p:
            not_paired.append(s_p)
    return {'aligned_pairs': aligned_pairs, 'overlapped': overlapped, 'not_paired': not_paired}


##### DENSITY AND ISOSURFACE CALCULATION UTILS ######


def calculate_atoms_density_and_isosurface(
    atoms: Atoms, grid_size: int = 64, isosurface_sigma: float = 1.5,
    isosurface_level: Optional[float] = None, padding_factor: float = 3.0
) -> Tuple[np.ndarray, float, np.ndarray, np.ndarray]:
    """
    Calculate density grid and isosurface level for an ASE Atoms object.
    
    Parameters:
    -----------
    atoms : ase.Atoms
        Atomic structure.
    grid_size : int, default 64
        Grid resolution for density calculation.
    isosurface_sigma : float, default 1.5
        Standard deviation for Gaussian kernel in density calculation.
    isosurface_level : float, optional
        Density level for isosurface. If None, auto-calculated as 0.2 * density.max().
    padding_factor : float, default 3.0
        Padding factor multiplied by isosurface_sigma to extend the grid.
    
    Returns:
    --------
    tuple: (density, isosurface_level, min_coords, max_coords)
        density : np.ndarray
            3D density grid
        isosurface_level : float
            Density level for isosurface extraction
        min_coords : np.ndarray
            Minimum coordinates of the grid
        max_coords : np.ndarray
            Maximum coordinates of the grid
    """
    points = atoms.get_positions()
    min_coords = points.min(axis=0)
    max_coords = points.max(axis=0)
    
    # Add padding
    padding = padding_factor * isosurface_sigma
    min_coords -= padding
    max_coords += padding
    
    # Create 3D grid
    grid, edges = np.histogramdd(points, bins=grid_size, range=list(zip(min_coords, max_coords)))
    
    # Apply Gaussian filter
    density = gaussian_filter(grid, sigma=isosurface_sigma)
    
    # Determine isovalue if not provided
    if isosurface_level is None:
        isosurface_level = 0.2 * density.max()
    
    return density, isosurface_level, min_coords, max_coords


def _point_in_cylinder(x: np.ndarray, y: np.ndarray, z: np.ndarray, r: float, h: float) -> np.ndarray:
    """Check if points are inside a cylinder (radius r, height h along z-axis, centered at origin)."""
    r_xy = np.sqrt(x**2 + y**2)
    in_radius = r_xy <= r
    in_height = (z >= -h/2) & (z <= h/2)
    return in_radius & in_height


def _point_in_dumbbell(x: np.ndarray, y: np.ndarray, z: np.ndarray, 
                       r1: float, r2: float, d: float) -> np.ndarray:
    """Check if points are inside a dumbbell (two spheres connected)."""
    # Center of first sphere at (-d/2, 0, 0), second at (d/2, 0, 0)
    dist1 = np.sqrt((x + d/2)**2 + y**2 + z**2)
    dist2 = np.sqrt((x - d/2)**2 + y**2 + z**2)
    return (dist1 <= r1) | (dist2 <= r2)


def _point_in_ellipsoid(x: np.ndarray, y: np.ndarray, z: np.ndarray,
                        a: float, b: float, c: float) -> np.ndarray:
    """Check if points are inside an ellipsoid (semiaxes a, b, c)."""
    return (x/a)**2 + (y/b)**2 + (z/c)**2 <= 1.0


def _point_in_elliptic_cylinder(x: np.ndarray, y: np.ndarray, z: np.ndarray,
                                a: float, c: float, h: float) -> np.ndarray:
    """Check if points are inside an elliptic cylinder (semiaxes a, c, height h along z-axis)."""
    r_xy = (x/a)**2 + (y/c)**2
    in_radius = r_xy <= 1.0
    in_height = (z >= -h/2) & (z <= h/2)
    return in_radius & in_height


def _point_in_hollow_cylinder(x: np.ndarray, y: np.ndarray, z: np.ndarray,
                              ro: float, ri: float, h: float) -> np.ndarray:
    """Check if points are inside a hollow cylinder (outer radius ro, inner radius ri, height h)."""
    r_xy = np.sqrt(x**2 + y**2)
    in_outer = r_xy <= ro
    in_inner = r_xy >= ri
    in_height = (z >= -h/2) & (z <= h/2)
    return in_outer & in_inner & in_height


def _point_in_hollow_sphere(x: np.ndarray, y: np.ndarray, z: np.ndarray,
                            ro: float, ri: float) -> np.ndarray:
    """Check if points are inside a hollow sphere (outer radius ro, inner radius ri)."""
    r = np.sqrt(x**2 + y**2 + z**2)
    return (r <= ro) & (r >= ri)


def _point_in_parallelepiped(x: np.ndarray, y: np.ndarray, z: np.ndarray,
                             a: float, b: float, c: float) -> np.ndarray:
    """Check if points are inside a parallelepiped (sides a, b, c, centered at origin)."""
    return ((x >= -a/2) & (x <= a/2) & 
            (y >= -b/2) & (y <= b/2) &
            (z >= -c/2) & (z <= c/2))


def _point_in_rotation_ellipsoid(x: np.ndarray, y: np.ndarray, z: np.ndarray,
                                 a: float, c: float) -> np.ndarray:
    """Check if points are inside a rotation ellipsoid (semiaxes a, a, c - rotation around z)."""
    r_xy = np.sqrt(x**2 + y**2)
    return (r_xy/a)**2 + (z/c)**2 <= 1.0


def calculate_shape_density_and_isosurface(
    shape_tuple: Tuple[str, Dict[str, float]], grid_size: int = 64,
    isosurface_level: Optional[float] = None, padding: float = 1.0
) -> Tuple[np.ndarray, float, np.ndarray, np.ndarray]:
    """
    Calculate density grid and isosurface level for a shape from BODIES_SHAPES.
    
    Parameters:
    -----------
    shape_tuple : tuple
        Tuple containing (shape_name, shape_params_dict) where:
        - shape_name: str, name of the shape from BODIES_SHAPES
        - shape_params_dict: dict, parameters for the shape
    grid_size : int, default 64
        Grid resolution for density calculation.
    isosurface_level : float, optional
        Density level for isosurface. If None, defaults to 0.5.
    padding : float, default 1.0
        Padding (in Å) to extend the grid beyond the shape boundaries.
    
    Returns:
    --------
    tuple: (density, isosurface_level, min_coords, max_coords)
        density : np.ndarray
            3D density grid (1 inside shape, 0 outside)
        isosurface_level : float
            Density level for isosurface extraction (default 0.5)
        min_coords : np.ndarray
            Minimum coordinates of the grid
        max_coords : np.ndarray
            Maximum coordinates of the grid
    """
    shape_name, shape_params = shape_tuple
    
    # Determine bounding box based on shape type and parameters
    if shape_name == 'cylinder':
        r, h = shape_params['r'], shape_params['h']
        max_dim = max(2*r, h)
        min_coords = np.array([-max_dim/2 - padding, -max_dim/2 - padding, -h/2 - padding])
        max_coords = np.array([max_dim/2 + padding, max_dim/2 + padding, h/2 + padding])
    elif shape_name == 'dumbbell':
        r1, r2, d = shape_params['r1'], shape_params['r2'], shape_params['d']
        max_r = max(r1, r2)
        min_coords = np.array([-d/2 - max_r - padding, -max_r - padding, -max_r - padding])
        max_coords = np.array([d/2 + max_r + padding, max_r + padding, max_r + padding])
    elif shape_name == 'ellipsoid':
        a, b, c = shape_params['a'], shape_params['b'], shape_params['c']
        min_coords = np.array([-a - padding, -b - padding, -c - padding])
        max_coords = np.array([a + padding, b + padding, c + padding])
    elif shape_name == 'elliptic-cylinder':
        a, c_semiaxis, h = shape_params['a'], shape_params['c'], shape_params['h']
        max_r = max(a, c_semiaxis)
        min_coords = np.array([-max_r - padding, -max_r - padding, -h/2 - padding])
        max_coords = np.array([max_r + padding, max_r + padding, h/2 + padding])
    elif shape_name == 'hollow-cylinder':
        ro, ri, h = shape_params['ro'], shape_params['ri'], shape_params['h']
        min_coords = np.array([-ro - padding, -ro - padding, -h/2 - padding])
        max_coords = np.array([ro + padding, ro + padding, h/2 + padding])
    elif shape_name == 'hollow-sphere':
        ro = shape_params['ro']
        min_coords = np.array([-ro - padding, -ro - padding, -ro - padding])
        max_coords = np.array([ro + padding, ro + padding, ro + padding])
    elif shape_name == 'parallelepiped':
        a, b, c = shape_params['a'], shape_params['b'], shape_params['c']
        min_coords = np.array([-a/2 - padding, -b/2 - padding, -c/2 - padding])
        max_coords = np.array([a/2 + padding, b/2 + padding, c/2 + padding])
    elif shape_name == 'rotation-ellipsoid':
        a, c = shape_params['a'], shape_params['c']
        min_coords = np.array([-a - padding, -a - padding, -c - padding])
        max_coords = np.array([a + padding, a + padding, c + padding])
    else:
        raise ValueError(f"Unknown shape name: {shape_name}")
    
    # Create grid coordinates
    x_coords = np.linspace(min_coords[0], max_coords[0], grid_size)
    y_coords = np.linspace(min_coords[1], max_coords[1], grid_size)
    z_coords = np.linspace(min_coords[2], max_coords[2], grid_size)
    
    # Create meshgrid
    X, Y, Z = np.meshgrid(x_coords, y_coords, z_coords, indexing='ij')
    
    # Check which points are inside the shape
    if shape_name == 'cylinder':
        inside = _point_in_cylinder(X, Y, Z, shape_params['r'], shape_params['h'])
    elif shape_name == 'dumbbell':
        inside = _point_in_dumbbell(X, Y, Z, shape_params['r1'], shape_params['r2'], shape_params['d'])
    elif shape_name == 'ellipsoid':
        inside = _point_in_ellipsoid(X, Y, Z, shape_params['a'], shape_params['b'], shape_params['c'])
    elif shape_name == 'elliptic-cylinder':
        inside = _point_in_elliptic_cylinder(X, Y, Z, shape_params['a'], shape_params['c'], shape_params['h'])
    elif shape_name == 'hollow-cylinder':
        inside = _point_in_hollow_cylinder(X, Y, Z, shape_params['ro'], shape_params['ri'], shape_params['h'])
    elif shape_name == 'hollow-sphere':
        inside = _point_in_hollow_sphere(X, Y, Z, shape_params['ro'], shape_params['ri'])
    elif shape_name == 'parallelepiped':
        inside = _point_in_parallelepiped(X, Y, Z, shape_params['a'], shape_params['b'], shape_params['c'])
    elif shape_name == 'rotation-ellipsoid':
        inside = _point_in_rotation_ellipsoid(X, Y, Z, shape_params['a'], shape_params['c'])
    else:
        raise ValueError(f"Unknown shape name: {shape_name}")
    
    # Create density grid (1 inside, 0 outside)
    density = inside.astype(float)
    
    # Set isosurface level (default 0.5 for binary density)
    if isosurface_level is None:
        isosurface_level = 0.5
    
    return density, isosurface_level, min_coords, max_coords


# def get_closest_idx(sorted_arr_1d, scalar, side=None):
#     assert np.all(sorted_arr_1d[:-1] <= sorted_arr_1d[1:]), 'Sorted arrays only'
#     if side is None or np.any(sorted_arr_1d == scalar):
#         return np.argmin(np.abs(sorted_arr_1d - scalar))
#     else:
#         idx = np.where(sorted_arr_1d > scalar)[0].tolist()
#         if not idx:
#             idx = len(sorted_arr_1d)
