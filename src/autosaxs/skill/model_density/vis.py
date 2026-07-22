"""Presentation PNG/GIF visuals for ``model_density`` (slices + rotating density/σ clouds)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
from matplotlib.colors import LinearSegmentedColormap, Normalize

# DENSS MRC coordinates are ångströms; on-figure scale is in nm.
_A_TO_NM = 0.1
_LEVEL_FRACTION = 0.15  # same as liveview denss AABB (isosurface_mesh_data)
_FRAME_DURATION_MS = 50
_GIF_TOTAL_FRAMES = 200  # full ping-pong loop → 200 × 50 ms = 10 s
_DPI = 120
_FIGSIZE = (11.5, 4.4)
_UPSAMPLE = 8  # in-plane nearest→smooth display factor

# Rotating density/σ GIFs (Liveview cloud style; camera schedule like model_dam).
_N_ROT = 36
_ELEV = 18.0
_ROT_DURATION_MS = 70
_ROT_DPI = 110
_ROT_FIGSIZE = (5.0, 4.6)
_ROT_MAX_POINTS = 12_000
_ROT_BG = "#0b1220"
_LIMIT_PAD = 1.08

_ELECTRON_CMAP = LinearSegmentedColormap.from_list(
    "electron_density",
    [
        (0.00, "#02040a"),
        (0.22, "#061433"),
        (0.45, "#0c3a7a"),
        (0.70, "#3a8fd0"),
        (0.88, "#c5e4f7"),
        (1.00, "#f7fbff"),
    ],
)

# Liveview denss cloud colormap (cold electron).
_CLOUD_CMAP = LinearSegmentedColormap.from_list(
    "electron_cold",
    ["#062033", "#0b4f6c", "#0e7490", "#22d3ee", "#e0f7ff"],
)


def _ping_pong_fractions(
    span_voxels: int = 1,
    *,
    total_frames: int = _GIF_TOTAL_FRAMES,
) -> List[float]:
    """Return 0→1→0 sweep fractions for a fixed-length ping-pong loop."""
    _ = span_voxels  # AABB depth does not change frame count (timing is fixed)
    n = max(int(total_frames), 2)
    n_fwd = (n + 2) // 2  # n_fwd + (n_fwd - 2) == n
    fracs_fwd = [i / max(n_fwd - 1, 1) for i in range(n_fwd)]
    if len(fracs_fwd) <= 1:
        return [0.0]
    fracs = fracs_fwd + list(reversed(fracs_fwd[1:-1]))
    if len(fracs) < n:
        fracs = fracs + [fracs[-1]] * (n - len(fracs))
    return fracs[:n]


def _round_scale_nm(span_nm: float) -> float:
    """Pick a round scale-bar length (~1/4 of displayed span)."""
    target = max(float(span_nm) * 0.25, 0.5)
    for cand in (0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0):
        if cand >= target * 0.6:
            return cand
    return float(max(1.0, round(target)))


def _fig_to_rgb(fig) -> np.ndarray:
    fig.canvas.draw()
    buf = getattr(fig.canvas, "buffer_rgba", None)
    if callable(buf):
        rgba = np.asarray(buf())
        return np.asarray(rgba[:, :, :3], dtype=np.uint8).copy()
    w, h = fig.canvas.get_width_height()
    rgb = np.frombuffer(fig.canvas.tostring_rgb(), dtype=np.uint8)
    return rgb.reshape(h, w, 3).copy()


def _save_gif(frames: Sequence[np.ndarray], path: Path, *, duration_ms: int) -> None:
    from PIL import Image

    if not frames:
        return
    imgs = [Image.fromarray(np.asarray(f, dtype=np.uint8), mode="RGB") for f in frames]
    dur = int(duration_ms)
    imgs[0].save(
        path,
        save_all=True,
        append_images=imgs[1:],
        duration=[dur] * len(imgs),
        loop=0,
        optimize=False,
        disposal=2,
    )


def _interp_along_axis(
    rho: np.ndarray,
    axis: int,
    coord: float,
    sl_a: slice,
    sl_b: slice,
) -> np.ndarray:
    """Linear interpolation along one axis at continuous ``coord``."""
    c0 = int(np.floor(coord))
    c1 = min(c0 + 1, rho.shape[axis] - 1)
    w = float(coord - c0)

    def _take(c: int) -> np.ndarray:
        if axis == 0:
            return rho[c, sl_a, sl_b]
        if axis == 1:
            return rho[sl_a, c, sl_b]
        return rho[sl_a, sl_b, c]

    return (1.0 - w) * _take(c0) + w * _take(c1)


def _orthogonal_planes(
    rho: np.ndarray,
    *,
    x_f: float,
    y_f: float,
    z_f: float,
    sx: slice,
    sy: slice,
    sz: slice,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """YZ @ x, XZ @ y, XY @ z with sub-voxel interpolation on the varying axis."""
    return (
        _interp_along_axis(rho, 0, x_f, sy, sz),
        _interp_along_axis(rho, 1, y_f, sx, sz),
        _interp_along_axis(rho, 2, z_f, sx, sy),
    )


def _mrc_voxel_side(rho: np.ndarray, side) -> float:
    n = int(rho.shape[0])
    side_f = float(np.asarray(side).reshape(-1)[0]) if np.size(side) else float(n)
    return side_f / float(max(n, 1))


def _find_support_mrc(density_mrc: Path) -> Optional[Path]:
    stem = density_mrc.stem
    for cand in (
        density_mrc.with_name(f"{stem}_support.mrc"),
        density_mrc.parent / f"{stem}_support.mrc",
    ):
        if cand.is_file():
            return cand
    return None


def _particle_aabb_slices(
    rho: np.ndarray,
    *,
    voxel_a: float,
    support: Optional[np.ndarray] = None,
    level_fraction: float = _LEVEL_FRACTION,
) -> Tuple[slice, slice, slice]:
    """
    Index AABB of the particle (liveview denss rule).

    Prefer ``support`` mask when present; else ``ρ ≥ level_fraction · ρ_max``.
    Pad like liveview: ``max(0.5·voxel, 0.10·core_span)`` converted to voxels.
    """
    if support is not None and support.shape == rho.shape and np.any(support > 0.5):
        mask = np.asarray(support) > 0.5
    else:
        rho_max = float(np.nanmax(rho))
        if not np.isfinite(rho_max) or rho_max <= 0:
            return tuple(slice(0, s) for s in rho.shape)  # type: ignore[return-value]
        mask = np.isfinite(rho) & (rho >= float(level_fraction) * rho_max)
        if not np.any(mask):
            return tuple(slice(0, s) for s in rho.shape)  # type: ignore[return-value]

    ijk = np.argwhere(mask)
    lo = ijk.min(axis=0).astype(np.float64)
    hi = ijk.max(axis=0).astype(np.float64)
    core_span_a = float(np.max(hi - lo) * voxel_a)
    pad_a = max(0.5 * float(voxel_a), 0.10 * max(core_span_a, 1e-6))
    pad_vox = int(max(1, np.ceil(pad_a / max(float(voxel_a), 1e-12))))
    lo_i = np.maximum(np.floor(lo).astype(int) - pad_vox, 0)
    hi_i = np.minimum(np.ceil(hi).astype(int) + pad_vox, np.array(rho.shape, dtype=int) - 1)
    return (
        slice(int(lo_i[0]), int(hi_i[0]) + 1),
        slice(int(lo_i[1]), int(hi_i[1]) + 1),
        slice(int(lo_i[2]), int(hi_i[2]) + 1),
    )


def _upsample2d(plane: np.ndarray, factor: int = _UPSAMPLE) -> np.ndarray:
    if factor <= 1:
        return np.asarray(plane, dtype=np.float64)
    try:
        from scipy.ndimage import zoom
    except ImportError:
        return np.asarray(plane, dtype=np.float64)
    return zoom(np.asarray(plane, dtype=np.float64), factor, order=1)


def _draw_scale_strip(ax, *, bar_nm: float, panel_extent_nm: float, color: str = "0.15") -> None:
    """Horizontal nm scale bar in a dedicated axes below the three panels (white bg)."""
    ax.set_xlim(0.0, 1.0)
    ax.set_ylim(0.0, 1.0)
    ax.set_axis_off()
    ax.set_facecolor("white")
    extent = max(float(panel_extent_nm), 1e-6)
    # Bar length as fraction of one panel; strip spans three panels → /3.
    width = float(np.clip((bar_nm / extent) / 3.0, 0.04, 0.28))
    x0 = 0.04
    y0 = 0.55
    tick = 0.28
    ax.plot([x0, x0 + width], [y0, y0], color=color, lw=2.6, solid_capstyle="butt", clip_on=False)
    ax.plot([x0, x0], [y0 - tick * 0.5, y0 + tick * 0.5], color=color, lw=2.0, clip_on=False)
    ax.plot(
        [x0 + width, x0 + width],
        [y0 - tick * 0.5, y0 + tick * 0.5],
        color=color,
        lw=2.0,
        clip_on=False,
    )
    label = f"{bar_nm:g} nm" if bar_nm >= 1 else f"{bar_nm:.1f} nm"
    ax.text(
        x0 + width * 0.5,
        y0 - 0.42,
        label,
        ha="center",
        va="top",
        fontsize=10,
        color=color,
        clip_on=False,
    )


def _make_slice_figure(
    rho: np.ndarray,
    *,
    x_f: float,
    y_f: float,
    z_f: float,
    sx: slice,
    sy: slice,
    sz: slice,
    vmin: float,
    vmax: float,
    bar_nm: float,
    panel_extent_nm: float,
):
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=_FIGSIZE, dpi=_DPI)
    fig.patch.set_facecolor("white")
    gs = fig.add_gridspec(2, 3, height_ratios=[1.0, 0.16], hspace=0.12, wspace=0.06)
    axes = [fig.add_subplot(gs[0, i]) for i in range(3)]
    ax_scale = fig.add_subplot(gs[1, :])

    planes = _orthogonal_planes(rho, x_f=x_f, y_f=y_f, z_f=z_f, sx=sx, sy=sy, sz=sz)
    for ax, plane in zip(axes, planes):
        img = _upsample2d(np.clip(plane, vmin, vmax))
        ax.imshow(
            img.T,
            origin="lower",
            cmap=_ELECTRON_CMAP,
            vmin=vmin,
            vmax=vmax,
            interpolation="bilinear",
            aspect="equal",
        )
        ax.set_axis_off()
        ax.set_facecolor("white")

    _draw_scale_strip(ax_scale, bar_nm=bar_nm, panel_extent_nm=panel_extent_nm)
    fig.subplots_adjust(left=0.02, right=0.98, top=0.98, bottom=0.02)
    return fig


def _mrc_box_min(rho: np.ndarray, side) -> Tuple[np.ndarray, float]:
    """Return (box_min_Å, voxel_Å) for a cubic DENSS MRC (Liveview convention)."""
    n = int(rho.shape[0])
    side_f = float(np.asarray(side).reshape(-1)[0]) if np.size(side) else float(n)
    voxel = side_f / float(max(n, 1))
    half = 0.5 * side_f
    return np.array([-half, -half, -half], dtype=np.float64), float(voxel)


def _build_density_cloud(
    rho: np.ndarray,
    side,
    *,
    sigma: Optional[np.ndarray] = None,
    level_fraction: float = _LEVEL_FRACTION,
    max_points: int = _ROT_MAX_POINTS,
    rng_seed: int = 0,
) -> Optional[Dict[str, Any]]:
    """
    Point cloud of voxels with ρ ≥ level_fraction·ρ_max (Liveview denss sampling).

    Coordinates and AABB are returned in nm for on-figure scale bars.
    """
    rho = np.asarray(rho, dtype=np.float64)
    if rho.ndim != 3 or rho.size == 0:
        return None
    rho_max = float(np.nanmax(rho))
    if not np.isfinite(rho_max) or rho_max <= 0:
        return None
    level = float(level_fraction) * rho_max
    mask = np.isfinite(rho) & (rho >= level)
    if not np.any(mask):
        return None

    box_min, voxel = _mrc_box_min(rho, side)
    flat_idx = np.flatnonzero(mask)
    weights = np.maximum(rho.ravel()[flat_idx], 0.0)
    wsum = float(np.sum(weights))
    if not np.isfinite(wsum) or wsum <= 0:
        return None

    ijk_core = np.column_stack(np.unravel_index(flat_idx, rho.shape)).astype(np.float64)
    xyz_core_a = ijk_core * voxel + box_min
    core_span = float(np.max(xyz_core_a.max(axis=0) - xyz_core_a.min(axis=0)))
    pad = max(0.5 * voxel, 0.10 * max(core_span, 1e-6))
    part_min_a = xyz_core_a.min(axis=0) - pad
    part_max_a = xyz_core_a.max(axis=0) + pad

    rng = np.random.default_rng(int(rng_seed))
    n_target = int(max(int(max_points), 1))
    n_vox = int(flat_idx.size)
    probs = weights / wsum
    if n_target <= n_vox:
        chosen_local = rng.choice(n_vox, size=n_target, replace=False, p=probs)
        sel = flat_idx[chosen_local]
        jitter = 0.35
    else:
        counts = rng.multinomial(n_target, probs)
        reps = np.repeat(np.arange(n_vox), counts)
        sel = flat_idx[reps]
        jitter = 0.45

    ijk = np.column_stack(np.unravel_index(sel, rho.shape)).astype(np.float64)
    ijk = ijk + rng.uniform(-jitter, jitter, size=ijk.shape)
    xyz_nm = (ijk * voxel + box_min) * _A_TO_NM
    density = rho.ravel()[sel].astype(np.float64)

    sigma_vals: Optional[np.ndarray] = None
    if sigma is not None:
        sig = np.asarray(sigma, dtype=np.float64)
        if sig.shape == rho.shape:
            sigma_vals = sig.ravel()[sel].astype(np.float64)

    return {
        "xyz_nm": xyz_nm,
        "density": density,
        "sigma": sigma_vals,
        "lo_nm": np.asarray(part_min_a, dtype=np.float64) * _A_TO_NM,
        "hi_nm": np.asarray(part_max_a, dtype=np.float64) * _A_TO_NM,
    }


def _cloud_rgba(
    values: np.ndarray,
    *,
    high_is_bright: bool = True,
    alpha_min: float = 0.10,
    alpha_max: float = 0.88,
) -> np.ndarray:
    vals = np.asarray(values, dtype=np.float64)
    vmin = float(np.nanmin(vals))
    vmax = float(np.nanmax(vals))
    if not np.isfinite(vmin) or not np.isfinite(vmax) or abs(vmax - vmin) < 1e-15:
        vmax = vmin + 1.0
    t_raw = np.clip(Normalize(vmin=vmin, vmax=vmax)(vals), 0.0, 1.0)
    t = t_raw if high_is_bright else (1.0 - t_raw)
    rgba = _CLOUD_CMAP(t)
    rgba[:, 3] = alpha_min + (alpha_max - alpha_min) * t
    return rgba


def _equal_limits_3d(ax, lo: np.ndarray, hi: np.ndarray, *, pad: float = _LIMIT_PAD) -> None:
    center = (lo + hi) * 0.5
    span = max(float(np.max(hi - lo)) * 0.5 * pad, 1e-3)
    ax.set_xlim(center[0] - span, center[0] + span)
    ax.set_ylim(center[1] - span, center[1] + span)
    ax.set_zlim(center[2] - span, center[2] + span)
    try:
        ax.set_box_aspect((1, 1, 1))
    except Exception:
        pass


def _draw_scale_bar_3d(ax, *, bar_nm: float, span_nm: float, color: str = "#c8d9e8") -> None:
    """Screen-space nm scale bar overlay (same approach as model_dam)."""
    axes_data_span = max(float(span_nm) * float(_LIMIT_PAD), 1e-6)
    width = float(np.clip(bar_nm / axes_data_span, 0.08, 0.40))
    x0, y0 = 0.06, 0.07
    tick = 0.022
    ax2 = ax.figure.add_axes(ax.get_position(), frameon=False, facecolor="none", zorder=20)
    ax2.set_xlim(0.0, 1.0)
    ax2.set_ylim(0.0, 1.0)
    ax2.set_axis_off()
    ax2.set_navigate(False)
    ax2.patch.set_alpha(0.0)
    ax2.plot([x0, x0 + width], [y0, y0], color=color, lw=2.8, solid_capstyle="butt", clip_on=False, zorder=21)
    ax2.plot([x0, x0], [y0 - tick, y0 + tick], color=color, lw=2.2, solid_capstyle="butt", clip_on=False, zorder=21)
    ax2.plot(
        [x0 + width, x0 + width],
        [y0 - tick, y0 + tick],
        color=color,
        lw=2.2,
        solid_capstyle="butt",
        clip_on=False,
        zorder=21,
    )
    label = f"{bar_nm:g} nm" if bar_nm >= 1 else f"{bar_nm:.1f} nm"
    ax2.text(
        x0 + width * 0.5,
        y0 + 0.048,
        label,
        ha="center",
        va="bottom",
        fontsize=10,
        color=color,
        clip_on=False,
        zorder=22,
    )


def _write_rotate_gif(
    cloud: Dict[str, Any],
    *,
    values: np.ndarray,
    high_is_bright: bool,
    out_path: Path,
    plt,
) -> str:
    xyz = np.asarray(cloud["xyz_nm"], dtype=np.float64)
    if xyz.size == 0:
        return ""
    rgba = _cloud_rgba(values, high_is_bright=high_is_bright)
    # σ mode: keep fewer points in high-σ regions (Liveview).
    if not high_is_bright:
        n = int(xyz.shape[0])
        target = max(1500, n // 2)
        if target < n:
            w = np.asarray(rgba[:, 3], dtype=np.float64)
            w = np.maximum(w, 0.0)
            s = float(np.sum(w))
            probs = (w / s) if s > 0 else np.full(n, 1.0 / n)
            rng = np.random.default_rng(2)
            keep = rng.choice(n, size=int(target), replace=False, p=probs)
            xyz = xyz[keep]
            rgba = rgba[keep]

    lo = np.asarray(cloud["lo_nm"], dtype=np.float64)
    hi = np.asarray(cloud["hi_nm"], dtype=np.float64)
    span_nm = float(np.max(hi - lo))
    bar_nm = _round_scale_nm(span_nm)
    azims = [360.0 * i / _N_ROT for i in range(_N_ROT)]

    frames: List[np.ndarray] = []
    for azim in azims:
        fig = plt.figure(figsize=_ROT_FIGSIZE, dpi=_ROT_DPI)
        fig.patch.set_facecolor(_ROT_BG)
        ax = fig.add_subplot(111, projection="3d")
        ax.set_axis_off()
        ax.set_facecolor(_ROT_BG)
        ax.scatter(
            xyz[:, 0],
            xyz[:, 1],
            xyz[:, 2],
            c=rgba,
            s=8.0,
            depthshade=False,
            edgecolors="none",
            linewidths=0,
        )
        _equal_limits_3d(ax, lo, hi)
        ax.view_init(elev=_ELEV, azim=azim)
        fig.tight_layout(pad=0.05)
        _draw_scale_bar_3d(ax, bar_nm=bar_nm, span_nm=span_nm)
        frames.append(_fig_to_rgb(fig))
        plt.close(fig)

    _save_gif(frames, out_path, duration_ms=_ROT_DURATION_MS)
    return str(out_path.resolve())


def _write_slice_visuals(
    *,
    rho: np.ndarray,
    side,
    dens_path: Path,
    denss,
    pres: Path,
    plt,
) -> Tuple[str, str]:
    """Write density_slices.gif + density_midplanes.png; return absolute paths."""
    voxel_a = _mrc_voxel_side(rho, side)
    support = None
    support_path = _find_support_mrc(dens_path)
    if support_path is not None:
        sup, _ = denss.read_mrc(str(support_path))
        support = np.asarray(sup, dtype=np.float64)

    sx, sy, sz = _particle_aabb_slices(rho, voxel_a=voxel_a, support=support)
    x_lo, x_hi = sx.start, sx.stop - 1
    y_lo, y_hi = sy.start, sy.stop - 1
    z_lo, z_hi = sz.start, sz.stop - 1

    span_vox = max(x_hi - x_lo + 1, y_hi - y_lo + 1, z_hi - z_lo + 1)
    panel_extent_nm = float(span_vox) * float(voxel_a) * _A_TO_NM
    bar_nm = _round_scale_nm(panel_extent_nm)

    crop = rho[sx, sy, sz]
    pos = crop[np.isfinite(crop) & (crop > 0)]
    vmin = 0.0
    if pos.size:
        vmax = float(np.percentile(pos, 99.0))
        if not np.isfinite(vmax) or vmax <= vmin:
            vmax = float(np.nanmax(pos))
    else:
        vmax = float(np.nanmax(crop)) if np.any(np.isfinite(crop)) else 1.0
    if not np.isfinite(vmax) or vmax <= vmin:
        vmax = vmin + 1.0

    fracs = _ping_pong_fractions(span_vox)
    frames: List[np.ndarray] = []
    for frac in fracs:
        x_f = float(x_lo + frac * (x_hi - x_lo))
        y_f = float(y_lo + frac * (y_hi - y_lo))
        z_f = float(z_lo + frac * (z_hi - z_lo))
        fig = _make_slice_figure(
            rho,
            x_f=x_f,
            y_f=y_f,
            z_f=z_f,
            sx=sx,
            sy=sy,
            sz=sz,
            vmin=vmin,
            vmax=vmax,
            bar_nm=bar_nm,
            panel_extent_nm=panel_extent_nm,
        )
        frames.append(_fig_to_rgb(fig))
        plt.close(fig)

    gif_path = pres / "density_slices.gif"
    _save_gif(frames, gif_path, duration_ms=_FRAME_DURATION_MS)

    x_m = 0.5 * (x_lo + x_hi)
    y_m = 0.5 * (y_lo + y_hi)
    z_m = 0.5 * (z_lo + z_hi)
    fig = _make_slice_figure(
        rho,
        x_f=x_m,
        y_f=y_m,
        z_f=z_m,
        sx=sx,
        sy=sy,
        sz=sz,
        vmin=vmin,
        vmax=vmax,
        bar_nm=bar_nm,
        panel_extent_nm=panel_extent_nm,
    )
    png_path = pres / "density_midplanes.png"
    fig.savefig(png_path, dpi=160, facecolor="white")
    plt.close(fig)
    return str(gif_path.resolve()), str(png_path.resolve())


def write_visuals(
    output_dir: str,
    *,
    density_map_path: str = "",
    sigma_map_path: str = "",
    event_bus: Any = None,
) -> Dict[str, Union[str, List[str]]]:
    """
    Write presentation visuals under ``{output_dir}/visuals/``.

    Always (when a density MRC is available):
    - ``density_slices.gif`` / ``density_midplanes.png`` — synced orthographic cuts
    - ``density_rotate.gif`` — rotating Liveview-style ρ point cloud

    When ``sigma_map_path`` is present:
    - ``sigma_rotate.gif`` — same cloud colored by σ (high σ → dim)
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    od = Path(output_dir)
    pres = od / "visuals"
    pres.mkdir(parents=True, exist_ok=True)

    empty: Dict[str, Union[str, List[str]]] = {
        "visuals_dir": str(pres.resolve()),
        "slices_gif": "",
        "midplanes_png": "",
        "density_rotate_gif": "",
        "sigma_rotate_gif": "",
    }

    dens_path = Path(density_map_path) if density_map_path else None
    if dens_path is None or not dens_path.is_file():
        cands = sorted(
            p
            for p in od.glob("*.mrc")
            if p.is_file()
            and "_support" not in p.name.lower()
            and "_sigma" not in p.name.lower()
        )
        dens_path = cands[0] if cands else None
    if dens_path is None or not dens_path.is_file():
        if event_bus:
            from autosaxs.core.event_bus import EventType

            event_bus.publish(
                EventType.MESSAGE,
                {"text": "model_density: visuals skipped (no density MRC)"},
            )
        return empty

    try:
        import denss
    except ImportError as exc:
        raise RuntimeError(
            "model_density visuals require denss (pip install denss)."
        ) from exc

    rho, side = denss.read_mrc(str(dens_path))
    rho = np.asarray(rho, dtype=np.float64)
    if rho.ndim != 3 or rho.size == 0:
        return empty

    sigma_arr: Optional[np.ndarray] = None
    sigma_path = Path(sigma_map_path) if sigma_map_path else None
    if sigma_path is not None and sigma_path.is_file():
        sig, _ = denss.read_mrc(str(sigma_path))
        sigma_arr = np.asarray(sig, dtype=np.float64)
        if sigma_arr.shape != rho.shape:
            if event_bus:
                from autosaxs.core.event_bus import EventType

                event_bus.publish(
                    EventType.MESSAGE,
                    {
                        "text": (
                            f"model_density: σ map shape {sigma_arr.shape} ≠ density "
                            f"{rho.shape}; skipping sigma_rotate.gif"
                        )
                    },
                )
            sigma_arr = None

    if event_bus:
        from autosaxs.core.event_bus import EventType

        event_bus.publish(
            EventType.MESSAGE,
            {"text": "model_density: writing visuals (slices + rotating density/σ)…"},
        )

    slices_gif, midplanes_png = _write_slice_visuals(
        rho=rho, side=side, dens_path=dens_path, denss=denss, pres=pres, plt=plt
    )

    density_rotate_gif = ""
    sigma_rotate_gif = ""
    cloud = _build_density_cloud(rho, side, sigma=sigma_arr)
    if cloud is not None:
        density_rotate_gif = _write_rotate_gif(
            cloud,
            values=cloud["density"],
            high_is_bright=True,
            out_path=pres / "density_rotate.gif",
            plt=plt,
        )
        if cloud.get("sigma") is not None:
            sigma_rotate_gif = _write_rotate_gif(
                cloud,
                values=cloud["sigma"],
                high_is_bright=False,
                out_path=pres / "sigma_rotate.gif",
                plt=plt,
            )

    return {
        "visuals_dir": str(pres.resolve()),
        "slices_gif": slices_gif,
        "midplanes_png": midplanes_png,
        "density_rotate_gif": density_rotate_gif,
        "sigma_rotate_gif": sigma_rotate_gif,
    }
