---
units: nm / nm^-1
invariant: metrics-inform-never-hard-gate
source: saxs-lit-corpus + knowledge_harvest drafts
---
# Stage: Radiation damage & averaging

### RA-1 — CorMap frame selection; damage raises Rg/I(0)

- **Regime:** Multi-frame exposures; bio samples.
- **Observable signatures:** Radiation-induced aggregation → rising Rg/I(0) over frames (Jacques 2010); CorMap compares frames without relying on error bars (Franke 2015 Nat. Methods).
- **Physical cause vs artifact twin:** Steady repulsive S(q) is not time-evolving like damage.
- **Action in autosaxs:** `average` (CorMap); report rejected frames; Jeffries Primer radiation caveats.
- **When to proceed despite weak metrics:** Sparse frames after rejection may worsen TE — still prefer undamaged average.
- **Primary citation:** Franke, Jeffries & Svergun (2015) Nat. Methods; Jacques 2010; Jeffries Primer
- **Trust:** Tier A
