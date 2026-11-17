---
units: nm / nm^-1
invariant: metrics-inform-never-hard-gate
source: saxs-lit-corpus + knowledge_harvest drafts
---
# Sample: Flexible / multi-domain / IDP

### FL-G1 — Narrower Guinier for elongated/flexible

- **Regime:** Extended or inhomogeneous contrast particles.
- **Observable signatures:** q·Rg upper limit can fall below 1.0–1.3 (Feigin & Svergun via Trewhella 2017; Putnam Debye approx for elongated).
- **Physical cause vs artifact twin:** Do not force q·Rg=1.3 window on rods/IDPs — residual curvature may be physics.
- **Action in autosaxs:** `fit-guinier` with narrower low-q; pair with Kratky.
- **When to proceed despite weak metrics:** Elevated ΔRg expected; proceed to fit-distances with soft D_max language.
- **Primary citation:** Trewhella 2017; Putnam 2007
- **Trust:** Tier A

### FL-K1 — Kratky: plateau / rising = disorder or elongation

- **Regime:** IDP / multi-domain with linker.
- **Observable signatures:** Dimensionless Kratky max shifts up-right; coil-like asymptote ~q^-2 (Debye) → plateau in Kratky (Trewhella 2017; Putnam 2007).
- **Physical cause vs artifact twin:** Under-subtracted background can also lift high-q Kratky — check subtract first.
- **Action in autosaxs:** `analyze-kratky` then `fit-distances`; prefer `model-lc` over single rigid `model-dam` as ground truth.
- **When to proceed despite weak metrics:** Weak TE / wide ensemble_te_span is common for soft D_max — do not hard-gate.
- **Primary citation:** Trewhella 2017; Putnam 2007; Jeffries et al. Methods Primer (flexibility caveats)
- **Trust:** Tier A

### FL-P1 — Asymmetric / extended P(r); soft D_max

- **Regime:** Extended chains; q_min·D_max may approach π.
- **Observable signatures:** Long asymmetric p(r) tail; D_max soft (Jacques 2010).
- **Physical cause vs artifact twin:** Artificial D_max≈π/q_min truncation for semi-dilute coils (see polymers-gels).
- **Action in autosaxs:** `fit-distances` + force_zero_off inspection; skip rigid DAM as “the structure”.
- **When to proceed despite weak metrics:** User may still request model-dam as effective average volume with explicit caveat.
- **Primary citation:** Jacques 2010; Trewhella 2017
- **Trust:** Tier A
