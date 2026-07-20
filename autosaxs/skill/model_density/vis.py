"""Presentation PNG/GIF visuals for ``model_density`` (synced orthographic density slices)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
from matplotlib.colors import LinearSegmentedColormap

# DENSS MRC coordinates are ångströms; presentation scale is in nm.
_A_TO_NM = 0.1
_LEVEL_FRACTION = 0.15  # same as liveview denss AABB (guisaxs isosurface_mesh_data)
_SLICE_DURATION_MS = 320  # 4× slower than the initial 80 ms draft
_DPI = 120
_FIGSIZE = (11.5, 4.4)
_UPSAMPLE = 8  # in-plane nearest→smooth display factor

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
    imgs = [Image.fromarray(f, mode="RGB") for f in frames]
    imgs[0].save(
        path,
        save_all=True,
        append_images=imgs[1:],
        duration=int(duration_ms),
        loop=0,
        optimize=False,
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
    ix: int,
    iy: int,
    iz: int,
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

    # YZ @ x, XZ @ y, XY @ z — crop to AABB in the free axes.
    planes = (
        rho[ix, sy, sz],
        rho[sx, iy, sz],
        rho[sx, sy, iz],
    )
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


def write_presentation_visuals(
    output_dir: str,
    *,
    density_map_path: str = "",
    event_bus: Any = None,
) -> Dict[str, Union[str, List[str]]]:
    """
    Write presentation slice GIF + midplane PNG under ``{output_dir}/presentation/``.

    Three synced panels (YZ@x(t), XZ@y(t), XY@z(t)) sweep the particle AABB
    (ρ ≥ 0.15·ρ_max or support MRC). Scale bar sits below the panels on white.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    od = Path(output_dir)
    pres = od / "presentation"
    pres.mkdir(parents=True, exist_ok=True)

    empty: Dict[str, Union[str, List[str]]] = {
        "presentation_dir": str(pres.resolve()),
        "presentation_slices_gif": "",
        "presentation_midplanes_png": "",
    }

    dens_path = Path(density_map_path) if density_map_path else None
    if dens_path is None or not dens_path.is_file():
        # Fall back to newest/first *.mrc that is not support/sigma in output_dir.
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
                {"text": "model_density: presentation vis skipped (no density MRC)"},
            )
        return empty

    try:
        import denss
    except ImportError as exc:
        raise RuntimeError(
            "model_density presentation vis requires denss (pip install denss)."
        ) from exc

    rho, side = denss.read_mrc(str(dens_path))
    rho = np.asarray(rho, dtype=np.float64)
    if rho.ndim != 3 or rho.size == 0:
        return empty

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

    # Shared square display extent (nm): max AABB side of the crop.
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

    if event_bus:
        from autosaxs.core.event_bus import EventType

        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"model_density: writing presentation slices "
                    f"(AABB {x_hi - x_lo + 1}×{y_hi - y_lo + 1}×{z_hi - z_lo + 1})…"
                )
            },
        )

    # Synced fractional depth: same t maps to relative position on each axis.
    n_steps = max(x_hi - x_lo + 1, y_hi - y_lo + 1, z_hi - z_lo + 1, 12)
    fracs_fwd = [i / max(n_steps - 1, 1) for i in range(n_steps)]
    fracs = fracs_fwd + list(reversed(fracs_fwd[1:-1]))

    frames: List[np.ndarray] = []
    for frac in fracs:
        ix = int(round(x_lo + frac * (x_hi - x_lo)))
        iy = int(round(y_lo + frac * (y_hi - y_lo)))
        iz = int(round(z_lo + frac * (z_hi - z_lo)))
        fig = _make_slice_figure(
            rho,
            ix=ix,
            iy=iy,
            iz=iz,
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
    _save_gif(frames, gif_path, duration_ms=_SLICE_DURATION_MS)

    # Midplane PNG (center of AABB).
    ix_m = (x_lo + x_hi) // 2
    iy_m = (y_lo + y_hi) // 2
    iz_m = (z_lo + z_hi) // 2
    fig = _make_slice_figure(
        rho,
        ix=ix_m,
        iy=iy_m,
        iz=iz_m,
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

    return {
        "presentation_dir": str(pres.resolve()),
        "presentation_slices_gif": str(gif_path.resolve()),
        "presentation_midplanes_png": str(png_path.resolve()),
    }
