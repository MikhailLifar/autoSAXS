---
units: nm / nm^-1
invariant: metrics-inform-never-hard-gate
source: saxs-lit-corpus + knowledge_harvest drafts
---
# Sample: Core–shell micelle / liposome / LNP

### CS-1 — Internal order / Bragg vs smooth form factor

- **Regime:** LNPs / non-lamellar lipid NPs with internal lattice or multilamellar stacks.
- **Observable signatures:** Sharp Bragg peaks from internal hexagonal/cubic/lamellar order (Caselli 2024); Spinozzi 2024 reports lamellar-family peaks near **~1.43–1.60 nm^-1** (0.143–0.160 Å^-1 ×10) for stacked platelets — convert carefully per sample. Code lists peaks as `subtract` summary `iq_peaks` via `detect_iq_peaks` (D-ASX-010) — interpretation stays here.
- **Physical cause vs artifact twin:** Smooth Bessel oscillations of dilute spheres (Li 2016) are different physics — do not force sphere `fit-sizes` on Bragg-rich LNPs.
- **Action in autosaxs:** `subtract`/`plot` → read `iq_peaks` → `fit-distances` only with contrast awareness; prefer `model-density` / parametric core–shell; avoid silent homogeneous sphere PSD.
- **When to proceed despite weak metrics:** Weak Guinier Rg (even “imaginary” near contrast match in literature regimes) is informative, not a stop.
- **Primary citation:** Spinozzi et al. (2024) JCIS — corpus `24-Spinozzi-2024-JCIS-VoR.pdf` printed **p.450** / PDF p.5 (peaks ≈0.143–0.160 Å⁻¹ → **1.43–1.60 nm⁻¹**); Caselli et al. (2024); Li et al. (2016); Grant (2018)
- **Trust:** Tier B LNP papers + Tier A Chem. Rev./DENSS
- **Code fields:** `subtract` report `iq_peaks` (`q_nm`, `I`, `prominence`).

### CS-2 — Homogeneous bead models break on sign-inverting Δρ

- **Regime:** Core–shell with opposite-sign contrasts.
- **Observable signatures:** Oscillatory / signed p(r); DENSS explicitly targets non-uniform density (Grant 2018).
- **Physical cause vs artifact twin:** Twin: failed GNOM myth from bio playbooks.
- **Action in autosaxs:** `model-density` over `model-dam`.
- **When to proceed despite weak metrics:** Proceed despite TE flags when contrast inversion expected.
- **Primary citation:** Grant (2018) Nat. Methods
- **Trust:** Tier A
