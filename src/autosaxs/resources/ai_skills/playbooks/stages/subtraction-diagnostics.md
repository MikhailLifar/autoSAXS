---
units: nm / nm^-1
invariant: metrics-inform-never-hard-gate
source: saxs-lit-corpus + knowledge_harvest drafts
---
# Stage: Buffer subtraction diagnostics

### SB-1 — Over-subtraction

- **Regime:** Blank scale too high / mismatched buffer.
- **Observable signatures:** High-q `I+σ<0`; passport `frac_neg` / `q_first_neg_nm` on `subtract` (D-ASX-003); log-log dropout. Trewhella 2017 warns large solvent corrections (≳1% solvent level) as red flag.
- **Physical cause vs artifact twin:** Twin of repulsive low-q downturn (IC-1) — oversubtract hits high-q first; do **not** flatten a low-q Guinier downturn by rescaling buffer alone (Jacques 2010 p.649).
- **Action in autosaxs:** Re-run `subtract`; inspect `frac_neg` / `q_first_neg_nm` before Guinier/GNOM. Confirm with `fit_distances`/`fit_sizes` `p0_classification=over_subtracted` when force-zero at r=0 is released (D-ASX-004).
- **When to proceed despite weak metrics:** Do not model oversubtracted curves; fix upstream.
- **Primary citation:** Jacques & Trewhella (2010) *Protein Sci.* 19:642–657 journal **p.649** / corpus `5-pro0019-0642.pdf` PDF p.8 (“If P(0) is positive or negative, then the solvent has been under- or oversubtracted, respectively”); Trewhella 2017; Trewhella 2022 (buffer background variability)
- **Trust:** Tier A
- **Code fields:** `subtract.correctness`, `subtract.frac_neg`, `subtract.q_first_neg_nm`; fit passport `p0`, `p0_classification`.

### SB-2 — Under-subtraction

- **Regime:** Blank scale too low.
- **Observable signatures:** Flat high-q plateau / elevated baseline; Kratky artificially rising; **P(0)>0** when force-zero at r=0 released (`p0_classification=under_subtracted` on fit passports — D-ASX-004). Not a full baseline suite on `subtract` (D-ASX-003).
- **Physical cause vs artifact twin:** Twin of true coil scattering — check absolute buffer match / transmission.
- **Action in autosaxs:** Adjust subtract; compare concentration series; read fit passport P(0).
- **When to proceed despite weak metrics:** If residual constant background small, Guinier may still be usable with caveat.
- **Primary citation:** Jacques & Trewhella (2010) journal **p.649** / `5-pro0019-0642.pdf` PDF p.8; Trewhella 2017
- **Trust:** Tier A
- **Code fields:** `fit_distances` / `fit_sizes` quality.yml `p0`, `p0_classification`.
