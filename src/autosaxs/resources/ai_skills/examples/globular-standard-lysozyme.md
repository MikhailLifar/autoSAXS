---
units: nm / nm^-1
invariant: metrics-inform-never-hard-gate
source: saxs-lit-corpus + knowledge_harvest drafts
---
# Archetype: `globular-standard-lysozyme`

| Field | Value |
|---|---|
| sample_class | Globular Bio |
| provenance | SASBDB SASDPT4 / Trewhella 2022 consensus |
| curve_path_hint | harvest curves under `saxsprocessing/knowledge_harvest/curves/` (lab) |
| q_units | nm^-1 (use `q_nm_*.dat` / ensure_q_nm) |

## Passport snapshot
**Pending batch autosaxs run** on harvested `q_nm` curves. Do not invent numbers. Fill `rg_nm`, `dmax_nm`, Kratky `(x_max,y_max)`, `total_estimate`, `chi2`, `shannon_s_min`, `force_zero_off_pathology` from skill YAML/txt outputs.

## Morphology & interpretation
Bell Kratky; p(r)≥0; Rg~1.51 nm, D_max~4.9 nm (Å×0.1 from round-robin tables)

## Recommended autosaxs sequence
`fit-guinier → analyze-kratky → fit-distances → model-dam`

## Metrics caveat
Metrics inform, never hard-gate. Weak TE/χ²/PDI may still allow modeling with explicit scientific caveat.

## Citations / trust
Trewhella 2022 Tier A
