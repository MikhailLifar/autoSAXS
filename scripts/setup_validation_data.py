"""
Copy IHS validation data from data/ihs/06-06-2025/cell to validation/ and rename
to follow pipeline conventions:
  - *_AgBh\\d+_*.tif -> raw/*_calib.tif (single calibration file)
  - *_ihs\\d+b_*.tif -> raw/*_buffer.tif
  - *_ihs\\d+_*.tif (no 'b') -> raw/*_sample.tif
  - *.chi -> reference/ (same basename, for validation)
  - config.conf copied from debug/protein_v0_interactive (or similar)
  - Mask: place a file matching mask* (e.g. mask_fti2d_1225.msk) in validation/ for calibration.
"""
import re
import shutil
import os

# Paths relative to workspace root (KurchatovCoop)
WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SOURCE_DIR = os.path.join(WORKSPACE_ROOT, "data", "ihs", "06-06-2025", "cell")
VALIDATION_DIR = os.path.join(WORKSPACE_ROOT, "validation")
CONFIG_SOURCE = os.path.join(WORKSPACE_ROOT, "debug", "protein_v0_interactive", "config.conf")

CALIB_PATTERN = re.compile(r"^(.+)_AgBh\d+_(.+)\.tif$")
BUFFER_PATTERN = re.compile(r"^(.+_ihs\d+)b_(.+)\.tif$")
SAMPLE_PATTERN = re.compile(r"^(.+_ihs\d+)_(.+)\.tif$")  # must not match buffer (no 'b' before _)


def _is_buffer_basename(basename: str) -> bool:
    return bool(BUFFER_PATTERN.match(basename + ".tif"))


def main():
    if not os.path.isdir(SOURCE_DIR):
        raise FileNotFoundError(f"Source directory not found: {SOURCE_DIR}")

    raw_dir = os.path.join(VALIDATION_DIR, "raw")
    ref_dir = os.path.join(VALIDATION_DIR, "reference")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(ref_dir, exist_ok=True)

    calib_done = False
    for name in sorted(os.listdir(SOURCE_DIR)):
        src = os.path.join(SOURCE_DIR, name)
        if not os.path.isfile(src):
            continue
        base, ext = os.path.splitext(name)

        if ext.lower() == ".chi":
            shutil.copy2(src, os.path.join(ref_dir, name))
            continue

        if ext.lower() != ".tif":
            continue

        # Calibration: *_AgBh\d+_*.tif -> first one as *_calib.tif
        m = CALIB_PATTERN.match(name)
        if m:
            if not calib_done:
                # Use a single conventional name so raw/*_calib.tif matches
                dest_name = "0001_calib.tif"
                shutil.copy2(src, os.path.join(raw_dir, dest_name))
                calib_done = True
            continue

        # Buffer: *_ihs\d+b_*.tif -> *_buffer.tif (keep base, add _buffer)
        m = BUFFER_PATTERN.match(name)
        if m:
            dest_name = f"{base}_buffer.tif"
            shutil.copy2(src, os.path.join(raw_dir, dest_name))
            continue

        # Sample: *_ihs\d+_*.tif (not buffer)
        m = SAMPLE_PATTERN.match(name)
        if m and not _is_buffer_basename(base):
            dest_name = f"{base}_sample.tif"
            shutil.copy2(src, os.path.join(raw_dir, dest_name))
            continue

    if not calib_done:
        raise RuntimeError("No calibration file (*_AgBh*_*.tif) found in source.")

    if os.path.isfile(CONFIG_SOURCE):
        shutil.copy2(CONFIG_SOURCE, os.path.join(VALIDATION_DIR, "config.conf"))
    else:
        raise FileNotFoundError(f"Config not found: {CONFIG_SOURCE}")

    print(f"Validation data prepared under {VALIDATION_DIR}")
    print(f"  raw: {len(os.listdir(raw_dir))} files")
    print(f"  reference: {len(os.listdir(ref_dir))} .chi files")


if __name__ == "__main__":
    main()
