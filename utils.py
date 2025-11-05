from typing import Tuple
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

sys.path.append(os.path.expanduser('~/SupervisedML/repos'))

from supervised_ml.whittaker_smooth import whittaker_smooth


##### READING/WRITING FUNCTIONS ######


def read_from_tiff(tiff_path) -> np.ndarray:
    return image.read_image_data(tiff_path)


def write_saxs(filename, wavenumber, intensity, metadata):
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
        'intensity': intensity
    })
    
    with open(filename, 'w') as f:
        # Write metadata in YAML format
        f.write("# SAXS Data File\n")
        f.write("# Metadata in YAML format\n")
        f.write("---\n")
        yaml.dump(metadata, f, default_flow_style=False)
        f.write("...\n")
        
        # Write data in CSV format
        f.write("\n# Data in CSV format\n")
        df.to_csv(f, index=False)

def read_saxs(filename) -> Tuple[np.ndarray, np.ndarray, dict]:
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
    
    # Extract arrays
    wavenumber = df['q'].to_numpy()
    intensity = df['intensity'].to_numpy()
    
    return wavenumber, intensity, metadata


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
    pipeline_file = os.path.join('repos', 'pipelines', f'{pipeline_name}.txt')

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


def get_necessary_paths(description_file, directory_path):
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
        if not (min_count <= actual_count <= max_count):
            raise AssertionError(f"Directory structure mismatch: Expected {min_count}-{max_count} files matching '{file_mask}', found {actual_count}")
        
        # Add to result dictionary with the list of matching files
        if min_count == max_count == 1:
            result[path_name] = matching_files[0]
        else:
            result[path_name] = matching_files
    
    # some files may be present in several groups simultaneously
    # if a smaller group is included to a larger group then it should be deleted from the larger group
    # if two groups have an intersection or both groups are singular and coincide - throw an error
    for group_name_0, group_name_1 in itertools.combinations(result.keys(), 2):
        entry0, entry1 = result[group_name_0], result[group_name_1]
        
        to_str_1 = to_str_0 = False
        if isinstance(entry0, str):
            entry0 = [entry0, ]
            to_str_0 = True
        if isinstance(entry1, str):
            entry1 = [entry1, ]
            to_str_1 = True
        entry0_set, entry1_set = set(entry0), set(entry1)
        assert entry0_set < entry1_set or entry1_set < entry0_set or not (entry0_set & entry1_set), 'Description contains overlapping masks!'
        
        if entry1_set < entry0_set:
            entry0_set, entry1_set = entry1_set, entry0_set
        if entry0_set < entry1_set:
            for e0 in entry0:
                entry1.remove(e0)
        
        if to_str_0:
            entry0 = entry0[0]
        if to_str_1:
            entry1 = entry1[0]
        result[group_name_0] = entry0
        result[group_name_1] = entry1
        
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
