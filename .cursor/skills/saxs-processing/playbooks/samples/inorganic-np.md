---
units: nm / nm^-1
invariant: metrics-inform-never-hard-gate
source: saxs-lit-corpus + knowledge_harvest drafts
---
# Sample: Inorganic nanoparticles (spheres)

### NP-M1 — Monodisperse spheres: form-factor oscillations

- **Regime:** Dilute spherical NPs; low polydispersity.
- **Observable signatures:** Clear minima; Li 2016 shows oscillations smear strongly by **~≥15%** size polydispersity; Rg comparable across shapes at fixed Rg (their Fig. 5 example uses Rg=10 nm). Expect high-q `powerlaw_exponent` near **−4** for compact solid spheres (D-ASX-007); −1/−2 warn against silent sphere kernel.
- **Physical cause vs artifact twin:** Structure-factor peaks (concentrated) twin form-factor minima — dilute or model S(q); `iq_peaks` on subtract may flag correlation peaks.
- **Action in autosaxs:** `fit-guinier` (rough) → `fit-sizes` (spheres; read `powerlaw_exponent`) → optional `model-mixture`.
- **When to proceed despite weak metrics:** Curved Guinier alone does not forbid PSD fitting (existing Guinier `classification` upturn/downturn is enough — D-ASX-005; no residual_shape work).
- **Primary citation:** Li, Senesi & Lee (2016) Chem. Rev. 116:11128 — corpus `7-1391950.pdf` letter G–H
- **Trust:** Tier A
- **Code fields:** `fit_sizes.powerlaw_exponent`; Guinier `classification` unchanged.

### NP-P1 — Polydisperse / multimodal: McSAS vs GNOM D(R)

- **Regime:** Continuous size polydispersity; non-interacting.
- **Observable signatures:** Smeared oscillations; Pauw 2013 observability / r_res≈π/q_max; Bressler 2015 McSAS form-free distributions with uncertainties.
- **Physical cause vs artifact twin:** Regularization may merge/split modes — cross-check McSAS error bars.
- **Action in autosaxs:** `fit-sizes` → `model-dr-mc` → `model-mixture` (1–3 modes).
- **When to proceed despite weak metrics:** Marginal stability_class still allows parametric mixture if user wants modes — advisory only.
- **Primary citation:** Pauw et al. (2013) JAC; Bressler, Pauw & Thünemann (2015) JAC; Li 2016
- **Trust:** Tier B methods + Tier A Chem. Rev.
