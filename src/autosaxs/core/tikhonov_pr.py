"""In-process Tikhonov P(r) solver for monodisperse GNOM (system=0) search.

Used by AutoGNOM's fast path: optimise ``(Dmax, α)`` under ``shape_tight_extent``
without calling ATSAS in the inner loop. Not bit-identical to ATSAS GNOM —
``α`` is remapped via a per-curve ``alpha_scale`` (see product docs).

Units: ``q`` in nm⁻¹, ``r`` / ``Dmax`` in nm.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Tuple

import numpy as np

KernelKind = Literal["sinc", "4pi_sinc", "4pi_r_sinc"]
StabilizerKind = Literal["d2", "d1", "I", "d2_weighted"]
QuadKind = Literal["trapz", "rect", "simpson_like"]

# Product default: fidelity-validated recipe for system=0 search.
DEFAULT_KERNEL: KernelKind = "4pi_sinc"
DEFAULT_STABILIZER: StabilizerKind = "d1"
DEFAULT_QUAD: QuadKind = "trapz"
DEFAULT_NR = 199


@dataclass(frozen=True)
class TikhonovSolveResult:
    """Solution of the regularized linear inverse for one ``(Dmax, α)``."""

    r: np.ndarray
    p: np.ndarray
    I_fit: np.ndarray
    q: np.ndarray
    chi2_mean: float
    chi2_med: float
    rg_pr: float
    i0_pr: float
    alpha: float
    dmax: float
    variant: str


def _quad_weights(r: np.ndarray, kind: QuadKind) -> np.ndarray:
    n = r.size
    if n < 2:
        return np.ones(n)
    dr = float(r[1] - r[0])
    w = np.ones(n) * dr
    if kind == "rect":
        return w
    if kind == "trapz":
        w[0] *= 0.5
        w[-1] *= 0.5
        return w
    w[:] = dr / 3.0
    w[1:-1:2] = 4.0 * dr / 3.0
    w[2:-1:2] = 2.0 * dr / 3.0
    if n % 2 == 0:
        w[-1] = dr / 2.0
        w[-2] = dr / 2.0 + (2.0 * dr / 3.0 if n > 2 else 0.0)
    return w


def build_kernel(
    q: np.ndarray,
    r: np.ndarray,
    *,
    kind: KernelKind = DEFAULT_KERNEL,
    quad: QuadKind = DEFAULT_QUAD,
) -> np.ndarray:
    """Design matrix ``K`` such that ``I ≈ K @ p``."""
    w = _quad_weights(r, quad)
    qr = np.outer(q, r)
    with np.errstate(divide="ignore", invalid="ignore"):
        sinc = np.where(np.abs(qr) < 1e-15, 1.0, np.sin(qr) / qr)
    if kind == "sinc":
        return sinc * w
    if kind == "4pi_sinc":
        return (4.0 * np.pi) * sinc * w
    if kind == "4pi_r_sinc":
        return (4.0 * np.pi) * sinc * w * np.maximum(r, 1e-30)
    raise ValueError(f"unknown kernel kind: {kind!r}")


def build_stabilizer(
    n: int,
    kind: StabilizerKind,
    r: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Return ``L`` with shape ``(m, n)`` for the ``||L p||²`` penalty."""
    if kind == "I":
        return np.eye(n)
    if kind == "d1":
        L = np.zeros((max(n - 1, 0), n))
        for i in range(n - 1):
            L[i, i] = -1.0
            L[i, i + 1] = 1.0
        if r is not None and n > 1:
            dr = float(r[1] - r[0])
            if dr > 0:
                L /= dr
        return L
    if kind in ("d2", "d2_weighted"):
        L = np.zeros((max(n - 2, 0), n))
        for i in range(n - 2):
            L[i, i] = 1.0
            L[i, i + 1] = -2.0
            L[i, i + 2] = 1.0
        if r is not None and n > 2:
            dr = float(r[1] - r[0])
            if dr > 0:
                L /= dr**2
        if kind == "d2_weighted" and r is not None:
            scale = np.linspace(0.5, 1.5, L.shape[0])
            L = L * scale[:, None]
        return L
    raise ValueError(f"unknown stabilizer kind: {kind!r}")


def rg_i0_from_pr(r: np.ndarray, p: np.ndarray) -> Tuple[float, float]:
    """``Rg``, ``I(0)`` assuming ``I(q)=4π∫ p sinc``; ``I0=4π∫p``; ``Rg²=∫r²p/(2∫p)``."""
    trap = getattr(np, "trapezoid", None) or np.trapz
    r = np.asarray(r, dtype=float)
    p = np.asarray(p, dtype=float)
    m = np.isfinite(r) & np.isfinite(p)
    r, p = r[m], p[m]
    if r.size < 2:
        return float("nan"), float("nan")
    integ = float(trap(p, r))
    integ_r2 = float(trap(p * r * r, r))
    i0 = 4.0 * np.pi * integ
    if abs(integ) < 1e-30:
        return float("nan"), i0
    rg2 = integ_r2 / (2.0 * integ)
    rg = float(np.sqrt(rg2)) if rg2 > 0 else float("nan")
    return rg, i0


def nr_for_dmax(dmax_nm: float, *, nr_seed: int, dmax_seed_nm: float) -> int:
    """Scale real-space point count with ``Dmax`` (clamped to a GNOM-like range)."""
    d = float(dmax_nm)
    d0 = float(dmax_seed_nm)
    n0 = int(nr_seed)
    if not (d > 0 and np.isfinite(d)):
        return max(51, min(501, n0 if n0 > 0 else DEFAULT_NR))
    if d0 > 0 and np.isfinite(d0) and n0 > 0:
        n = int(round(n0 * d / d0))
    else:
        # ~0.03 nm spacing heuristic when no seed is available
        n = int(round(d / 0.03)) + 1
    return max(51, min(501, n))


def solve_tikhonov(
    q: np.ndarray,
    I: np.ndarray,
    sigma: np.ndarray,
    *,
    dmax: float,
    alpha: float,
    nr: int = DEFAULT_NR,
    force_zero_rmin: bool = True,
    force_zero_rmax: bool = True,
    kernel: KernelKind = DEFAULT_KERNEL,
    stabilizer: StabilizerKind = DEFAULT_STABILIZER,
    quad: QuadKind = DEFAULT_QUAD,
    alpha_scale: float = 1.0,
) -> TikhonovSolveResult:
    """
    Solve ``min ||W(Kp-I)||² + (α·α_scale) ||Lp||²`` with optional hard endpoint zeros.

    ``alpha`` is in **GNOM-coordinate** units when ``alpha_scale`` was calibrated
    against a reference ATSAS solve; the NumPy effective multiplier is ``α·α_scale``.
    """
    q = np.asarray(q, dtype=float)
    I = np.asarray(I, dtype=float)
    sig = np.asarray(sigma, dtype=float)
    mask = np.isfinite(q) & np.isfinite(I) & np.isfinite(sig) & (sig > 0) & (q >= 0)
    q, I, sig = q[mask], I[mask], sig[mask]
    if q.size < 3:
        raise ValueError("solve_tikhonov: need ≥3 finite (q,I,σ) points")

    nr_i = int(nr)
    if nr_i < 3:
        raise ValueError(f"solve_tikhonov: nr must be ≥3; got {nr}")

    r = np.linspace(0.0, float(dmax), nr_i)
    K_full = build_kernel(q, r, kind=kernel, quad=quad)
    L_full = build_stabilizer(nr_i, stabilizer, r=r)

    free = np.ones(nr_i, dtype=bool)
    if force_zero_rmin:
        free[0] = False
    if force_zero_rmax:
        free[-1] = False
    idx = np.where(free)[0]
    if idx.size == 0:
        raise RuntimeError("solve_tikhonov: no free p(r) degrees of freedom")

    K = K_full[:, idx]
    L = L_full[:, idx]
    W = 1.0 / sig
    a = float(alpha) * float(alpha_scale)
    if not (a > 0 and np.isfinite(a)):
        raise ValueError(f"solve_tikhonov: invalid alpha={alpha} scale={alpha_scale}")

    A = K * W[:, None]
    rhs = I * W
    ATA = A.T @ A + a * (L.T @ L)
    ATA = ATA + (1e-12 * np.trace(ATA) / ATA.shape[0]) * np.eye(ATA.shape[0])
    p_free = np.linalg.solve(ATA, A.T @ rhs)
    p = np.zeros(nr_i, dtype=float)
    p[idx] = p_free

    I_fit = K_full @ p
    res2 = ((I - I_fit) / sig) ** 2
    chi2_mean = float(np.mean(res2))
    chi2_med = float(np.median(res2))
    rg, i0 = rg_i0_from_pr(r, p)

    variant = f"{kernel}|{stabilizer}|{quad}|as={float(alpha_scale):g}|nr={nr_i}"
    return TikhonovSolveResult(
        r=r,
        p=p,
        I_fit=I_fit,
        q=q,
        chi2_mean=chi2_mean,
        chi2_med=chi2_med,
        rg_pr=rg,
        i0_pr=i0,
        alpha=float(alpha),
        dmax=float(dmax),
        variant=variant,
    )


def calibrate_alpha_scale(
    q: np.ndarray,
    I: np.ndarray,
    sigma: np.ndarray,
    *,
    dmax: float,
    alpha: float,
    r_ref: np.ndarray,
    p_ref: np.ndarray,
    nr: int,
    force_zero_rmin: bool = True,
    force_zero_rmax: bool = True,
    scales: Optional[np.ndarray] = None,
    kernel: KernelKind = DEFAULT_KERNEL,
    stabilizer: StabilizerKind = DEFAULT_STABILIZER,
    quad: QuadKind = DEFAULT_QUAD,
) -> Tuple[float, float]:
    """
    Pick ``alpha_scale`` maximizing p(r) correlation vs a reference GNOM solution.

    Returns ``(best_scale, best_correlation)``.
    """
    if scales is None:
        scales = np.logspace(-6, 6, 49)
    r_ref = np.asarray(r_ref, dtype=float)
    p_ref = np.asarray(p_ref, dtype=float)
    best_s, best_corr, best_l2 = 1.0, -1.0, float("inf")
    for sc in scales:
        try:
            sol = solve_tikhonov(
                q,
                I,
                sigma,
                dmax=float(dmax),
                alpha=float(alpha),
                nr=int(nr),
                force_zero_rmin=force_zero_rmin,
                force_zero_rmax=force_zero_rmax,
                kernel=kernel,
                stabilizer=stabilizer,
                quad=quad,
                alpha_scale=float(sc),
            )
        except Exception:
            continue
        # Interpolate NumPy p onto GNOM r grid for correlation
        if sol.r.size < 2 or r_ref.size < 2:
            continue
        p_on_ref = np.interp(r_ref, sol.r, sol.p)
        m = np.isfinite(p_ref) & np.isfinite(p_on_ref)
        if int(np.count_nonzero(m)) < 8:
            continue
        pref = p_ref[m]
        pnp = p_on_ref[m]
        a = pref - np.mean(pref)
        b = pnp - np.mean(pnp)
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denom <= 0:
            continue
        corr = float(np.dot(a, b) / denom)
        # Amplitude-matched relative L2 (shape corr alone is flat for oversmoothed p)
        scale_p = float(np.dot(pref, pnp) / (np.dot(pnp, pnp) + 1e-30))
        rel_l2 = float(np.linalg.norm(pref - scale_p * pnp) / (np.linalg.norm(pref) + 1e-30))
        better = corr > best_corr + 1e-6 or (
            abs(corr - best_corr) <= 1e-6 and rel_l2 < best_l2
        )
        # Prefer lower L2 among near-tied correlations (corr within 0.1% of best)
        if corr >= 0.999 * best_corr and rel_l2 < best_l2 - 1e-6 and best_corr > 0:
            better = True
        if better:
            best_corr = corr
            best_l2 = rel_l2
            best_s = float(sc)
    return best_s, best_corr
