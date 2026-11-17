---
units: nm / nm^-1
invariant: metrics-inform-never-hard-gate
source: saxs-lit-corpus + knowledge_harvest drafts
---
# Stage: Guinier & Kratky regimes

### GK-1 — Smile vs frown residual morphology

- **Regime:** Guinier window residuals.
- **Observable signatures:** Smile (+−+) aggregation/polydispersity; frown (−+−) repulsion/over-subtraction (Jacques 2010 Fig. 3 / journal p.649–650).
- **Physical cause vs artifact twin:** Beamstop-hooked first points ≠ bulk aggregation.
- **Action in autosaxs:** `fit-guinier`; use existing passport `classification` (`upturn` / `downturn` / …). **No** new `residual_shape` field (D-ASX-005).
- **When to proceed despite weak metrics:** Linear Guinier necessary but not sufficient (Trewhella 2017).
- **Primary citation:** Jacques & Trewhella (2010) *Protein Sci.* 19:642–657 journal **p.649–650** / corpus `5-pro0019-0642.pdf` PDF pp.8–9; Trewhella 2017
- **Trust:** Tier A
- **Code fields:** Guinier `classification` only (D-ASX-005 — not-to-do for residual_shape).

### GK-2 — Kratky / Porod–Debye folding screens

- **Regime:** After accurate subtraction.
- **Observable signatures:** Bell ≈ folded; plateau/rise ≈ flexible; Porod–Debye plateaus for discrete vs disordered (Trewhella citing Rambo & Tainer 2011 — secondary cite).
- **Physical cause vs artifact twin:** Background errors mimic flexibility.
- **Action in autosaxs:** `analyze-kratky` + plot. **No** Kratky threshold/label code change in D-ASX-009 landing.
- **When to proceed despite weak metrics:** Classification soft-routes workflow; never hard-stop.
- **Primary citation:** Trewhella 2017; Putnam 2007
- **Trust:** Tier A
