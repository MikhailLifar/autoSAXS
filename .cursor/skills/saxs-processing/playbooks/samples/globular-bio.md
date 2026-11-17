---
units: nm / nm^-1
invariant: metrics-inform-never-hard-gate
source: saxs-lit-corpus + knowledge_harvest drafts
---
# Sample: Globular bio (proteins / folded nucleic acids)

### GB-G1 — Guinier q·Rg cap for compact particles

- **Regime:** Dilute monodisperse globular; valid low-q window in nm^-1 (dimensionless product).
- **Observable signatures:** Linear ln I vs q²; passport `q_max * rg_nm` ≤ ~1.3 for uniform spheres; Jacques/Putnam also cite ~0.8 for elongated.
- **Physical cause vs artifact twin:** Upturn (smile) = aggregation/polydispersity; downturn (frown) = repulsion or over-subtraction — not a Guinier limit issue.
- **Action in autosaxs:** `fit-guinier` classical window; report q·Rg; do not silently truncate low-q without caveat (Trewhella 2017).
- **When to proceed despite weak metrics:** If only q·Rg slightly >1.3 but P(r) and Kratky look compact, proceed with caveat; never hard-stop modeling on TE alone.
- **Primary citation:** Trewhella et al. (2017) Acta Cryst. D 73:710; Putnam et al. (2007) QRB 40:191; Jacques & Trewhella (2010) Protein Sci. 19:642
- **Trust:** Tier A — IUCr guidelines + QRB + Protein Sci. pitfalls

### GB-K1 — Dimensionless Kratky bell for folded domains

- **Regime:** Folded domains; plot (q Rg)² I(q)/I(0) vs q Rg.
- **Observable signatures:** Bell with max ≈1.1 near q·Rg ≈ 1.75 (Trewhella 2017).
- **Physical cause vs artifact twin:** Rising/plateau = flexibility/IDP (see flexible-idp); rising from under-subtraction twin (check high-q baseline).
- **Action in autosaxs:** `analyze-kratky`; keep classification as soft label.
- **When to proceed despite weak metrics:** Borderline y_max still allows fit-distances / model-dam with flexibility caveat.
- **Primary citation:** Trewhella et al. (2017) Acta Cryst. D 73:710 (dimensionless Kratky guidance)
- **Trust:** Tier A

### GB-P1 — P(r) positive bell; Guinier↔P(r) Rg agreement

- **Regime:** Uniform-contrast compact particle; q_min ≲ π/D_max.
- **Observable signatures:** Smooth p(r)≥0 →0 at D_max; ΔRg (Guinier vs p(r)) small; Trewhella 2022 consensus lysozyme Rg~1.51 nm, D_max~4.8–5.0 nm (from Å tables ×0.1).
- **Physical cause vs artifact twin:** Cliff at D_max = D_max too small; long oscillating toe = D_max too large or aggregation (Jacques 2010).
- **Action in autosaxs:** `fit-distances`; inspect ensemble + force_zero_off; compare Rg.
- **When to proceed despite weak metrics:** TE in “acceptable/failed” on noisy dilute data can still justify envelope if user wants — report Shannon/ambiguity risk.
- **Primary citation:** Trewhella 2017; Trewhella 2022 round-robin; Jacques 2010; Svergun 1992 TE via Trewhella citing GNOM
- **Trust:** Tier A (+ B for ATSAS TE mechanics)

### GB-M1 — Ab initio after quality triage

- **Regime:** Dilute, monodisperse, matched buffer.
- **Observable signatures:** Good Guinier+P(r); CorMap OK vs model; AMBIMETER uniqueness when available.
- **Physical cause vs artifact twin:** Mixtures/flexibility → bead model over-interpretation (Jacques 2010).
- **Action in autosaxs:** `model-dam` (DAMMIF) and/or `calc-profile`; report SASRES if ensemble (Tuukkanen 2016).
- **When to proceed despite weak metrics:** Proceed with ambiguity caveat if AMBIMETER high; metrics inform.
- **Primary citation:** Franke & Svergun (2009) DAMMIF; Franke et al. (2017) ATSAS 2.8; Tuukkanen et al. (2016) SASRES
- **Trust:** Tier B methods / A for reporting culture
