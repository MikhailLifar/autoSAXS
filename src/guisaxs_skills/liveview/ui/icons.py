"""Toolbar icons for liveview analysis buttons (light ink for dark UI)."""

from __future__ import annotations

from PyQt5.QtCore import QPointF, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

# Light strokes for dark panel backgrounds.
_INK = QColor(236, 236, 240)
_INK_SOFT = QColor(200, 200, 210)
_FILL = QColor(230, 230, 238, 70)
_CURVE = QColor(190, 195, 210, 220)


def _decay_curve(*, w: float, h: float) -> QPainterPath:
    """I(q)-like decay along the lower-left → lower-right; leaves upper-right free."""
    path = QPainterPath()
    path.moveTo(0.08 * w, 0.58 * h)
    path.cubicTo(
        0.32 * w,
        0.22 * h,
        0.58 * w,
        0.72 * h,
        0.94 * w,
        0.90 * h,
    )
    return path


def monodisperse_analysis_icon(*, size: int = 128) -> QIcon:
    """Amorphous bead (upper-right) + scattering curve (lower band)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)

    pen_c = QPen(_CURVE)
    pen_c.setWidthF(max(2.5, size / 22.0))
    pen_c.setCapStyle(Qt.RoundCap)
    p.setPen(pen_c)
    p.setBrush(Qt.NoBrush)
    p.drawPath(_decay_curve(w=float(size), h=float(size)))

    # Soft amorphous blob in the clear upper-right quadrant (above the decaying curve).
    blob = QPainterPath()
    cx, cy = 0.70 * size, 0.28 * size
    r = 0.20 * size
    blob.moveTo(cx + 0.85 * r, cy)
    blob.cubicTo(cx + 0.95 * r, cy - 0.75 * r, cx + 0.15 * r, cy - 1.05 * r, cx - 0.45 * r, cy - 0.70 * r)
    blob.cubicTo(cx - 1.05 * r, cy - 0.20 * r, cx - 0.95 * r, cy + 0.65 * r, cx - 0.25 * r, cy + 0.85 * r)
    blob.cubicTo(cx + 0.40 * r, cy + 1.05 * r, cx + 1.05 * r, cy + 0.55 * r, cx + 0.85 * r, cy)

    pen = QPen(_INK)
    pen.setWidthF(max(2.8, size / 18.0))
    p.setPen(pen)
    p.setBrush(_FILL)
    p.drawPath(blob)
    p.end()
    return QIcon(pm)


def polydisperse_analysis_icon(*, size: int = 128) -> QIcon:
    """Scattered spheres (upper-right) + scattering curve (lower band)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)

    pen_c = QPen(_CURVE)
    pen_c.setWidthF(max(2.5, size / 22.0))
    pen_c.setCapStyle(Qt.RoundCap)
    p.setPen(pen_c)
    p.setBrush(Qt.NoBrush)
    p.drawPath(_decay_curve(w=float(size), h=float(size)))

    pen = QPen(_INK)
    pen.setWidthF(max(2.4, size / 20.0))
    p.setPen(pen)
    p.setBrush(_FILL)
    # Scattered in the clear upper-right band — not overlapping the curve.
    spheres = (
        (0.58, 0.18, 0.075),
        (0.82, 0.16, 0.055),
        (0.70, 0.34, 0.095),
        (0.88, 0.36, 0.045),
        (0.55, 0.40, 0.050),
    )
    for fx, fy, fr in spheres:
        r = fr * size
        p.drawEllipse(QPointF(fx * size, fy * size), r, r)
    p.end()
    return QIcon(pm)
