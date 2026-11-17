---
units: nm / nm^-1
invariant: metrics-inform-never-hard-gate
source: saxs-lit-corpus + knowledge_harvest drafts
---
# Sample: Rods, fibrils, nanoplatelets

### RD-1 — Power-law slopes −1 / −2 + anisotropy gate

- **Regime:** High-aspect particles; intermediate-q.
- **Observable signatures:** Log-log I~q^-1 (rods) or q^-2 (platelets/discs) (Putnam/Li/Stribeck soft-matter framing); 2D anisotropy if flow-aligned invalidates isotropic 1D.
- **Physical cause vs artifact twin:** Sphere PSD hallucinates multimodal populations from shape oscillations (design trap; Li form-factor diversity).
- **Action in autosaxs:** `plot-2d` first → `plot` power-law → `fit-guinier` with q·Rg≲0.8 → `fit-sizes --shape rods` or `model-bodies`; block silent spheres.
- **When to proceed despite weak metrics:** Weak Guinier coverage common — still report cross-section/thickness language.
- **Primary citation:** Putnam 2007; Li 2016; Stribeck 2007; Jeffries Primer
- **Trust:** Tier A
