"""
Copy IHS validation data from data/ihs/06-06-2025/cell to validation/ and rename
to follow pipeline conventions:
  - *_AgBh\\d+_*.tif -> raw/*_calib.tif (single calibration file)
  - *_ihs\\d+b_*.tif -> raw/*_buffer.tif
  - *_ihs\\d+_*.tif (no 'b') -> raw/*_sample.tif
  - *.chi -> reference/ (same basename, for validation)
  - sub_\\d+.dat -> reference_subtracted/ (reference subtracted 1D curves; metadata gives sample/buffer .chi)
  - config.conf copied from repos/resources/validation_config.conf (skill-keyed YAML)
  - Mask: place a file matching mask* (e.g. mask_fti2d_1225.msk) in validation/ for calibration.
"""
import re
import shutil
import os

SUB_DAT_PATTERN = re.compile(r"^sub_\d+\.dat$")

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WORKSPACE_ROOT = os.path.abspath(os.path.join(REPO_DIR, ".."))
SOURCE_DIR = os.path.join(WORKSPACE_ROOT, "data", "ihs", "06-06-2025", "cell")
VALIDATION_DIR = os.path.join(WORKSPACE_ROOT, "validation")
CONFIG_SOURCE = os.path.join(REPO_DIR, "resources", "validation_config.conf")

CALIB_PATTERN = re.compile(r"^(.+)_AgBh\d+_(.+)\.tif$")
BUFFER_PATTERN = re.compile(r"^(.+_ihs\d+)b_(.+)\.tif$")
SAMPLE_PATTERN = re.compile(r"^(.+_ihs\d+)_(.+)\.tif$")  # must not match buffer (no 'b' before _)


def _is_buffer_basename(basename: str) -> bool:
    return bool(BUFFER_PATTERN.match(basename + ".tif"))


def _strip_leading_number_codes(name: str) -> str:
    """Remove leading number codes (digits + underscore) from filename/stem. E.g. 0002_ihs27_95.9 -> ihs27_95.9."""
    while True:
        stripped = re.sub(r"^\d+_", "", name)
        if stripped == name:
            return name
        name = stripped
    return name


def main():
    if not os.path.isdir(SOURCE_DIR):
        raise FileNotFoundError(f"Source directory not found: {SOURCE_DIR}")

    raw_dir = os.path.join(VALIDATION_DIR, "raw")
    ref_dir = os.path.join(VALIDATION_DIR, "reference")
    ref_sub_dir = os.path.join(VALIDATION_DIR, "reference_subtracted")
    os.makedirs(raw_dir, exist_ok=True)
    for f in os.listdir(raw_dir):
        if f.lower().endswith(".tif"):
            try:
                os.remove(os.path.join(raw_dir, f))
            except OSError:
                pass
    os.makedirs(ref_dir, exist_ok=True)
    os.makedirs(ref_sub_dir, exist_ok=True)

    # Pair sample with buffer by index; naming so sample base starts with buffer base (alignment: b_base in s_base)
    sample_bases = []
    buffer_bases = []
    for name in sorted(os.listdir(SOURCE_DIR)):
        if not name.lower().endswith(".tif"):
            continue
        base, ext = os.path.splitext(name)
        if BUFFER_PATTERN.match(name):
            buffer_bases.append(base)
        elif SAMPLE_PATTERN.match(name) and not _is_buffer_basename(base):
            sample_bases.append(base)
    sample_to_buffer = dict(zip(sample_bases, buffer_bases)) if len(sample_bases) == len(buffer_bases) else {}

    def _buffer_stem(buf_base: str) -> str:
        """0003_ihs27b_95.6 -> ihs27 (just the ihsNN part; alignment: b_base in s_base)."""
        stem = _strip_leading_number_codes(buf_base)
        m = re.match(r"ihs(\d+)b", stem)
        return f"ihs{m.group(1)}" if m else stem

    calib_done = False
    for name in sorted(os.listdir(SOURCE_DIR)):
        src = os.path.join(SOURCE_DIR, name)
        if not os.path.isfile(src):
            continue
        base, ext = os.path.splitext(name)

        if ext.lower() == ".chi":
            shutil.copy2(src, os.path.join(ref_dir, name))
            continue

        if SUB_DAT_PATTERN.match(name):
            shutil.copy2(src, os.path.join(ref_sub_dir, name))
            continue

        if ext.lower() != ".tif":
            continue

        # Calibration: *_AgBh\d+_*.tif -> first one as *_calib.tif (strip leading number codes)
        m = CALIB_PATTERN.match(name)
        if m:
            if not calib_done:
                stem = _strip_leading_number_codes(base)
                dest_name = f"{stem}_calib.tif" if stem else "main_calib.tif"
                shutil.copy2(src, os.path.join(raw_dir, dest_name))
                calib_done = True
            continue

        # Buffer: ihs27_buffer.tif (b_base = ihs27; alignment: sample base starts with this)
        m = BUFFER_PATTERN.match(name)
        if m:
            stem = _buffer_stem(base)
            dest_name = f"{stem}_buffer.tif"
            shutil.copy2(src, os.path.join(raw_dir, dest_name))
            continue

        # Sample: ihs27_95.9_sample.tif (s_base = ihs27_95.9, so b_base ihs27 in s_base)
        m = SAMPLE_PATTERN.match(name)
        if m and not _is_buffer_basename(base):
            stem = _strip_leading_number_codes(base)
            dest_name = f"{stem}_sample.tif"
            shutil.copy2(src, os.path.join(raw_dir, dest_name))
            continue

    if not calib_done:
        raise RuntimeError("No calibration file (*_AgBh*_*.tif) found in source.")

    if os.path.isfile(CONFIG_SOURCE):
        shutil.copy2(CONFIG_SOURCE, os.path.join(VALIDATION_DIR, "config.conf"))
    else:
        raise FileNotFoundError(f"Config not found: {CONFIG_SOURCE}")

    n_sub = len([n for n in os.listdir(ref_sub_dir) if SUB_DAT_PATTERN.match(n)])
    print(f"Validation data prepared under {VALIDATION_DIR}")
    print(f"  raw: {len(os.listdir(raw_dir))} files")
    print(f"  reference: {len(os.listdir(ref_dir))} .chi files")
    print(f"  reference_subtracted: {n_sub} sub_*.dat files")


if __name__ == "__main__":
    main()
