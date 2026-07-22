from __future__ import annotations

from pathlib import Path

from .state import LiveviewWatchMode


def tiff_output_root(*, watchdir: Path, tiff_path: str, mode: LiveviewWatchMode) -> Path:
    """Root directory for per-TIFF pipeline outputs (averaged, subtracted, fit_*, …)."""
    wd = watchdir.expanduser().resolve()
    if mode == LiveviewWatchMode.TREE:
        tp = (tiff_path or "").strip()
        if tp:
            return Path(tp).expanduser().resolve().parent
    return wd


def averaged_proxy_dir(root: Path) -> Path:
    return root / "averaged_proxy"


def averaged_dir(root: Path) -> Path:
    return root / "averaged"


def subtracted_dir(root: Path) -> Path:
    return root / "subtracted"


def guinier_dir(root: Path) -> Path:
    """Legacy shared Guinier tree (pre per-chain split). Prefer guinier_mono_dir / guinier_poly_dir."""
    return root / "guinier"


def guinier_mono_dir(root: Path) -> Path:
    return root / "guinier_mono"


def guinier_poly_dir(root: Path) -> Path:
    return root / "guinier_poly"


def fit_distances_dir(root: Path) -> Path:
    return root / "fit_distances"


def fit_sizes_dir(root: Path) -> Path:
    return root / "fit_sizes"


def model_bodies_dir(root: Path) -> Path:
    return root / "model_bodies"


def fit_bodies_dir(root: Path) -> Path:
    """Deprecated alias for :func:`model_bodies_dir` (legacy liveview folder name was ``fit_bodies``)."""
    return model_bodies_dir(root)


def dammif_dir(root: Path) -> Path:
    return root / "dammif"


def denss_dir(root: Path) -> Path:
    return root / "denss"


def mixture_dir(root: Path) -> Path:
    return root / "mixture"


def integrated_dat_path(*, root: Path, stem: str, integrator_ready: bool) -> Path:
    int_a = averaged_dir(root) / f"int_{stem}.dat"
    int_p = averaged_proxy_dir(root) / f"int_{stem}.dat"
    if integrator_ready:
        if int_a.is_file():
            return int_a
        if int_p.is_file():
            return int_p
    else:
        if int_p.is_file():
            return int_p
        if int_a.is_file():
            return int_a
    return int_a if integrator_ready else int_p


def subtracted_dat_path(*, root: Path, stem: str) -> Path:
    return subtracted_dir(root) / f"sub_{stem}.dat"


def tiff_history_label(*, watchdir: Path, tiff_path: str, mode: LiveviewWatchMode) -> str:
    """Short label for session history (disambiguate duplicate stems in tree mode)."""
    tp = (tiff_path or "").strip()
    if not tp:
        return ""
    p = Path(tp)
    if mode == LiveviewWatchMode.TREE:
        try:
            rel = p.resolve().relative_to(watchdir.expanduser().resolve())
            return rel.as_posix()
        except ValueError:
            pass
    return p.name
