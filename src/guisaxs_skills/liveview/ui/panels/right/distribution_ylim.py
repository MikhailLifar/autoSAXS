"""Shared y-limits for P(r) / D(R) (and related distribution) plots."""

from __future__ import annotations

DIST_YLIM_MIN = -5.0
DIST_YLIM_MAX = 20.0


def clamp_distribution_ylim(ax) -> None:
    """
    Keep distribution y-axis within ``[-5, 20]``.

    Uses the axis' current (autoscaled) limits when they are narrower;
    never expands beyond the floor/ceiling.
    """
    ymin, ymax = ax.get_ylim()
    lo = max(DIST_YLIM_MIN, float(ymin))
    hi = min(DIST_YLIM_MAX, float(ymax))
    if not (lo < hi):
        lo, hi = DIST_YLIM_MIN, DIST_YLIM_MAX
    ax.set_ylim(lo, hi)
