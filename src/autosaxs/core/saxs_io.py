"""SAXS 1D / tabular I/O helpers (YAML+CSV .dat, ATSAS .dat, .chi).

Extracted from ``autosaxs.core.utils`` (P5 gradual split). Prefer importing from
this module for new code; ``utils`` re-exports these names temporarily.
"""
from __future__ import annotations

from io import StringIO
from pathlib import PurePosixPath, PureWindowsPath
from typing import Optional, Tuple

import numpy as np
import pandas as pd
import yaml

# Pipeline units convention: q in nm^-1 (inverse nanometers), Rg and lengths in nm.
# PyFAI integrate1d must be called with unit='q_nm^-1'. ATSAS (autorg, datgnom, etc.)
# accept .dat with q in nm^-1 and return Rg, Dmax in nm; use write_saxs_atsas_format for input.


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
    tuple: (wavenumber, intensity, sigma, metadata)
        wavenumber (numpy.ndarray): Array of q-values
        intensity (numpy.ndarray): Array of intensity values
        sigma (numpy.ndarray or None): Uncertainties, if present in the file
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
                    sample_path = paths[0]
                    # "Parent(s)" may contain POSIX or Windows paths, regardless of host OS.
                    p = PureWindowsPath(sample_path) if ("\\" in sample_path or (len(sample_path) >= 2 and sample_path[1] == ":")) else PurePosixPath(sample_path)
                    sample_basename = p.name
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

