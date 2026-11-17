---
units: nm / nm^-1
invariant: metrics-inform-never-hard-gate
source: saxs-lit-corpus + knowledge_harvest drafts
---
# Playbooks INDEX

Units: **q in nm^-1**, lengths in **nm**. **Metrics inform, never hard-gate** (exception: unattended `process_monodisperse` / `process_polydisperse` keep hard quality gates by default — D-ASX-001).

Citations: author/year + corpus pin or journal page (see Project `docs/code-change-citations.md` / harvest drafts).

## Trust tiers used in rules

| Tier | Meaning |
|---|---|
| **A** | IUCr guidelines / Nat. Methods / Chem. Rev. / QRB / Protein Sci. pitfalls / established textbooks / Nat. Rev. Methods Primer |
| **B** | Solid IUCr methods/software papers + recent LNP primary/review (use for mapping, not as sole physics SSOT) |
| **C / X** | Downgraded book-review PDF; excluded non-SAXS PDF (`21-mmc2.pdf`) — **not cited as rules** |

## Sample-class playbooks

| ID | File |
|---|---|
| Globular Bio | `samples/globular-bio.md` |
| Flexible / IDP | `samples/flexible-idp.md` |
| Membrane + detergent/nanodisc | `samples/membrane-detergent-nanodisc.md` |
| Core–shell LNP / micelle | `samples/core-shell-lnp-micelle.md` |
| Inorganic NP | `samples/inorganic-np.md` |
| Rods / fibrils / platelets | `samples/rods-fibrils-platelets.md` |
| Polymers / gels | `samples/polymers-gels.md` |
| Interacting / concentrated | `samples/interacting-concentrated.md` |

## Stage playbooks

| ID | File |
|---|---|
| 2D & masking | `stages/reduction-2d-masking.md` |
| Radiation / averaging | `stages/radiation-averaging.md` |
| Subtraction | `stages/subtraction-diagnostics.md` |
| Guinier & Kratky | `stages/guinier-kratky-regimes.md` |
| IFT p(r)/D(R) | `stages/ift-pr-dr-artifacts.md` |
| Modeling selection | `stages/modeling-selection-and-ambiguity.md` |

## Visual

| ID | File |
|---|---|
| Visual diagnosis | `visual/visual-diagnosis.md` |

## Quick routing

| Clue | Sample | Stage | First skills |
|---|---|---|---|
| Compact protein/RNA | globular-bio | guinier-kratky-regimes | fit-guinier → analyze-kratky → fit-distances |
| Kratky coil / rising | flexible-idp | ift-pr-dr-artifacts | narrow Guinier → analyze-kratky → fit-distances |
| Bilayer bump ~3–6 nm^-1 / neg contrast lobe | membrane-detergent-nanodisc | ift-pr-dr-artifacts | fit-distances → model-density |
| Internal Bragg / complex LNP | core-shell-lnp-micelle | ift-pr-dr-artifacts | plot → flag peaks → model-density / parametric note |
| Bessel minima / poly NP | inorganic-np | ift-pr-dr-artifacts | fit-sizes → model-dr-mc → model-mixture |
| Log-log −1 / −2 | rods-fibrils-platelets | reduction-2d-masking | plot-2d → plot → model-bodies / rods kernel |
| Low-q downturn, high-q OK | interacting-concentrated | subtraction-diagnostics | dilution / force_zero_off vs subtract twin |
| High-q I<0 | — | subtraction-diagnostics | re-run subtract first |
| Unknown | — | guinier-kratky-regimes | plot → fit-guinier → analyze-kratky → triage |
