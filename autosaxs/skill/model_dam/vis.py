"""Presentation PNG/GIF visuals for ``model_dam`` (synced rotation, occupancy threshold)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

# CIF coordinates from ATSAS are ångströms; on-figure scale is in nm.
_A_TO_NM = 0.1

_OVERLAP_RGBA: Tuple[Tuple[float, float, float, float], ...] = (
    (0.12, 0.47, 0.71, 0.50),
    (1.00, 0.50, 0.05, 0.45),
    (0.17, 0.63, 0.17, 0.45),
    (0.84, 0.15, 0.16, 0.45),
    (0.58, 0.40, 0.74, 0.45),
    (0.55, 0.34, 0.29, 0.45),
    (0.89, 0.47, 0.76, 0.45),
    (0.50, 0.50, 0.50, 0.45),
    (0.74, 0.74, 0.13, 0.45),
    (0.09, 0.75, 0.81, 0.45),
)

_N_ROT = 36
_ELEV = 18.0
_GRID = 52
_ROT_DURATION_MS = 70
_OCC_DURATION_MS = 90
_DPI = 110
_FIGSIZE = (5.0, 4.6)
_LIMIT_PAD = 1.08


def _round_scale_nm(span_nm: float) -> float:
    """Pick a round scale-bar length (~1/4 of bbox span)."""
    target = max(float(span_nm) * 0.25, 0.5)
    for cand in (0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0):
        if cand >= target * 0.6:
            return cand
    return float(max(1.0, round(target)))


def _draw_scale_bar_nm(
    ax,
    *,
    bar_nm: float,
    span_nm: float,
    pad: float = _LIMIT_PAD,
    color: str = "0.2",
) -> None:
    """Screen-space scale bar on a 2D overlay (Axes3D ignores transform= on plot).

    Bar length as a fraction of the axes width matches ``bar_nm / (span_nm * pad)``,
    i.e. the same ratio as in the equalized 3D data limits. Call **after**
    ``tight_layout`` so ``ax.get_position()`` is final.
    """
    axes_data_span = max(float(span_nm) * float(pad), 1e-6)
    width = float(np.clip(bar_nm / axes_data_span, 0.08, 0.40))
    x0, y0 = 0.06, 0.07
    tick = 0.022

    # Overlay 2D axes sharing the 3D axes position — Line2D/text work reliably here.
    ax2 = ax.figure.add_axes(ax.get_position(), frameon=False, facecolor="none", zorder=20)
    ax2.set_xlim(0.0, 1.0)
    ax2.set_ylim(0.0, 1.0)
    ax2.set_axis_off()
    ax2.set_navigate(False)
    ax2.patch.set_alpha(0.0)

    ax2.plot(
        [x0, x0 + width],
        [y0, y0],
        color=color,
        lw=2.8,
        solid_capstyle="butt",
        clip_on=False,
        zorder=21,
    )
    ax2.plot(
        [x0, x0],
        [y0 - tick, y0 + tick],
        color=color,
        lw=2.2,
        solid_capstyle="butt",
        clip_on=False,
        zorder=21,
    )
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


def _equal_limits(ax, lo: np.ndarray, hi: np.ndarray, *, pad: float = _LIMIT_PAD) -> None:
    center = (lo + hi) * 0.5
    span = max(float(np.max(hi - lo)) * 0.5 * pad, 1e-3)
    ax.set_xlim(center[0] - span, center[0] + span)
    ax.set_ylim(center[1] - span, center[1] + span)
    ax.set_zlim(center[2] - span, center[2] + span)
    try:
        ax.set_box_aspect((1, 1, 1))
    except Exception:
        pass


def _fig_to_rgb(fig) -> np.ndarray:
    fig.canvas.draw()
    # Prefer buffer_rgba (Matplotlib ≥3.8); fall back for older builds.
    try:
        rgba = np.asarray(fig.canvas.buffer_rgba())
        return np.ascontiguousarray(rgba[:, :, :3])
    except Exception:
        w, h = fig.canvas.get_width_height()
        buf = np.frombuffer(fig.canvas.tostring_rgb(), dtype=np.uint8)
        return buf.reshape(h, w, 3).copy()


def _save_gif(frames: Sequence[np.ndarray], path: Path, *, duration_ms: int) -> None:
    from PIL import Image

    if not frames:
        raise ValueError("no frames to write")
    imgs = [Image.fromarray(f) for f in frames]
    path.parent.mkdir(parents=True, exist_ok=True)
    imgs[0].save(
        path,
        save_all=True,
        append_images=imgs[1:],
        duration=int(duration_ms),
        loop=0,
        optimize=False,
    )


def _mesh_from_cif(path: Path, *, grid_size: int = _GRID) -> Optional[Dict[str, Any]]:
    """Return mesh dict with verts/faces/lo/hi in nm, or None if isosurface fails."""
    import matplotlib

    matplotlib.use("Agg")
    from skimage.measure import marching_cubes

    from autosaxs.core.utils import calculate_atoms_density_and_isosurface, read_bodies_cif

    atoms = read_bodies_cif(str(path))
    if atoms is None or len(atoms) < 3:
        return None
    try:
        density, level, min_c, max_c = calculate_atoms_density_and_isosurface(
            atoms, grid_size=int(grid_size)
        )
    except ValueError:
        return None
    try:
        verts, faces, _, _ = marching_cubes(density, level=level)
    except ValueError:
        return None
    scale = (max_c - min_c) / (np.array(density.shape) - 1)
    verts = verts * scale + min_c
    # Å → nm
    verts_nm = np.asarray(verts, dtype=np.float64) * _A_TO_NM
    lo = verts_nm.min(axis=0)
    hi = verts_nm.max(axis=0)
    return {
        "verts": verts_nm,
        "faces": np.asarray(faces, dtype=np.int64),
        "lo": lo,
        "hi": hi,
    }


def _add_mesh(ax, mesh: Dict[str, Any], rgba: Tuple[float, float, float, float]) -> None:
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    r, g, b, a = rgba
    colors = [(r, g, b, a)] * len(mesh["faces"])
    poly = Poly3DCollection(
        mesh["verts"][mesh["faces"]],
        alpha=a,
        facecolors=colors,
        edgecolor="k",
        linewidth=0.04,
    )
    ax.add_collection3d(poly)


def _read_cif_xyz_occupancy(path: Path) -> Tuple[np.ndarray, np.ndarray]:
    """Parse ``_atom_site`` Cartn + occupancy; positions returned in nm."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    cols: List[str] = []
    data_start = None
    for i, raw in enumerate(lines):
        if raw.strip().lower() != "loop_":
            continue
        j = i + 1
        headers: List[str] = []
        while j < len(lines) and lines[j].strip().startswith("_"):
            headers.append(lines[j].strip().split()[0])
            j += 1
        if any(h.lower().endswith("cartn_x") for h in headers):
            cols = headers
            data_start = j
            break
    if data_start is None or not cols:
        from autosaxs.core.utils import read_bodies_cif

        atoms = read_bodies_cif(str(path))
        pts = np.asarray(atoms.positions, dtype=np.float64) * _A_TO_NM
        return pts, np.ones(len(pts), dtype=np.float64)

    def _idx(*names: str) -> Optional[int]:
        for i, c in enumerate(cols):
            cl = c.lower()
            for n in names:
                if cl.endswith(n.lower()) or cl == n.lower():
                    return i
        return None

    ix = _idx("Cartn_x")
    iy = _idx("Cartn_y")
    iz = _idx("Cartn_z")
    io = _idx("occupancy")
    if ix is None or iy is None or iz is None:
        from autosaxs.core.utils import read_bodies_cif

        atoms = read_bodies_cif(str(path))
        pts = np.asarray(atoms.positions, dtype=np.float64) * _A_TO_NM
        return pts, np.ones(len(pts), dtype=np.float64)

    xyz: List[List[float]] = []
    occ: List[float] = []
    for raw in lines[data_start:]:
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("_") or line.lower() == "loop_" or line.startswith("data_"):
            if xyz:
                break
            continue
        parts = line.split()
        if len(parts) <= max(ix, iy, iz):
            continue
        try:
            xyz.append([float(parts[ix]), float(parts[iy]), float(parts[iz])])
            if io is not None and io < len(parts):
                occ.append(float(parts[io]))
            else:
                occ.append(1.0)
        except (ValueError, IndexError):
            continue
    if not xyz:
        return np.zeros((0, 3), dtype=np.float64), np.zeros((0,), dtype=np.float64)
    pts = np.asarray(xyz, dtype=np.float64) * _A_TO_NM
    return pts, np.asarray(occ, dtype=np.float64)


def _find_aligned_run_cifs(output_dir: Path) -> List[Path]:
    dam = output_dir / "damaver"
    if dam.is_dir():
        aligned = sorted(dam.glob("damaver-global-dammif-*-1r.cif"))
        if aligned:
            return aligned
        aligned = sorted(dam.glob("*-dammif-*-1r.cif"))
        if aligned:
            return aligned
    return sorted(output_dir.glob("dammif-*-1.cif"))


def _find_frequency_map(output_dir: Path) -> Optional[Path]:
    dam = output_dir / "damaver"
    for name in (
        "damaver-global-damaver.cif",
        "damaver.cif",
    ):
        for root in (dam, output_dir):
            cand = root / name
            if cand.is_file():
                return cand
    if dam.is_dir():
        matches = [
            p
            for p in sorted(dam.glob("*damaver*.cif"))
            if "damfilt" not in p.name.lower() and "damstart" not in p.name.lower()
        ]
        if matches:
            return matches[0]
    return None


def _ensure_cifsup_aligned(static: Path, movable: Path, out_dir: Path) -> Path:
    if static.resolve() == movable.resolve():
        return movable
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{movable.stem}_aligned.cif"
    try:
        if out.is_file() and out.stat().st_mtime >= movable.stat().st_mtime:
            return out
    except OSError:
        pass
    cifsup = shutil.which("cifsup")
    if not cifsup:
        raise RuntimeError("cifsup not found on PATH (needed to align models for overlap visuals)")
    proc = subprocess.run(
        [cifsup, "--method=nsd", "-o", str(out), str(static), str(movable)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not out.is_file():
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"cifsup failed for {movable.name}: {err or 'no output'}")
    return out


def _prepare_run_cifs(output_dir: Path, best_cif: Optional[Path]) -> List[Tuple[int, Path]]:
    """Return [(run_idx, cif_path), ...] preferably DAMAVER-aligned *r.cif."""
    found = _find_aligned_run_cifs(output_dir)
    out: List[Tuple[int, Path]] = []
    if found and any(p.name.endswith("1r.cif") or "1r.cif" in p.name for p in found):
        for p in found:
            m = re.search(r"dammif-(\d+)-1r", p.name, flags=re.I)
            if not m:
                m = re.search(r"dammif-(\d+)", p.name, flags=re.I)
            idx = int(m.group(1)) if m else len(out) + 1
            out.append((idx, p))
        return sorted(out, key=lambda t: t[0])

    # Fallback: cifsup each run onto best
    raw = sorted(output_dir.glob("dammif-*-1.cif"))
    if not raw:
        if best_cif is not None and best_cif.is_file():
            return [(1, best_cif)]
        return []
    static = best_cif if best_cif is not None and best_cif.is_file() else raw[0]
    align_dir = output_dir / "aligned"
    for p in raw:
        m = re.match(r"dammif-(\d+)-1\.cif$", p.name, flags=re.I)
        idx = int(m.group(1)) if m else len(out) + 1
        aligned = _ensure_cifsup_aligned(static, p, align_dir)
        out.append((idx, aligned))
    return sorted(out, key=lambda t: t[0])


def write_visuals(
    output_dir: str,
    *,
    best_cif_path: str = "",
    frequency_map_path: str = "",
    event_bus: Any = None,
) -> Dict[str, Union[str, List[str]]]:
    """
    Write unlabeled PNGs/GIFs under ``{output_dir}/visuals/``.

    Includes a screen-space scale bar in nm on every frame. Per-run rotation GIFs share
    camera schedule and bbox so they stay synchronous when played together.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    od = Path(output_dir)
    pres = od / "visuals"
    pres.mkdir(parents=True, exist_ok=True)

    empty: Dict[str, Union[str, List[str]]] = {
        "visuals_dir": str(pres.resolve()),
        "overlap_png": "",
        "occupancy_png": "",
        "occupancy_thresholds_png": "",
        "overlap_gif": "",
        "occupancy_gif": "",
        "run_gifs": [],
    }

    best = Path(best_cif_path) if best_cif_path else None
    if best is not None and not best.is_file():
        best = od / "best.cif" if (od / "best.cif").exists() else None

    runs = _prepare_run_cifs(od, best)
    if not runs:
        if event_bus:
            from autosaxs.core.event_bus import EventType

            event_bus.publish(
                EventType.MESSAGE,
                {"text": "model_dam: visuals skipped (no particle CIFs)"},
            )
        return empty

    if event_bus:
        from autosaxs.core.event_bus import EventType

        event_bus.publish(
            EventType.MESSAGE,
            {"text": f"model_dam: writing visuals ({len(runs)} run(s))…"},
        )

    meshes: List[Dict[str, Any]] = []
    for run_idx, cif in runs:
        mesh = _mesh_from_cif(cif)
        if mesh is None:
            continue
        rgba = _OVERLAP_RGBA[(run_idx - 1) % len(_OVERLAP_RGBA)]
        meshes.append({"run": run_idx, "mesh": mesh, "rgba": rgba, "cif": cif})

    if not meshes:
        return empty

    lo = np.min(np.stack([m["mesh"]["lo"] for m in meshes], axis=0), axis=0)
    hi = np.max(np.stack([m["mesh"]["hi"] for m in meshes], axis=0), axis=0)
    span_nm = float(np.max(hi - lo))
    bar_nm = _round_scale_nm(span_nm)
    azims = [360.0 * i / _N_ROT for i in range(_N_ROT)]

    run_gifs: List[str] = []
    for item in meshes:
        frames = []
        for azim in azims:
            fig = plt.figure(figsize=_FIGSIZE, dpi=_DPI)
            fig.patch.set_facecolor("white")
            ax = fig.add_subplot(111, projection="3d")
            ax.set_axis_off()
            ax.set_facecolor("white")
            _add_mesh(ax, item["mesh"], item["rgba"])
            _equal_limits(ax, lo, hi)
            ax.view_init(elev=_ELEV, azim=azim)
            fig.tight_layout(pad=0.05)
            _draw_scale_bar_nm(ax, bar_nm=bar_nm, span_nm=span_nm)
            frames.append(_fig_to_rgb(fig))
            plt.close(fig)
        path = pres / f"dammif_run{item['run']}_rotate.gif"
        _save_gif(frames, path, duration_ms=_ROT_DURATION_MS)
        run_gifs.append(str(path.resolve()))

    result = dict(empty)
    result["run_gifs"] = run_gifs

    if len(meshes) >= 2:
        # Overlap GIF
        frames = []
        for azim in azims:
            fig = plt.figure(figsize=_FIGSIZE, dpi=_DPI)
            fig.patch.set_facecolor("white")
            ax = fig.add_subplot(111, projection="3d")
            ax.set_axis_off()
            ax.set_facecolor("white")
            for item in meshes:
                _add_mesh(ax, item["mesh"], item["rgba"])
            _equal_limits(ax, lo, hi)
            ax.view_init(elev=_ELEV, azim=azim)
            fig.tight_layout(pad=0.05)
            _draw_scale_bar_nm(ax, bar_nm=bar_nm, span_nm=span_nm)
            frames.append(_fig_to_rgb(fig))
            plt.close(fig)
        ogif = pres / "dammif_runs_overlap_rotate.gif"
        _save_gif(frames, ogif, duration_ms=_ROT_DURATION_MS)
        result["overlap_gif"] = str(ogif.resolve())

        # Overlap PNG (main + view2)
        for azim, name in ((35.0, "dammif_runs_overlap.png"), (125.0, "dammif_runs_overlap_view2.png")):
            fig = plt.figure(figsize=(7.0, 6.2), dpi=160)
            fig.patch.set_facecolor("white")
            ax = fig.add_subplot(111, projection="3d")
            ax.set_axis_off()
            ax.set_facecolor("white")
            for item in meshes:
                _add_mesh(ax, item["mesh"], item["rgba"])
            _equal_limits(ax, lo, hi)
            ax.view_init(elev=_ELEV, azim=azim)
            fig.tight_layout(pad=0.2)
            _draw_scale_bar_nm(ax, bar_nm=bar_nm, span_nm=span_nm)
            outp = pres / name
            fig.savefig(outp, dpi=160, facecolor="white")
            plt.close(fig)
            if name == "dammif_runs_overlap.png":
                result["overlap_png"] = str(outp.resolve())

    freq_path = Path(frequency_map_path) if frequency_map_path else None
    if freq_path is None or not freq_path.is_file():
        freq_path = _find_frequency_map(od)
    if freq_path is not None and freq_path.is_file():
        pts, occ_raw = _read_cif_xyz_occupancy(freq_path)
        if len(pts):
            occ_max = float(np.max(occ_raw)) if len(occ_raw) else 1.0
            occ = occ_raw / occ_max if occ_max > 0 else occ_raw
            lo_b, hi_b = pts.min(0), pts.max(0)
            span_b = float(np.max(hi_b - lo_b))
            bar_b = _round_scale_nm(span_b)
            azim_fixed = 35.0

            # Threshold GIF 0→1→0
            up = np.linspace(0.0, 1.0, 21)
            down = np.linspace(1.0, 0.0, 21)[1:]
            threshs = np.concatenate([up, down])
            frames = []
            for thr in threshs:
                mask = occ >= thr
                fig = plt.figure(figsize=_FIGSIZE, dpi=_DPI)
                fig.patch.set_facecolor("white")
                ax = fig.add_subplot(111, projection="3d")
                ax.set_axis_off()
                ax.set_facecolor("white")
                if np.any(mask):
                    ax.scatter(
                        pts[mask, 0],
                        pts[mask, 1],
                        pts[mask, 2],
                        c=occ[mask],
                        cmap="viridis",
                        vmin=0.0,
                        vmax=1.0,
                        s=8,
                        alpha=0.85,
                        depthshade=True,
                        edgecolors="none",
                    )
                _equal_limits(ax, lo_b, hi_b)
                ax.view_init(elev=_ELEV, azim=azim_fixed)
                fig.tight_layout(pad=0.05)
                _draw_scale_bar_nm(ax, bar_nm=bar_b, span_nm=span_b)
                frames.append(_fig_to_rgb(fig))
                plt.close(fig)
            ogif = pres / "damaver_occupancy_threshold.gif"
            _save_gif(frames, ogif, duration_ms=_OCC_DURATION_MS)
            result["occupancy_gif"] = str(ogif.resolve())

            # Hero PNG
            thr = 0.40
            mask = occ >= thr
            fig = plt.figure(figsize=(7.0, 6.2), dpi=160)
            fig.patch.set_facecolor("white")
            ax = fig.add_subplot(111, projection="3d")
            ax.set_axis_off()
            ax.set_facecolor("white")
            if np.any(mask):
                ax.scatter(
                    pts[mask, 0],
                    pts[mask, 1],
                    pts[mask, 2],
                    c=occ[mask],
                    cmap="viridis",
                    vmin=0.0,
                    vmax=1.0,
                    s=10,
                    alpha=0.88,
                    depthshade=True,
                    edgecolors="none",
                )
            _equal_limits(ax, lo_b, hi_b)
            ax.view_init(elev=_ELEV, azim=azim_fixed)
            fig.tight_layout(pad=0.2)
            _draw_scale_bar_nm(ax, bar_nm=bar_b, span_nm=span_b)
            opng = pres / "damaver_occupancy.png"
            fig.savefig(opng, dpi=160, facecolor="white")
            plt.close(fig)
            result["occupancy_png"] = str(opng.resolve())

            # Threshold panel PNG
            thresholds = [0.0, 0.25, 0.50, 0.75]
            fig, axes = plt.subplots(
                1, 4, figsize=(14.5, 4.2), dpi=150, subplot_kw={"projection": "3d"}
            )
            fig.patch.set_facecolor("white")
            for ax, thr in zip(axes, thresholds):
                ax.set_axis_off()
                ax.set_facecolor("white")
                mask = occ >= thr
                if np.any(mask):
                    ax.scatter(
                        pts[mask, 0],
                        pts[mask, 1],
                        pts[mask, 2],
                        c=occ[mask],
                        cmap="viridis",
                        vmin=0.0,
                        vmax=1.0,
                        s=5,
                        alpha=0.85,
                        depthshade=True,
                        edgecolors="none",
                    )
                _equal_limits(ax, lo_b, hi_b)
                ax.view_init(elev=_ELEV, azim=azim_fixed)
            fig.tight_layout()
            for ax in axes:
                _draw_scale_bar_nm(ax, bar_nm=bar_b, span_nm=span_b)
            tpng = pres / "damaver_occupancy_thresholds.png"
            fig.savefig(tpng, dpi=150, facecolor="white")
            plt.close(fig)
            result["occupancy_thresholds_png"] = str(tpng.resolve())

    return result
