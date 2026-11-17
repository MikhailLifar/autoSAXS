---
units: nm / nm^-1
invariant: metrics-inform-never-hard-gate
source: saxs-lit-corpus + knowledge_harvest drafts
---
# Archetype: `high-aspect-platelets-rods`

| Field | Value |
|---|---|
| sample_class | Rods / platelets |
| provenance | Literature / 4TU gap |
| curve_path_hint | harvest curves under `saxsprocessing/knowledge_harvest/curves/` (lab) |
| q_units | nm^-1 (use `q_nm_*.dat` / ensure_q_nm) |

## Passport snapshot
**Pending batch autosaxs run** on harvested `q_nm` curves. Do not invent numbers. Fill `rg_nm`, `dmax_nm`, Kratky `(x_max,y_max)`, `total_estimate`, `chi2`, `shannon_s_min`, `force_zero_off_pathology` from skill YAML/txt outputs.

## Morphology & interpretation
I~q^-1 or q^-2; anisotropy check

## Recommended autosaxs sequence
`plot-2d → plot → model-bodies / rods`

## Metrics caveat
Metrics inform, never hard-gate. Weak TE/χ²/PDI may still allow modeling with explicit scientific caveat.

## Citations / trust
Putnam 2007; Li 2016; Stribeck 2007 Tier A
