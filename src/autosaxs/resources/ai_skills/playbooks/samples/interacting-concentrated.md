---
units: nm / nm^-1
invariant: metrics-inform-never-hard-gate
source: saxs-lit-corpus + knowledge_harvest drafts
---
# Sample: Concentrated / interacting (P(q)·S(q))

### IC-1 — Low-q downturn: S(q)<1 vs over-subtraction

- **Regime:** Charged proteins/colloids at finite concentration.
- **Observable signatures:** Frowning Guinier; Rg & I(0) underestimate; p(r) may go negative past D_max when force-zero off (repulsion fingerprint). Correlation peak q_p=2π/d.
- **Physical cause vs artifact twin:** **Tie-break:** high-q baseline still non-negative and scales with concentration → physics S(q). High-q I<0 / log dropout → over-subtraction (Jacques 2010; Trewhella buffer notes).
- **Action in autosaxs:** Do not flatten S(q) peak by tweaking buffer scale. Dilution series; compare force_zero_off.
- **When to proceed despite weak metrics:** Modeling form factor alone is OK only with explicit S(q) caveat or after dilution.
- **Primary citation:** Jacques & Trewhella (2010); Trewhella 2017; Li 2016 (structure factor section)
- **Trust:** Tier A
