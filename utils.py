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

import time
from contextlib import contextmanager

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DIR = os.path.dirname(os.path.abspath(__file__))

SUPERVISED_ML_DIR = os.path.join(ROOT_DIR, 'supervised_ml')
sys.path.append(SUPERVISED_ML_DIR)
from supervised_ml.whittaker_smooth import whittaker_smooth

GLOBALS_DIR = os.path.join(REPO_DIR, 'global') 
TEMPLATES_DIR = os.path.join(GLOBALS_DIR, 'templates')
with open(os.path.join(GLOBALS_DIR, 'env.yml'), 'r') as fread:
    ENV = yaml.safe_load(fread)
ATSAS_BIN_PREFIX = ENV["ATSAS_BIN_PREFIX"]


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


def write_data(filename, data: pd.DataFrame, metadata):
    """
    Write generic tabular data and metadata to a file using YAML for metadata and CSV for data.

    Parameters:
    filename (str): Output file path
    data (pd.DataFrame): Tabular data to be written as CSV
    metadata (dict): Dictionary containing metadata
    """
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
    pipeline_file = os.path.join(REPO_DIR, 'pipelines', f'{pipeline_name}.txt')

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
