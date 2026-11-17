---
units: nm / nm^-1
invariant: metrics-inform-never-hard-gate
source: saxs-lit-corpus + knowledge_harvest drafts
---
# Visual diagnosis protocol (Pillar #2)

**Capability gate:** If harness cannot read PNGs, skip entirely (no error). Numbers from passports own numbers; vision owns morphology only.

## Pair-check table

| PNG | Vision checks | Numerical cross-check | Grounding |
|---|---|---|---|
| 2D (`plot-2d`) | Beamstop/flare, hot pixels, anisotropy | mask shape, calibrate residuals | Jeffries Primer; Li 2016 |
| I(q) / log-log | High-q dropout (oversub), flat plateau (undersub), Bragg spikes vs smooth Bessel | subtract correctness; q-range | Trewhella 2017; Jacques 2010; Li 2016 |
| Guinier + residuals | Smile / frown / hooked first points | rg_nm, q·Rg, residual metrics | Jacques 2010 Fig. 3; Trewhella 2017 |
| Kratky | Bell vs plateau vs rise | x_max, y_max, classification | Trewhella 2017; Putnam 2007 |
| p(r) ensemble | Cliff / toe / signed contrast lobe / wiggles | dmax, TE, wiggle_index, force_zero_off | Jacques 2010; Trewhella 2017; Graziano 2018; Grant 2018 |
| D(R) / McSAS | Boundary pile-up; stable vs artifactual modes | pdi, stability_class, McSAS uncertainties | Pauw 2013; Bressler 2015 |

**Trust:** Tier A for bio visual pitfall language; Tier B for McSAS/nanodisc specifics.
