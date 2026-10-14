from __future__ import annotations

import csv
import os
import sys
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
from scipy.optimize import nnls

from .common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
    PathExpressionArg,
    coerce_dat_path_expression,
    coerce_path_expression,
    expand_files_from_unwrapped,
)
from .deps import (
    EventBus,
    EventType,
    _strip_sub_int_prefix,
    apply_batch,
    ensure_q_nm,
    load_saxs_1d_any,
    run_with_cache,
    write_saxs,
)


def model_lc(
    profile: DatPathExpressionArg,
    database: PathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    n_components: int = 3,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    """
    SAXS / small-angle x-ray scattering: fit an experimental profile as a nonnegative linear
    combination of library curves (custom NNLS — **not** ATSAS OLIGOMER).

    Library input is either:

    - one CSV: column 0 = q (nm⁻¹), remaining columns = component intensities; or
    - a path expression / directory / glob / comma-list of `.dat` curves.

    When the library has more curves than `n_components`, all combinations of size
    ``k = 1..n_components`` are scored with NNLS and the lowest χ² wins.

    **Complexity:** for library size ``N`` and ``k_max = n_components``, the search evaluates
    ``Σ_{k=1}^{k_max} C(N,k)`` NNLS problems, each roughly ``O(n_q · k²)``. Prefer
    ``N ≲ 50`` for ``k_max=3`` (``C(50,3)=19600``). When ``N ≤ n_components``, a single
    NNLS on all columns is used (fast path).

    Curves are interpolated onto the experimental q grid (nm⁻¹). Points outside a component's
    q coverage are excluded from that component's column (set NaN → row dropped if any NaN).

    ### Arguments

    - `profile` (str): Experimental 1D path expression (file/directory/glob → `.dat`).
    - `database` (str): Library CSV **or** `.dat` path expression / directory / glob / list.
    - `output_dir` (str, default `.`): Directory where outputs are written.
    - `config_path` (str | None, default `None`): Deprecated. Unused.
    - `n_components` (int, default `3`): Maximum number of library curves in the combination (1–3).
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    ### Returns

    `dict[str, str]` with:

    - `fit_path`: Fitted I(q) autosaxs `.dat` (q in nm⁻¹).
    - `residual_path`: Residual (I_exp − I_fit) `.dat`.
    - `weights_path`: CSV of chosen component names/indices and nonnegative weights.
    - `plot_path`: Overlay PNG (experiment vs fit ± components).
    - `components_used`: Comma-separated component labels chosen by the search.
    - `chi2`: Reduced χ² string (for CLI/path-dict stability).
    - `output_subdir`: Output directory for this sample.

    Does **not** require ATSAS.

    ### Python usage

    ```python
    from autosaxs.skill import model_lc

    out = model_lc(
        profile="subtracted/sample.dat",
        database="library/",           # dir of .dat
        output_dir="lc_out",
        n_components=3,
    )
    # or: database="ff_library.csv"
    print(out["weights_path"], out["components_used"])
    ```

    ### CLI usage

    ```bash
    autosaxs model-lc subtracted/sample.dat library/ --output-dir lc_out --n-components 3
    autosaxs model-lc sample.dat ff_library.csv -o lc_out --n-components 2
    ```
    """
    _ = config_path
    if not isinstance(n_components, int) or n_components < 1 or n_components > 3:
        raise ValueError("model_lc: n_components must be an integer in 1..3")

    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))

    profile_pe = coerce_dat_path_expression(profile)
    profiles = expand_files_from_unwrapped(profile_pe.unwrap(), kind="1d_dat")
    for p in profiles:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("model_lc profile files must have .dat extension")

    db_resolved = _resolve_database(database)
    db_for_paths: Union[str, List[str]]
    if db_resolved["kind"] == "csv":
        db_for_paths = str(db_resolved["csv"])
    else:
        db_for_paths = list(db_resolved["paths"])
    input_batch = [{"profile": p, "database": db_for_paths} for p in profiles]
    return _model_lc_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        n_components=int(n_components),
        database_kind=str(db_resolved["kind"]),
    )


def _resolve_database(database: PathExpressionArg) -> Dict[str, Union[str, List[str]]]:
    """
    Resolve library input to either a CSV path or a list of .dat paths.

    Returns dict with keys: kind ('csv'|'dats'), token (hashable path string for batch),
    paths (list), csv (optional).
    """
    pe = coerce_path_expression(database)
    items = pe.unwrap()
    # Single CSV file
    if len(items) == 1 and Path(items[0]).suffix.lower() == ".csv" and os.path.isfile(items[0]):
        return {"kind": "csv", "token": items[0], "paths": [], "csv": items[0]}

    dats: List[str] = []
    for item in items:
        if os.path.isdir(item):
            dats.extend(
                str(p)
                for p in sorted(Path(item).iterdir())
                if p.is_file() and p.suffix.lower() == ".dat"
            )
        elif os.path.isfile(item):
            suf = Path(item).suffix.lower()
            if suf == ".csv":
                if len(items) != 1:
                    raise ValueError("model_lc: when using CSV database, pass exactly one .csv path")
                return {"kind": "csv", "token": item, "paths": [], "csv": item}
            if suf != ".dat":
                raise ValueError(
                    f"model_lc database entries must be .dat or one .csv, got {suf!r} ({item})"
                )
            dats.append(item)
        else:
            raise FileNotFoundError(f"model_lc database path not found: {item}")

    dats = list(dict.fromkeys(dats))  # stable unique
    if not dats:
        raise FileNotFoundError("model_lc: no library .dat files found in database")
    token = dats[0] if len(dats) == 1 else ",".join(dats)
    return {"kind": "dats", "token": token, "paths": dats, "csv": ""}


def _load_library_matrix(
    *,
    kind: str,
    csv_path: Optional[str],
    dat_paths: Sequence[str],
    q_exp: np.ndarray,
) -> Tuple[np.ndarray, List[str]]:
    """
    Build A (n_q × N) interpolated onto q_exp. Returns (A, labels).
    Rows with any non-finite library value are marked NaN in that row.
    """
    q_exp = np.asarray(q_exp, dtype=float)
    columns: List[np.ndarray] = []
    labels: List[str] = []

    if kind == "csv":
        if not csv_path or not os.path.isfile(csv_path):
            raise FileNotFoundError(f"model_lc: CSV database not found: {csv_path}")
        raw = np.genfromtxt(csv_path, delimiter=",", dtype=float)
        if raw.ndim != 2 or raw.shape[1] < 2:
            raise ValueError(
                "model_lc: CSV must have q in column 0 and ≥1 intensity columns "
                f"(got shape {getattr(raw, 'shape', None)})"
            )
        # Drop header-only NaN rows at top if present
        finite_rows = np.isfinite(raw[:, 0])
        raw = raw[finite_rows]
        q_lib = raw[:, 0]
        q_lib, _, _ = ensure_q_nm(q_lib, raw[:, 1], None)
        for j in range(1, raw.shape[1]):
            I_j = raw[:, j]
            col = _interp_onto(q_exp, q_lib, I_j)
            columns.append(col)
            labels.append(f"col{j}")
    else:
        if not dat_paths:
            raise FileNotFoundError("model_lc: empty .dat library")
        for path in dat_paths:
            q_i, I_i, _ = load_saxs_1d_any(path)
            q_i, I_i, _ = ensure_q_nm(q_i, I_i, None)
            columns.append(_interp_onto(q_exp, q_i, I_i))
            labels.append(Path(path).stem)

    A = np.column_stack(columns)
    return A, labels


def _interp_onto(q_tgt: np.ndarray, q_src: np.ndarray, I_src: np.ndarray) -> np.ndarray:
    """Interpolate I_src(q_src) onto q_tgt; NaN outside [q_src.min, q_src.max]."""
    q_src = np.asarray(q_src, dtype=float)
    I_src = np.asarray(I_src, dtype=float)
    order = np.argsort(q_src)
    q_src = q_src[order]
    I_src = I_src[order]
    out = np.full_like(q_tgt, np.nan, dtype=float)
    lo, hi = float(q_src[0]), float(q_src[-1])
    mask = (q_tgt >= lo) & (q_tgt <= hi) & np.isfinite(q_tgt)
    if np.any(mask):
        out[mask] = np.interp(q_tgt[mask], q_src, I_src)
    return out


def _chi2(y: np.ndarray, yhat: np.ndarray, sigma: Optional[np.ndarray], n_params: int) -> float:
    resid = y - yhat
    if sigma is not None:
        s = np.asarray(sigma, dtype=float)
        s = np.where(np.isfinite(s) & (s > 0), s, np.nan)
        if np.any(np.isfinite(s)):
            w = 1.0 / np.where(np.isfinite(s), s, np.inf) ** 2
            # fallback weight 1 where sigma missing
            w = np.where(np.isfinite(w), w, 1.0)
        else:
            w = np.ones_like(y)
    else:
        w = np.ones_like(y)
    dof = max(int(len(y) - n_params), 1)
    return float(np.sum(w * resid**2) / dof)


def fit_nnls_sparse(
    A: np.ndarray,
    y: np.ndarray,
    *,
    sigma: Optional[np.ndarray] = None,
    n_components: int = 3,
) -> Tuple[np.ndarray, np.ndarray, float, Tuple[int, ...]]:
    """
    Nonnegative least-squares with cardinality ≤ n_components.

    Fast path: if ``A.shape[1] <= n_components``, one NNLS on all columns.
    Else: brute-force combinations of size 1..n_components; pick lowest χ².

    Returns (weights_full, y_fit, chi2, chosen_indices).
    """
    A = np.asarray(A, dtype=float)
    y = np.asarray(y, dtype=float)
    if A.ndim != 2:
        raise ValueError("A must be 2D")
    n_q, n_lib = A.shape
    if y.shape[0] != n_q:
        raise ValueError("y length must match A rows")
    if n_lib < 1:
        raise ValueError("library is empty")

    # Drop rows with any NaN in A or y
    row_ok = np.isfinite(y) & np.all(np.isfinite(A), axis=1)
    if sigma is not None:
        # keep rows even if sigma bad; chi2 handles it
        pass
    if not np.any(row_ok):
        raise ValueError("model_lc: no overlapping finite q points between profile and library")
    A_u = A[row_ok]
    y_u = y[row_ok]
    sig_u = None if sigma is None else np.asarray(sigma, dtype=float)[row_ok]

    # Weighted NNLS: scale rows by 1/sigma
    if sig_u is not None and np.any(np.isfinite(sig_u) & (sig_u > 0)):
        s = np.where(np.isfinite(sig_u) & (sig_u > 0), sig_u, np.nanmedian(sig_u[sig_u > 0]))
        s = np.where(np.isfinite(s) & (s > 0), s, 1.0)
        scale = 1.0 / s
        Aw = A_u * scale[:, None]
        yw = y_u * scale
    else:
        Aw, yw = A_u, y_u

    k_max = min(int(n_components), n_lib)

    def _eval(idxs: Tuple[int, ...]) -> Tuple[np.ndarray, float]:
        sub = Aw[:, list(idxs)]
        w_sub, _ = nnls(sub, yw)
        yhat_u = A_u[:, list(idxs)] @ w_sub
        c2 = _chi2(y_u, yhat_u, sig_u, n_params=len(idxs))
        return w_sub, c2

    best_w: Optional[np.ndarray] = None
    best_chi2 = float("inf")
    best_idxs: Tuple[int, ...] = ()

    if n_lib <= k_max:
        w_all, _ = nnls(Aw, yw)
        yhat_u = A_u @ w_all
        best_chi2 = _chi2(y_u, yhat_u, sig_u, n_params=n_lib)
        best_w = w_all
        best_idxs = tuple(range(n_lib))
    else:
        for k in range(1, k_max + 1):
            for idxs in combinations(range(n_lib), k):
                w_sub, c2 = _eval(idxs)
                if c2 < best_chi2:
                    best_chi2 = c2
                    best_w = w_sub
                    best_idxs = idxs

    assert best_w is not None and best_idxs
    weights = np.zeros(n_lib, dtype=float)
    weights[list(best_idxs)] = best_w
    y_fit_full = np.full(n_q, np.nan, dtype=float)
    y_fit_full[row_ok] = A_u[:, list(best_idxs)] @ best_w
    return weights, y_fit_full, float(best_chi2), best_idxs


@apply_batch(stem_from_keys="profile", per_sample_subdir="always")
@run_with_cache(
    path_keys_for_hash=["profile", "database"],
    kwargs_for_hash_keys=["n_components", "database_kind"],
    include_config_in_hash=False,
    warn_if_no_cache=True,
)
def _model_lc_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = False,
    sample_index: int = 0,
    n_components: int = 3,
    database_kind: str = "dats",
) -> Dict[str, Union[str, List[str]]]:
    _ = config, use_cache, sample_index
    profile = input_paths.get("profile")
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    profile = os.path.normpath(os.path.abspath(os.path.expanduser(str(profile or ""))))
    if not profile or not os.path.isfile(profile):
        raise FileNotFoundError("model_lc requires input_paths['profile']")

    os.makedirs(output_dir, exist_ok=True)
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(profile))[0])
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": f"model_lc: NNLS fit for {base}…"})

    q, I, sigma = load_saxs_1d_any(profile)
    q, I, sigma = ensure_q_nm(q, I, sigma)

    db = input_paths.get("database")
    paths: List[str] = []
    csv_path: Optional[str] = None
    if database_kind == "csv":
        if isinstance(db, list):
            csv_path = str(db[0]) if db else None
        else:
            csv_path = str(db) if db else None
    else:
        if isinstance(db, list):
            paths = [str(p) for p in db]
        elif isinstance(db, str) and db:
            paths = [db]

    A, labels = _load_library_matrix(
        kind=database_kind,
        csv_path=csv_path,
        dat_paths=paths,
        q_exp=q,
    )
    weights, I_fit, chi2, idxs = fit_nnls_sparse(
        A, I, sigma=sigma, n_components=int(n_components)
    )

    chosen_labels = [labels[i] for i in idxs]
    components_used = ",".join(chosen_labels)

    fit_path = os.path.join(output_dir, f"{base}_lc_fit.dat")
    write_saxs(
        fit_path,
        q,
        I_fit,
        None,
        {
            "type": "model_lc_fit",
            "components": components_used,
            "chi2": chi2,
            "n_components": int(n_components),
        },
    )

    residual = I - I_fit
    residual_path = os.path.join(output_dir, f"{base}_lc_residual.dat")
    write_saxs(
        residual_path,
        q,
        residual,
        None,
        {"type": "model_lc_residual", "chi2": chi2},
    )

    weights_path = os.path.join(output_dir, f"{base}_lc_weights.csv")
    with open(weights_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["index", "label", "weight", "selected"])
        for i, lab in enumerate(labels):
            w.writerow([i, lab, f"{weights[i]:.8g}", "1" if i in idxs else "0"])
        w.writerow([])
        w.writerow(["chi2", f"{chi2:.8g}"])
        w.writerow(["components_used", components_used])

    plot_path = os.path.join(output_dir, f"{base}_lc_fit.png")
    try:
        import matplotlib.pyplot as plt

        finite = np.isfinite(I_fit) & np.isfinite(I) & (I > 0) & (I_fit > 0)
        fig, ax = plt.subplots()
        ax.plot(q[finite], I[finite], "k-", lw=0.8, label="experiment")
        ax.plot(q[finite], I_fit[finite], "-", lw=1.4, alpha=0.85, label="NNLS fit")
        for i in idxs:
            comp = weights[i] * A[:, i]
            ok = finite & np.isfinite(comp) & (comp > 0)
            if np.any(ok):
                ax.plot(q[ok], comp[ok], "--", lw=1.0, alpha=0.7, label=f"{labels[i]}×{weights[i]:.3g}")
        ax.set_yscale("log")
        ax.set_xlabel(r"$q$ (nm$^{-1}$)")
        ax.set_ylabel(r"$I(q)$ (a.u.)")
        ax.set_title(f"model_lc: {base} (χ²={chi2:.3g})")
        ax.legend(loc="best")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(plot_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
    except Exception as exc:
        if event_bus:
            event_bus.publish(EventType.MESSAGE, {"text": f"model_lc: plot skipped ({exc})"})
        plot_path = ""

    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {"text": f"model_lc: chose [{components_used}] χ²={chi2:.4g}"},
        )

    return {
        "fit_path": fit_path,
        "residual_path": residual_path,
        "weights_path": weights_path,
        "plot_path": plot_path,
        "components_used": components_used,
        "chi2": f"{chi2:.8g}",
        "output_subdir": output_dir,
    }
