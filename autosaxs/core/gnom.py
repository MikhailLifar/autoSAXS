"""GNOM/DATGNOM output parsing and candidate scoring."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

def parse_gnom_out(source: Union[str, os.PathLike]) -> Dict[str, Any]:
    """
    Parse common ATSAS GNOM/DATGNOM ``.out`` content.

    ``source`` may be a filesystem path or the already-read output text. The returned
    dictionary contains:

    - ``total_estimate``: GNOM/DATGNOM Total Estimate, if present.
    - ``suspicious``: whether GNOM marked the solution as suspicious.
    - ``real_space_rmax``: parsed upper real-space range, if present.
    - ``iq_table``: ``(q, I_exp, sigma, I_fit)`` from the scattering table, if parsed.
    - ``distribution``: ``(r_or_R, values)`` from the last suitable 3-column real-space
      distribution block, if parsed. Interpret as p(r) for DATGNOM and D(R) for
      polydisperse GNOM runs.
    """
    if isinstance(source, os.PathLike) or (isinstance(source, str) and os.path.isfile(source)):
        with open(source, "r", errors="replace") as f:
            out_text = f.read()
    else:
        out_text = str(source or "")

    def parse_total_estimate(text: str) -> Optional[float]:
        patterns = [
            r"Total\s+Estimate\s*[:=]\s*([0-9]*\.?[0-9]+)",
            r"TOTAL\s+ESTIMATE\s*[:=]\s*([0-9]*\.?[0-9]+)",
            r"\bTOTAL\b\s*[:=]\s*([0-9]*\.?[0-9]+)",
        ]
        for pat in patterns:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    continue
        return None

    def parse_real_space_rmax(text: str) -> Optional[float]:
        m = re.search(r"Real\s+space\s+range:\s*[0-9]*\.?[0-9]+\s*to\s*([0-9]*\.?[0-9]+)", text or "")
        if not m:
            return None
        try:
            return float(m.group(1))
        except ValueError:
            return None

    def numeric_blocks(
        lines: List[str],
        *,
        min_cols: int = 3,
        reject_numeric_fourth_col: bool = False,
    ) -> List[List[List[float]]]:
        blocks: List[List[List[float]]] = []
        cur: List[List[float]] = []
        for ln in lines:
            st = ln.strip()
            if not st:
                if cur:
                    blocks.append(cur)
                    cur = []
                continue
            parts = re.split(r"[,\s]+", st)
            if len(parts) < min_cols:
                if cur:
                    blocks.append(cur)
                    cur = []
                continue
            if reject_numeric_fourth_col and len(parts) > 3:
                try:
                    _ = float(parts[3])
                    if cur:
                        blocks.append(cur)
                        cur = []
                    continue
                except ValueError:
                    pass
            try:
                vals = [float(x) for x in (parts[:3] if reject_numeric_fourth_col else parts)]
            except ValueError:
                if cur:
                    blocks.append(cur)
                    cur = []
                continue
            cur.append(vals)
        if cur:
            blocks.append(cur)
        return blocks

    def parse_iq_table(text: str) -> Optional[Tuple[np.ndarray, np.ndarray, Optional[np.ndarray], np.ndarray]]:
        lines = (text or "").splitlines()
        header_idx: Optional[int] = None
        for i, ln in enumerate(lines):
            s = ln.strip().upper()
            if ("EXP" in s or "EXPER" in s) and ("ERROR" in s or "ERR" in s) and ("S" in s or "Q" in s):
                header_idx = i
                break
        start = header_idx + 1 if header_idx is not None else 0

        rows: List[List[float]] = []
        for ln in lines[start:]:
            st = ln.strip()
            if not st:
                if rows:
                    break
                continue
            parts = re.split(r"[,\s]+", st)
            if len(parts) < 3:
                if rows:
                    break
                continue
            try:
                vals = [float(x) for x in parts]
            except ValueError:
                if rows:
                    break
                continue
            if not np.isfinite(vals[0]) or vals[0] <= 0:
                if rows:
                    break
                continue
            rows.append(vals)

        if len(rows) < 8:
            rows = []
            for blk in reversed(numeric_blocks(lines, min_cols=3)):
                if len(blk) >= 8 and len(blk[0]) >= 3:
                    rows = blk
                    break
        if len(rows) < 8:
            return None

        ncol = max(len(r) for r in rows)
        arr = np.full((len(rows), ncol), np.nan, dtype=float)
        for i, row in enumerate(rows):
            arr[i, : len(row)] = row
        q = arr[:, 0]
        I_exp = arr[:, 1]
        sigma = arr[:, 2] if ncol >= 3 else None

        I_fit: Optional[np.ndarray]
        if ncol >= 5:
            I_fit = arr[:, 4]
        elif ncol >= 4:
            I_fit = arr[:, 3]
        else:
            I_fit = None
        if I_fit is None or not np.any(np.isfinite(I_fit)):
            for j in range(ncol - 1, 1, -1):
                cand = arr[:, j]
                if np.any(np.isfinite(cand)):
                    I_fit = cand
                    break
        if I_fit is None:
            return None
        return (
            q.astype(float),
            I_exp.astype(float),
            sigma.astype(float) if sigma is not None else None,
            I_fit.astype(float),
        )

    def parse_distribution(text: str) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        lines = (text or "").splitlines()
        for blk in reversed(numeric_blocks(lines, min_cols=3, reject_numeric_fourth_col=True)):
            if len(blk) < 8:
                continue
            r = np.asarray([x[0] for x in blk], dtype=float)
            values = np.asarray([x[1] for x in blk], dtype=float)
            if np.all(np.diff(r) >= 0):
                return r, values
        return None

    return {
        "total_estimate": parse_total_estimate(out_text),
        "suspicious": bool(re.search(r"SUSPICIOUS", out_text or "", flags=re.IGNORECASE)),
        "real_space_rmax": parse_real_space_rmax(out_text),
        "iq_table": parse_iq_table(out_text),
        "distribution": parse_distribution(out_text),
    }

def candidate_score(cand: Dict[str, Any]) -> float:
    """score = Total Estimate − neg_frac (higher is better)."""
    te = cand.get("total_estimate")
    try:
        te_v = float(te) if te is not None else float("-inf")
    except (TypeError, ValueError):
        te_v = float("-inf")
    nf = cand.get("neg_frac")
    try:
        nf_v = float(nf) if nf is not None else 0.0
    except (TypeError, ValueError):
        nf_v = 0.0
    if not np.isfinite(te_v):
        return float("-inf")
    return float(te_v - nf_v)
