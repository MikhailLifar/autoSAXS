---
units: nm / nm^-1
invariant: metrics-inform-never-hard-gate
source: saxs-lit-corpus + knowledge_harvest drafts
---
# Stage: IFT p(r) / D(R) artifacts

### IFT-1 — D_max selection & TE are advisory

- **Regime:** Indirect FT (GNOM family).
- **Observable signatures:** D_max soft; TE = χ² + perceptual criteria (Svergun 1992 via Trewhella 2017); cliff vs toe (Jacques 2010).
- **Physical cause vs artifact twin:** Automated D_max can be unphysical (Trewhella 2017).
- **Action in autosaxs:** `fit-distances` / `fit-sizes`; inspect ensemble, wiggle_index, force_zero_off.
- **When to proceed despite weak metrics:** **Never hard-gate modeling solely on TE** — explain risk and proceed if user chooses.
- **Primary citation:** Trewhella 2017; Jacques 2010
- **Trust:** Tier A

### IFT-2 — Sphere kernel trap for non-spheres

- **Regime:** fit-sizes system=spheres.
- **Observable signatures:** High-aspect or core–shell data → multimodal sphere hallucination (Li 2016 form-factor diversity; McSAS needs correct shape library — Bressler 2015). Passport `powerlaw_exponent` near **−1 / −2** (rods / disks) vs **−4** (solid Porod) — D-ASX-007.
- **Physical cause vs artifact twin:** True multimodal spheres (bimodal SiO2) vs shape misfit — confirm with power-law/2D.
- **Action in autosaxs:** Read `fit_sizes` `powerlaw_exponent`; warn + route to rods/bodies/density; McSAS cross-check. Never hard-skip solely on exponent.
- **When to proceed despite weak metrics:** User may still run spheres for comparison with explicit caveat.
- **Primary citation:** Li, Senesi & Lee (2016) *Chem. Rev.* 116:11128 — corpus `7-1391950.pdf` letter **G–H** (PDF pp.7–8): Porod exponents −1 / −2 / −4; ≥15% polydispersity smears sphere oscillations.
- **Trust:** Tier A/B
- **Code fields:** `fit_sizes` quality `powerlaw_exponent`.
