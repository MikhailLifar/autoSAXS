from typing import Tuple
import yaml
import pandas as pd
import numpy as np
from io import StringIO
import os
import sys
import re

sys.path.append(os.path.expanduser('~/SupervisedML/repos'))

from supervised_ml.whittaker_smooth import whittaker_smooth


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
    tuple: (metadata, wavenumber, intensity)
        metadata (dict): Dictionary containing metadata
        wavenumber (numpy.ndarray): Array of q-values
        intensity (numpy.ndarray): Array of intensity values
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

    return "\n\n".join(description_parts)
