"""
Copy IHS validation data from data/ihs/06-06-2025/cell to validation/ and rename
to follow pipeline conventions:
  - *_AgBh\\d+_*.tif -> raw/*_calib.tif (single calibration file)
  - *_ihs\\d+b_*.tif -> raw/*_buffer.tif
  - *_ihs\\d+_*.tif (no 'b') -> raw/*_sample.tif
  - *.chi -> reference/ (same basename, for validation)
  - sub_\\d+.dat -> reference_subtracted/ (reference subtracted 1D curves; metadata gives sample/buffer .chi)
  - config.conf copied from resources/validation_config.conf (skill-keyed YAML)
  - Mask: place a file matching mask* (e.g. mask_fti2d_1225.msk) in validation/ for calibration.
  - Optional monodisperse references: when PROTOCOL_MONO_2D (or ~/tmp/test_autosaxs/test_protocol/2d)
    exists, sync validation/reference_mono/ (Guinier/Kratky/GNOM/.out + manifest) from that tree.
"""
import re
import shutil
import os

import yaml

SUB_DAT_PATTERN = re.compile(r"^sub_\d+\.dat$")

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WORKSPACE_ROOT = os.path.abspath(os.path.join(REPO_DIR, ".."))
SOURCE_DIR = os.path.join(WORKSPACE_ROOT, "data", "ihs", "06-06-2025", "cell")
VALIDATION_DIR = os.path.join(WORKSPACE_ROOT, "validation")
CONFIG_SOURCE = os.path.join(REPO_DIR, "resources", "validation_config.conf")
DEFAULT_PROTOCOL_MONO_2D = os.path.expanduser("~/tmp/test_autosaxs/test_protocol/2d")

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

    protocol_mono = os.environ.get("PROTOCOL_MONO_2D", DEFAULT_PROTOCOL_MONO_2D)
    if os.path.isdir(protocol_mono):
        n_mono = sync_reference_mono(protocol_mono)
        print(f"  reference_mono: synced {n_mono} files from {protocol_mono}")
    else:
        print(f"  reference_mono: skip (protocol tree not found: {protocol_mono})")


def sync_reference_mono(protocol_2d: str) -> int:
    """
    Copy monodisperse golden artifacts from a protocol 2d tree into validation/reference_mono.

    Uses filesystem copy (not retype). Returns number of files written under reference_mono.
    """
    proto = os.path.abspath(os.path.expanduser(protocol_2d))
    ref_root = os.path.join(VALIDATION_DIR, "reference_mono")
    for sub in ("guinier", "kratky", "fit_distances", "dammif"):
        os.makedirs(os.path.join(ref_root, sub), exist_ok=True)

    samples = {
        "ihs27": {
            "mode": "refine",
            "q_min": 0.142,
            "q_max": 5.0037,
            "dmax_nm": 6.5,
            "alpha": 35.4,
            "force_zero_rmin": "N",
            "force_zero_rmax": "N",
            "best_out": "gnom_best.out",
        },
        "ihs28": {
            "mode": "datgnom",
            "q_min": 0.2502,
            "q_max": 4.8864,
            "best_out": "datgnom_best.out",
        },
        "ihs29": {
            "mode": "refine",
            "q_min": 0.142,
            "q_max": 5.0037,
            "dmax_nm": 9.0,
            "alpha": 5.1,
            "force_zero_rmin": "N",
            "force_zero_rmax": "N",
            "best_out": "gnom_best.out",
        },
        "ihs30": {
            "mode": "refine",
            "q_min": 0.142,
            "q_max": 5.0037,
            "dmax_nm": 7.03,
            "alpha": 11.3,
            "force_zero_rmin": "N",
            "force_zero_rmax": "N",
            "best_out": "gnom_best.out",
        },
        "ihs31": {
            "mode": "refine",
            "q_min": 0.142,
            "q_max": 5.0037,
            "dmax_nm": 9.2,
            "alpha": 3.61,
            "force_zero_rmin": "N",
            "force_zero_rmax": "N",
            "best_out": "gnom_best.out",
        },
    }

    n_written = 0
    manifest = {
        "protocol_keys": list(samples.keys()),
        "model_dam_key": "ihs27",
        "samples": {},
        "dammif_ihs27": "dammif/ihs27_dammif_fits.yml",
    }

    def _cp(src: str, dst: str) -> None:
        nonlocal n_written
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        n_written += 1

    for key, meta in samples.items():
        _cp(
            os.path.join(proto, "guinier_mono", f"{key}_sample", f"{key}_sample_results.txt"),
            os.path.join(ref_root, "guinier", f"{key}_results.txt"),
        )
        _cp(
            os.path.join(proto, "analyze_kratky", f"{key}_sample", f"{key}_sample_kratky_params.yml"),
            os.path.join(ref_root, "kratky", f"{key}_kratky_params.yml"),
        )
        fd_src = os.path.join(proto, "fit_distances", f"{key}_sample")
        fd_dst = os.path.join(ref_root, "fit_distances", key)
        best_name = meta["best_out"]
        _cp(os.path.join(fd_src, best_name), os.path.join(fd_dst, best_name))
        dg = os.path.join(fd_src, "datgnom_best.out")
        if os.path.isfile(dg) and best_name != "datgnom_best.out":
            _cp(dg, os.path.join(fd_dst, "datgnom_best.out"))
        qpath = os.path.join(fd_src, f"{key}_sample_fit_distances_quality.yml")
        if os.path.isfile(qpath):
            _cp(qpath, os.path.join(fd_dst, "quality.yml"))
        entry = dict(meta)
        entry["guinier_results"] = f"guinier/{key}_results.txt"
        entry["kratky_params"] = f"kratky/{key}_kratky_params.yml"
        entry["best_out_path"] = f"fit_distances/{key}/{best_name}"
        manifest["samples"][key] = entry

    _cp(
        os.path.join(proto, "dammif", "ihs27_sample", "dammif_fits.yml"),
        os.path.join(ref_root, "dammif", "ihs27_dammif_fits.yml"),
    )
    man_path = os.path.join(ref_root, "manifest.yml")
    with open(man_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(manifest, f, sort_keys=False)
    n_written += 1
    return n_written


if __name__ == "__main__":
    main()
