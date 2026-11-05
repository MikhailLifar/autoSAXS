"""Regression: fit_distances default auto path must match remote-main ATSAS quality.

Liveview never passes ``full_grid`` / ``fast`` flags (see
``fit_distances_opts``), so the skill default *is* the liveview GNOM path.

Invariants locked here:

1. Default auto search is the ATSAS Shannon×α grid (``full_grid=True``);
   Fast AutoGNOM only via explicit ``full_grid=False``.
2. Liveview option builder does not override the search engine.
3. When ATSAS + a known fixture are available, Stage‑2 I(q) must not collapse
   (H32 Stage‑1 failure mode: I(0)→~1, Ifit/Iexp→~0, χ²~600).
"""

from __future__ import annotations

import inspect
import os
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from autosaxs.core.gnom import parse_gnom_out
from autosaxs.skill.fit_distances import fit_distances


# Optional hard fixture used in the A/B that exposed H32 I(0) collapse.
_STORE_SASDA54 = Path(
    "/cursor/stores/bc-2b169515-f772-4921-ac0c-85787ab57ccb/"
    "internal/gnom-reverse-engineer/data/SASDA54.dat"
)


def test_fit_distances_default_auto_path_is_atsas_shannon_grid() -> None:
    """Skill default must be ATSAS grid; Fast AutoGNOM only via explicit opt-in."""
    sig = inspect.signature(fit_distances)
    params = sig.parameters

    assert "full_grid" in params, "restore tip must expose full_grid (ATSAS grid vs Fast)"
    assert params["full_grid"].default is True, (
        "full_grid must default True so liveview (no flags) uses ATSAS Shannon×α grid"
    )
    for fast_name in ("fast", "use_fast_autognom", "fast_autognom"):
        if fast_name in params:
            assert params[fast_name].default is False, (
                f"{fast_name} must default False (explicit opt-in only)"
            )


def test_default_search_routes_to_atsas_grid_when_fast_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default `_search_shannon_alpha_pr` must call the ATSAS grid; Fast only if opted in."""
    import autosaxs.skill.fit_distances.optimize as opt

    if not hasattr(opt, "_search_shannon_alpha_pr_fast"):
        pytest.skip("Fast AutoGNOM not in tree (main ATSAS-only search) — OK")
    if not hasattr(opt, "_search_shannon_alpha_pr_full_grid"):
        pytest.skip("full_grid helper missing alongside fast path")

    calls = {"fast": 0, "full": 0}

    def _fake_fast(**kwargs: Any):
        calls["fast"] += 1
        best = {
            "dmax_nm": 7.0,
            "rmax_nm": 7.0,
            "alpha": 2.0,
            "score": -0.1,
            "ok": True,
            "search_workdir": None,
        }
        return best, [best], [], {"engine": "fast"}

    def _fake_full(**kwargs: Any):
        calls["full"] += 1
        best = {
            "dmax_nm": 7.0,
            "rmax_nm": 7.0,
            "alpha": 2.0,
            "score": -0.1,
            "ok": True,
            "search_workdir": None,
        }
        return best, [best], [], {"engine": "full"}

    monkeypatch.setattr(opt, "_search_shannon_alpha_pr_fast", _fake_fast)
    monkeypatch.setattr(opt, "_search_shannon_alpha_pr_full_grid", _fake_full)

    q = np.linspace(0.1, 2.0, 40)
    I = np.exp(-(q**2))
    common = dict(
        atsas_dat_path="/dev/null",
        output_dir="/tmp",
        q_nm=q,
        I=I,
        sigma=0.05 * I,
        rg_guinier_nm=1.5,
        first=1,
        last=None,
    )
    sig = inspect.signature(opt._search_shannon_alpha_pr)
    kwargs = {k: v for k, v in common.items() if k in sig.parameters}

    # Default call (omit full_grid) — must hit ATSAS grid.
    opt._search_shannon_alpha_pr(**kwargs)
    assert calls["full"] == 1, "default auto search must use ATSAS Shannon×α grid"
    assert calls["fast"] == 0, "Fast AutoGNOM must not run unless explicitly opted in"

    # Explicit opt-in.
    opt._search_shannon_alpha_pr(**kwargs, full_grid=False)
    assert calls["fast"] == 1
    assert calls["full"] == 1


def test_liveview_fit_distances_opts_do_not_override_search_engine() -> None:
    """Liveview must leave search-engine selection to the skill default.

    Import-free check: ``fit_distances_opts`` must not mention Fast AutoGNOM /
    ``full_grid`` knobs (liveview never passes them; skill default wins).
    """
    root = Path(__file__).resolve().parents[3]
    src_path = (
        root
        / "src"
        / "guisaxs_skills"
        / "liveview"
        / "pipeline"
        / "monodisperse_pipeline.py"
    )
    text = src_path.read_text(encoding="utf-8")
    for banned in ("full_grid", "use_fast_autognom", "fast_autognom"):
        assert banned not in text, (
            f"liveview monodisperse_pipeline must not reference {banned!r}; "
            "skill default owns the auto GNOM path"
        )


def _iq_scale_from_gnom_out(text: str) -> tuple[float | None, float | None, float | None]:
    """Return (I_exp_max, I_REG_max, ratio) from GNOM scattering table."""
    rows: list[list[float]] = []
    in_fit = False
    for line in text.splitlines():
        if "S" in line and "J EXP" in line and "I REG" in line:
            in_fit = True
            continue
        if not in_fit:
            continue
        parts = line.split()
        if len(parts) >= 5:
            try:
                # S, J EXP, ERROR, J REG, I REG — use I REG (same as parse_iq_table).
                rows.append([float(parts[0]), float(parts[1]), float(parts[4])])
            except ValueError:
                if rows:
                    break
        elif rows:
            break
    if not rows:
        return None, None, None
    arr = np.asarray(rows, dtype=float)
    # Skip S=0 sentinel rows if present.
    arr = arr[arr[:, 0] > 0]
    if arr.size == 0:
        return None, None, None
    i_exp = float(np.nanmax(np.abs(arr[:, 1])))
    i_fit = float(np.nanmax(np.abs(arr[:, 2])))
    ratio = (i_fit / i_exp) if i_exp > 0 else None
    return i_exp, i_fit, ratio


@pytest.mark.skipif(shutil.which("gnom") is None, reason="ATSAS gnom not on PATH")
def test_sasda54_i0_not_collapsed_vs_main_quality(tmp_path: Path) -> None:
    """Real ATSAS lock: reject H32-style I(0)/I(q) collapse on SASDA54.

    origin/main @ e3d6f46 on this fixture (first=1, minimal): I(0)≈227,
    Ifit/Iexp≈0.95. H32 Stage‑1 tip collapsed to I(0)≈0.97, Ifit/Iexp≈0.004,
    χ²≈618 — the liveview GNOM destroyer mode.
    """
    fixture = Path(
        os.environ.get("AUTOSAXS_GNOM_REGRESSION_DAT", str(_STORE_SASDA54))
    )
    if not fixture.is_file():
        pytest.skip(f"regression fixture missing: {fixture}")

    out_dir = tmp_path / "fit_distances_sasda54"
    outs = fit_distances(
        str(fixture),
        output_dir=str(out_dir),
        first=1,
        minimal=True,
        use_cache=False,
        # Default full_grid=True on restore tip; do not pass Fast.
    )
    assert isinstance(outs, dict)
    gnom_out = outs.get("best_gnom_out_path") or outs.get("gnom_out_paths")
    if isinstance(gnom_out, list):
        gnom_out = gnom_out[0]
    assert gnom_out and Path(gnom_out).is_file()

    text = Path(gnom_out).read_text(errors="replace")
    parsed = parse_gnom_out(text)
    i0 = parsed.get("real_space_i0")
    assert i0 is not None and float(i0) > 50.0, (
        f"I(0) collapsed ({i0!r}); expected main-quality scale (~227 on SASDA54)"
    )

    _i_exp, _i_fit, ratio = _iq_scale_from_gnom_out(text)
    assert ratio is not None and ratio > 0.2, (
        f"GNOM I(q) scale collapsed (Ifit/Iexp={ratio!r}); main≈0.95, H32≈0.004"
    )

    qfiles = list(Path(outs.get("output_subdir", out_dir)).rglob("*_fit_distances_quality.yml"))
    if not qfiles:
        qfiles = list(out_dir.rglob("*_fit_distances_quality.yml"))
    assert qfiles, "missing fit_distances quality passport"
    quality = yaml.safe_load(qfiles[0].read_text())
    # Do not lock absolute χ² on SASDA54+first=1 (main itself is ~4e3 here);
    # only reject the collapsed-I(0) regime that produced χ²~600 with tiny I(q).
    assert float(quality.get("i0_pr") or i0) > 50.0
