---
units: nm / nm^-1
invariant: metrics-inform-never-hard-gate
source: saxs-lit-corpus + knowledge_harvest drafts
---
# Archetype: `membrane-protein-nanodisc`

| Field | Value |
|---|---|
| sample_class | Membrane + nanodisc |
| provenance | Graziano 2018 nanodisc SAXS/WAXS |
| curve_path_hint | harvest curves under `saxsprocessing/knowledge_harvest/curves/` (lab) |
| q_units | nm^-1 (use `q_nm_*.dat` / ensure_q_nm) |

## Passport snapshot
**Pending batch autosaxs run** on harvested `q_nm` curves. Do not invent numbers. Fill `rg_nm`, `dmax_nm`, Kratky `(x_max,y_max)`, `total_estimate`, `chi2`, `shannon_s_min`, `force_zero_off_pathology` from skill YAML/txt outputs.

## Morphology & interpretation
Bilayer features q~3–6 nm^-1; packing ~15 nm^-1

## Recommended autosaxs sequence
`fit-guinier → fit-distances → model-density`

## Metrics caveat
Metrics inform, never hard-gate. Weak TE/χ²/PDI may still allow modeling with explicit scientific caveat.

## Citations / trust
Graziano 2018 Tier B; Grant 2018 Tier A
