---
units: nm / nm^-1
invariant: metrics-inform-never-hard-gate
source: saxs-lit-corpus + knowledge_harvest drafts
---
# Stage: 2D reduction & masking

### R2-1 — Beamstop / mask / anisotropy spoil low-q

- **Regime:** calibrate → integrate; isotropic solution assumption.
- **Observable signatures:** Beamstop shadow, parasitic flare, module gaps; elliptical rings if aligned rods/lamellae (Jeffries Primer; Li GISAXS/anisotropy awareness).
- **Physical cause vs artifact twin:** Real anisotropy vs bad mask — check 2D before trusting 1D Guinier.
- **Action in autosaxs:** `plot-2d` + mask QA (`effective_mask.npy`); fix mask/geometry before Guinier.
- **When to proceed despite weak metrics:** If only mid/high-q usable, proceed with Shannon caveat on s_min.
- **Primary citation:** Jeffries et al. Methods Primer; Li 2016; Hopkins 2024 (pyFAI reduction context)
- **Trust:** Tier A/B
