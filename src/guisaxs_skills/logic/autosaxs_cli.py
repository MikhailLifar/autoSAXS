from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_get_default_config(*, output_dir: Path) -> Path:
    """
    Run ``autosaxs get-default-config -o <output_dir>`` and return the written config path.
    """
    out_dir = output_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "autosaxs.cli.cli",
        "get-default-config",
        "-o",
        str(out_dir),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(out_dir),
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            "autosaxs get-default-config failed"
            + (f":\n{detail}" if detail else f" (exit code {proc.returncode})")
        )

    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if lines:
        printed = Path(lines[-1])
        if printed.is_file():
            return printed.resolve()

    dest = out_dir / "config_base.conf"
    if dest.is_file():
        return dest
    raise FileNotFoundError(
        f"autosaxs get-default-config did not create config_base.conf under {out_dir}"
    )
