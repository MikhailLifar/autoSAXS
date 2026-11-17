---
units: nm / nm^-1
invariant: metrics-inform-never-hard-gate
source: saxs-lit-corpus + knowledge_harvest drafts
---
# Archetype: `elongated-coiled-coil`

| Field | Value |
|---|---|
| sample_class | Globular / rod-like bio |
| provenance | SASBDB protein elongated (D_max/Rg high) |
| curve_path_hint | harvest curves under `saxsprocessing/knowledge_harvest/curves/` (lab) |
| q_units | nm^-1 (use `q_nm_*.dat` / ensure_q_nm) |

## Passport snapshot
**Pending batch autosaxs run** on harvested `q_nm` curves. Do not invent numbers. Fill `rg_nm`, `dmax_nm`, Kratky `(x_max,y_max)`, `total_estimate`, `chi2`, `shannon_s_min`, `force_zero_off_pathology` from skill YAML/txt outputs.

## Morphology & interpretation
Narrow Guinier q·Rg<0.8–1.0; asymmetric p(r)

## Recommended autosaxs sequence
`fit-guinier → analyze-kratky → fit-distances → model-dam`

## Metrics caveat
Metrics inform, never hard-gate. Weak TE/χ²/PDI may still allow modeling with explicit scientific caveat.

## Citations / trust
Trewhella 2017; Putnam 2007 Tier A
