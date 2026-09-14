from __future__ import annotations

import os
from pathlib import Path

import numpy as np


def render_mask_overlay_png(
    *,
    image_path: str,
    mask_path: str,
    out_path: str,
) -> bool:
    """Render mask overlay PNG for the left-panel preview.

    When ``image_path`` is missing or unreadable, draws a black (cmap-min) frame
    sized to the mask — same idea as the dedicated mask wizard's blank frame.

    Uses the Agg backend only (no Qt canvas) so liveview shutdown is not left
    with orphaned matplotlib/Qt objects from this offscreen render.
    """
    # Import Agg canvas before Figure so this figure never binds to Qt5Agg.
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    mask = None
    try:
        from autosaxs.core.integrator import IntegratorExtended

        mask = IntegratorExtended.read_mask(mask_path)
    except Exception:
        mask = None
    if mask is None:
        return False
    m = np.asarray(mask, dtype=bool)

    img = None
    ip = (image_path or "").strip()
    if ip and os.path.isfile(ip):
        try:
            import fabio

            img = fabio.open(ip).data
        except Exception:
            img = None
        if img is None:
            try:
                import tifffile

                img = tifffile.imread(ip)
            except Exception:
                img = None

    if img is not None:
        a = np.asarray(img, dtype=float)
        if a.ndim > 2:
            a = a.reshape((-1,) + a.shape[-2:])[0]
        if a.shape != m.shape:
            return False
        display = np.log1p(np.maximum(a, 0.0))
        vmin = vmax = None
        title = Path(mask_path).name
    else:
        display = np.zeros(m.shape, dtype=float)
        vmin, vmax = 0.0, 1.0
        title = f"{Path(mask_path).name} (no image)"

    fig = Figure(figsize=(6, 5), dpi=120)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    imshow_kw = dict(cmap="viridis", origin="lower", aspect="equal", interpolation="nearest")
    if vmin is not None and vmax is not None:
        imshow_kw["vmin"] = vmin
        imshow_kw["vmax"] = vmax
    ax.imshow(display, **imshow_kw)
    rgba = np.zeros((m.shape[0], m.shape[1], 4), dtype=float)
    rgba[m, 0] = 1.0
    rgba[m, 3] = 0.45
    ax.imshow(rgba, origin="lower", aspect="equal", interpolation="nearest")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(title)
    fig.tight_layout()
    try:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, format="png")
    finally:
        fig.clf()
        # Avoid matplotlib.pyplot.close — it can touch the interactive Qt backend.
    return os.path.exists(out_path) and os.path.getsize(out_path) > 0
