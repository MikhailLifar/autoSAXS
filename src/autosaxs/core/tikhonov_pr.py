"""In-process Tikhonov solvers for AutoGNOM Stage 1 (no ATSAS in the loop).

Supports:

- **Monodisperse** GNOM system=0 — Fourier-sine ``p(r)`` kernel (``fit_distances``)
- **Polydisperse spheres** GNOM system=1 — volume-distribution kernel
  ``I(q) = ∫ D_v(R) V(R) Φ²(qR) dR`` with ``V=(4/3)πR³`` and
  ``Φ(x)=3(sin x − x cos x)/x³`` (``fit_sizes``; verified against ATSAS
  ``gnom --system=1`` ``I_fit``)

Default α packaging is the locked **H32** recipe from GNOM reverse-engineering
(stock d1 + ``κ·(2/n_free)·lmb·dr²``), so printed GNOM α is used directly —
Stage 2 re-solves with ATSAS ``gnom`` at the same ``(extent, α)``.

Units: ``q`` in nm⁻¹, ``r`` / ``Dmax`` / ``Rmax`` in nm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional, Tuple

import numpy as np

KernelKind = Literal["sinc", "4pi_sinc", "4pi_r_sinc", "sphere_v_phi2"]
StabilizerKind = Literal["d2", "d1", "I", "d2_weighted"]
QuadKind = Literal["trapz", "rect", "simpson_like"]

# Product default: H32-locked recipe for system=0 search (GNOM-close α).
DEFAULT_KERNEL: KernelKind = "4pi_sinc"
DEFAULT_STABILIZER: StabilizerKind = "d1"
DEFAULT_QUAD: QuadKind = "trapz"
DEFAULT_NR = 199
# GNOM system=1 spheres — Stage-1 default for fit_sizes.
DEFAULT_SPHERE_KERNEL: KernelKind = "sphere_v_phi2"

# H32: κ = (4π)²√π = C₀/(4π)² with C₀ = (4π)⁴√π (H24b).
H32_KAPPA = (4.0 * math.pi) ** 2 * math.sqrt(math.pi)


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
    smoothness_scale: float = 1.0


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


def sphere_form_factor_squared(qr: np.ndarray) -> np.ndarray:
    """Solid-sphere ``Φ²(x)`` with ``Φ(x) = 3 (sin x − x cos x) / x³`` (``Φ(0)=1``)."""
    x = np.asarray(qr, dtype=float)
    out = np.empty_like(x, dtype=float)
    small = np.abs(x) < 1e-8
    out[small] = 1.0
    xs = x[~small]
    out[~small] = (3.0 * (np.sin(xs) - xs * np.cos(xs)) / (xs**3)) ** 2
    return out


def build_kernel(
    q: np.ndarray,
    r: np.ndarray,
    *,
    kind: KernelKind = DEFAULT_KERNEL,
    quad: QuadKind = DEFAULT_QUAD,
) -> np.ndarray:
    """Design matrix ``K`` such that ``I ≈ K @ p`` (or ``K @ D_v`` for spheres)."""
    w = _quad_weights(r, quad)
    qr = np.outer(q, r)
    if kind == "sphere_v_phi2":
        # GNOM system=1: I(q) = ∫ D_v(R) · V(R) · Φ²(qR) dR, V=(4/3)πR³.
        vol = (4.0 / 3.0) * np.pi * np.maximum(r, 0.0) ** 3
        return sphere_form_factor_squared(qr) * vol[None, :] * w[None, :]
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


def generalized_lambda_max(ATA: np.ndarray, S: np.ndarray) -> float:
    """Largest generalized eigenvalue of ``(ATA, S)`` via ``S``-whitening."""
    n = int(S.shape[0])
    if n == 0:
        return float("nan")
    Sr = S + 1e-14 * (float(np.trace(S)) / max(n, 1) + 1.0) * np.eye(n)
    w, V = np.linalg.eigh(Sr)
    w = np.maximum(w, 1e-30)
    Wis = V @ np.diag(1.0 / np.sqrt(w)) @ V.T
    return float(np.max(np.linalg.eigvalsh(Wis @ ATA @ Wis)))


def h32_smoothness_scale(
    ATA: np.ndarray,
    S: np.ndarray,
    *,
    dmax: float,
    nr: int,
    n_free: Optional[int] = None,
) -> float:
    """
    Locked H32 smoothness packaging ``s`` for ``||Lp||²``.

    ``s = κ · (2/n_free) · lmb · dr²`` with
    ``lmb = λ_max(ATA,S) / (tr(ATA)/n_free)``, ``κ = (4π)²√π``,
    ``dr = D/(nr-1)``, ``n_free = nr-2`` under hard force-zero ends.
    """
    nf = int(n_free if n_free is not None else S.shape[0])
    if nf < 1:
        raise ValueError("h32_smoothness_scale: n_free must be ≥1")
    tr = float(np.trace(ATA))
    if not (tr > 0 and np.isfinite(tr)):
        raise ValueError("h32_smoothness_scale: ATA trace must be positive")
    lmb = generalized_lambda_max(ATA, S) / (tr / nf)
    dr = float(dmax) / (int(nr) - 1)
    if not (dr > 0 and np.isfinite(dr) and np.isfinite(lmb) and lmb > 0):
        raise ValueError(
            f"h32_smoothness_scale: invalid lmb={lmb} dr={dr} dmax={dmax} nr={nr}"
        )
    return float(H32_KAPPA * (2.0 / nf) * lmb * dr * dr)


def solve_tikhonov(
    q: np.ndarray,
    I: np.ndarray,
    sigma: np.ndarray,
    *,
    dmax: float,
    alpha: float,
    nr: int = DEFAULT_NR,
    rmin: float = 0.0,
    force_zero_rmin: bool = True,
    force_zero_rmax: bool = True,
    kernel: KernelKind = DEFAULT_KERNEL,
    stabilizer: StabilizerKind = DEFAULT_STABILIZER,
    quad: QuadKind = DEFAULT_QUAD,
    alpha_scale: float = 1.0,
    use_h32: bool = True,
) -> TikhonovSolveResult:
    """
    Solve ``min ||W(Kp-I)||² + (α · s) ||Lp||²`` with optional hard endpoint zeros.

    ``dmax`` is the upper real-space bound (Dmax for system=0, Rmax for system=1).
    Optional ``rmin`` shifts the grid to ``[rmin, dmax]`` (polydisperse ``--rmin``).

    Default ``use_h32=True``: ``s`` is the H32 packaging so ``alpha`` is in
    **GNOM-printed** units (Stage 2 can pass the same α to ATSAS ``gnom``).
    When ``use_h32=False``, ``s = alpha_scale`` (legacy dual-anchor path).
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

    r_lo = float(rmin) if np.isfinite(float(rmin)) else 0.0
    r_hi = float(dmax)
    if not (r_hi > r_lo and np.isfinite(r_hi) and np.isfinite(r_lo)):
        raise ValueError(f"solve_tikhonov: require dmax > rmin; got rmin={rmin} dmax={dmax}")
    r_span = r_hi - r_lo

    r = np.linspace(r_lo, r_hi, nr_i)
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
    if not (float(alpha) > 0 and np.isfinite(float(alpha))):
        raise ValueError(f"solve_tikhonov: invalid alpha={alpha}")

    A = K * W[:, None]
    rhs = I * W
    ATA0 = A.T @ A
    S = L.T @ L
    if use_h32:
        # H32 ``dr`` uses the real-space span (Dmax when rmin=0; Rmax−rmin otherwise).
        s = h32_smoothness_scale(
            ATA0, S, dmax=float(r_span), nr=nr_i, n_free=int(idx.size)
        )
        s = float(s) * float(alpha_scale)
    else:
        s = float(alpha_scale)
        if not (s > 0 and np.isfinite(s)):
            raise ValueError(f"solve_tikhonov: invalid alpha_scale={alpha_scale}")

    a = float(alpha) * s
    ATA = ATA0 + a * S
    ATA = ATA + (1e-12 * np.trace(ATA) / ATA.shape[0]) * np.eye(ATA.shape[0])
    p_free = np.linalg.solve(ATA, A.T @ rhs)
    p = np.zeros(nr_i, dtype=float)
    p[idx] = p_free

    I_fit = K_full @ p
    res2 = ((I - I_fit) / sig) ** 2
    chi2_mean = float(np.mean(res2))
    chi2_med = float(np.median(res2))
    if str(kernel) == "sphere_v_phi2":
        # Size-distribution moments are not Guinier Rg / I(0); leave unset.
        rg, i0 = float("nan"), float("nan")
    else:
        rg, i0 = rg_i0_from_pr(r, p)

    pack = "h32" if use_h32 else f"scale={float(alpha_scale):g}"
    variant = f"{kernel}|{stabilizer}|{quad}|{pack}|nr={nr_i}"
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
        smoothness_scale=float(s),
    )
