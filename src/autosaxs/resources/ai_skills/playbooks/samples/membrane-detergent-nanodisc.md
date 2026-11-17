---
units: nm / nm^-1
invariant: metrics-inform-never-hard-gate
source: saxs-lit-corpus + knowledge_harvest drafts
---
# Sample: Membrane protein + detergent / nanodisc

### MD-C1 — Negative contrast / bilayer features are physics

- **Regime:** Nanodisc or detergent belt with hydrocarbon Δρ sign change.
- **Observable signatures:** I(q) features correlating to bilayer often near **q ≈ 3–6 nm^-1** (paper: 0.3–0.6 Å^-1 ×10); lipid packing peak near **~15 nm^-1** (1.5 Å^-1 ×10) in WAXS (Graziano 2018). Negative lobes in p(r) possible from contrast cross-terms.
- **Physical cause vs artifact twin:** Do **not** treat negative p(r) as automatic GNOM failure (override naive bio rule). Twin: noisy oscillation from over-regularization.
- **Action in autosaxs:** `fit-distances` then prefer `model-density` (DENSS; Grant 2018) over uniform-contrast `model-dam`. ATSAS DAMEMB for membrane search volume (Manalastas-Cantos 2021) when using bead multiphase.
- **When to proceed despite weak metrics:** TE/χ² may look “weak” while morphology is physical — proceed with density modeling + caveat.
- **Primary citation:** Graziano, Miller & Yang (2018) J. Appl. Cryst.; Grant (2018) Nat. Methods; Manalastas-Cantos et al. (2021) JAC
- **Trust:** Tier B (nanodisc JAC) + Tier A (DENSS Nat. Methods)

### MD-S1 — Free micelle / blank mismatch trap

- **Regime:** Detergent present in sample ≠ blank.
- **Observable signatures:** Dip+hump morphology distorted; high-q baseline wrong.
- **Physical cause vs artifact twin:** Twin of core–shell physics vs subtraction error — verify blank recipe / dialysis / SEC match (Jacques 2010 buffer discipline).
- **Action in autosaxs:** Re-run `subtract` with matched blank before modeling.
- **When to proceed despite weak metrics:** If user insists on modeling mismatched blank, label result non-quantitative.
- **Primary citation:** Jacques & Trewhella (2010); Jeffries Primer (sample/buffer matching)
- **Trust:** Tier A
