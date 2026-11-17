---
name: saxs-processing
description: Orchestrate SAXS data processing end-to-end — clarify goals, plan single- or multi-step workflows, and delegate execution to nested subskills. Use when the user works with small-angle X-ray scattering (2D frames, 1D curves, calibration, integrate, subtraction, analysis, plots, or fits).
---

# SAXS processing (orchestrator)

Top-level router for the SAXS skill bundle. **Depth lives in playbooks and examples** — open those on demand; do not invent regime rules from memory when a linked playbook exists.

Subskills: `saxs-processing/<name>/<name>.md`. Only this file is a Cursor Agent Skill.

## When to use

- Process, interpret, or fit SAXS data (2D frames or 1D curves).
- Single step or a sequence; user may not say `autosaxs` — treat it as the default CLI for these leaves.

## Units and metrics

- **Units:** `q` in `nm^-1`; lengths (`R_g`, `D_max`, `R`, …) in `nm`.
- **Metrics inform, never hard-gate** for interactive / agent-orchestrated single skills. Explain risk and caveats; proceed if the user chooses.
- **Exception (D-ASX-001):** unattended `process-monodisperse` / `process-polydisperse` keep **hard quality gates** by default. Optional `gate_pass` (when implemented) bypasses gates while still recording the same checks as advice.

## Sample-context intake (optional, non-blocking)

Before locking a multi-step plan (skip if the user already stated the sample or asked for one mechanical command), ask this compact intake — or proceed immediately as **unknown**:

1. **Sample family:** protein / nucleic acid / membrane-protein + detergent or nanodisc / LNP or micelle / inorganic nanoparticle / polymer or gel / unknown.
2. **Dispersity & morphology:** monodisperse single species / discrete oligomer or conformer mixture / continuous size-polydisperse spheres / high-aspect rods, fibrils, or platelets / unknown.
3. **Solvent & contrast:** matched dialysis/SEC buffer / high salt–glycerol–sucrose–ethanol / multiphase core–shell (possible negative contrast) / dry or unsubtracted.
4. **Series variable (if many files):** concentration / SEC frames / T–pH–time–ligand / independent samples.

If the user skips or is unsure → **`unknown — standard diagnostic triage`**: `plot` → `fit-guinier` → `analyze-kratky` → inspect log-log slope / oscillations → branch via `playbooks/INDEX.md`.

When the user gives a sample label (e.g. `Pt-NPs`, `Lysozyme`), pass that **exact** label into CLI/metadata so local processing history stays clean.

## Playbooks and examples (progressive disclosure)

| Open | When |
|---|---|
| [`playbooks/INDEX.md`](playbooks/INDEX.md) | Route sample class × stage; load **at most one** sample + one stage playbook per run |
| [`examples/INDEX.md`](examples/INDEX.md) | Match a worked archetype card (≤1–2 cards) |
| [`playbooks/visual/visual-diagnosis.md`](playbooks/visual/visual-diagnosis.md) | Vision-capable harness only (see below) |

Every playbook rule carries an inline literature citation (author/year + corpus pin or journal page). Prefer those cites over improvisation.

## Visual diagnosis (capability gate)

- If the harness **can** read PNGs: after numerical passports exist, optionally open `playbooks/visual/visual-diagnosis.md`. **Numbers own numbers** (passports/YAML); vision owns morphology only; state passport + visual + agree/conflict for each PNG.
- If text-only / headless: **skip** visual diagnosis with zero steps (no error).

## What you do

- Reframe the request in scientific / data-flow terms; produce a concise plan (ordered subskills, dependencies, branches).
- Tell the user the plan in friendly scientific language (not only CLI names); name concrete inputs (which frame for calibration, which buffer curve, …).
- Open a linked leaf `.md` only when about to run or explain that step; execute after agreement; reorder/skip/repeat when justified.
- Do **not** replace leaf procedures, safety checks, or argument contracts.

## Subskill catalog

Compact index (path → CLI hook → teaser). Purpose/use-cases live in each linked file:

- [`calibrate/calibrate.md`](calibrate/calibrate.md) (`autosaxs calibrate`) — SAXS / small-angle x-ray scattering: calibrate detector geometry using calibrant image. This is a prerequisite for 'integrate' (azimuthal integration).
- [`integrate/integrate.md`](integrate/integrate.md) (`autosaxs integrate`) — SAXS / small-angle x-ray scattering: integrate 2D SAXS images to 1D curves (q, I, sigma) using a calibrated integrator produced by 'calibrate' (azimuthal integration; q-space).
- [`average/average.md`](average/average.md) (`autosaxs average`) — SAXS / small-angle x-ray scattering: radiation-damage-aware averaging of per-frame 1D SAXS curves.
- [`integrate-proxy/integrate-proxy.md`](integrate-proxy/integrate-proxy.md) (`autosaxs integrate-proxy`) — SAXS / small-angle x-ray scattering: integrate 2D TIFF image(s) to a 1D curve without detector calibration, using radial averaging in pixel-radius space (quick-look / debugging; not q-calibrated).
- [`subtract/subtract.md`](subtract/subtract.md) (`autosaxs subtract`) — SAXS / small-angle x-ray scattering: subtract a buffer curve from a sample 1D profile (background subtraction).
- [`plot/plot.md`](plot/plot.md) (`autosaxs plot`) — SAXS / small-angle x-ray scattering: generate standard diagnostic plots for a 1D curve (Guinier, Kratky, log-log):
- [`plot-2d/plot-2d.md`](plot-2d/plot-2d.md) (`autosaxs plot-2d`) — SAXS / small-angle x-ray scattering: render 2D SAXS TIFF image(s) to PNG using log-intensity scaling (2D detector view).
- [`fit-guinier/fit-guinier.md`](fit-guinier/fit-guinier.md) (`autosaxs fit-guinier`) — SAXS / small-angle x-ray scattering: Do Guinier analysis on a 1D profile (Rg, I(0), Rg span, Guinier interval, quality).
- [`analyze-kratky/analyze-kratky.md`](analyze-kratky/analyze-kratky.md) (`autosaxs analyze-kratky`) — SAXS / small-angle x-ray scattering: dimensionless Kratky conformation analysis on a 1D profile.
- [`fit-distances/fit-distances.md`](fit-distances/fit-distances.md) (`autosaxs fit-distances`) — SAXS / small-angle x-ray scattering: obtain a pair distance distribution function \(p(r)\) for a monodisperse system from a 1D SAXS curve via ATSAS GNOM (real-space distance distribution).
- [`fit-sizes/fit-sizes.md`](fit-sizes/fit-sizes.md) (`autosaxs fit-sizes`) — SAXS / small-angle x-ray scattering: run ATSAS GNOM (system=1, spheres) to obtain a size distribution function \(D(R)\) / \(D_v(R)\) for a polydisperse system from a 1D SAXS curve.
- [`model-dr-mc/model-dr-mc.md`](model-dr-mc/model-dr-mc.md) (`autosaxs model-dr-mc`) — SAXS / small-angle x-ray scattering: recover a form-free volume-weighted size distribution
- [`calc-profile/calc-profile.md`](calc-profile/calc-profile.md) (`autosaxs calc-profile`) — SAXS / small-angle x-ray scattering: compute a theoretical I(q) from an atomic model via ATSAS CRYSOL.
- [`model-lc/model-lc.md`](model-lc/model-lc.md) (`autosaxs model-lc`) — SAXS / small-angle x-ray scattering: fit an experimental profile as a nonnegative linear
- [`model-mixture/model-mixture.md`](model-mixture/model-mixture.md) (`autosaxs model-mixture`) — SAXS / small-angle x-ray scattering: run MIXTURE fits on a 1D subtracted curve, select the best model by BIC, and write a comparison plot, size distribution plot, and results CSV.
- [`model-bodies/model-bodies.md`](model-bodies/model-bodies.md) (`autosaxs model-bodies`) — SAXS / small-angle x-ray scattering: run ATSAS 'bodies' shape fitting for multiple candidate shapes on a 1D profile.
- [`model-dam/model-dam.md`](model-dam/model-dam.md) (`autosaxs model-dam`) — SAXS / small-angle x-ray scattering: ab initio bead-model shape reconstruction with ATSAS DAMMIF, optionally followed by DAMAVER ensemble averaging. When no GNOM '.out' is supplied, 'fit_distances' is run in-process to obtain one.
- [`model-density/model-density.md`](model-density/model-density.md) (`autosaxs model-density`) — SAXS / small-angle x-ray scattering: ab initio continuous electron-density reconstruction with DENSS (Grant protocol; density map / FSC resolution / voxel σ map).
- [`process-monodisperse/process-monodisperse.md`](process-monodisperse/process-monodisperse.md) (`autosaxs process-monodisperse`) — SAXS / small-angle x-ray scattering: run the monodisperse single-profile quality pipeline
- [`process-polydisperse/process-polydisperse.md`](process-polydisperse/process-polydisperse.md) (`autosaxs process-polydisperse`) — SAXS / small-angle x-ray scattering: run the polydisperse single-profile quality pipeline
- [`process-full/process-full.md`](process-full/process-full.md) (`autosaxs process-full`) — SAXS / small-angle x-ray scattering: process a directory of TIFF frames to
- [`report-individual/report-individual.md`](report-individual/report-individual.md) (`autosaxs report-individual`) — SAXS / small-angle x-ray scattering: build a per-sample report from an existing pipeline directory.
- [`report-summary/report-summary.md`](report-summary/report-summary.md) (`autosaxs report-summary`) — SAXS / small-angle x-ray scattering: build a summary report for all samples in a pipeline directory.

## Reports (fragment contract)

Skills write `{basename}_report_individual.md` and optional `{basename}_report_summary.yaml` next to outputs. `report-individual` / `report-summary` merge those under the pipeline root.

## SAXS data I/O

Prefer `autosaxs.core.utils` helpers (`read_saxs`, `parse_gnom_out`, …) over ad-hoc parsing:

- `read_saxs(filename) -> (wavenumber, intensity, sigma, metadata)` — Read SAXS data and metadata from a file with YAML metadata and CSV data.
- `load_saxs_1d_any(filename: str) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]` — Load q, I and optionally sigma from a SAXS 1D file.
- `write_saxs(filename, wavenumber, intensity, sigma, metadata)` — Write SAXS data and metadata to a file using YAML for metadata and CSV for data.
- `write_saxs_atsas_format(filename: str, q: np.ndarray, I: np.ndarray, sigma: Optional[np.ndarray] = None) -> None` — Write SAXS data in ATSAS .dat format: plain 3 columns (q, intensity, errors),
- `read_data(filename) -> (data, columns_as_arrays, metadata)` — Read generic tabular data and metadata from a file with YAML metadata and CSV data.
- `write_data(filename, data: pd.DataFrame, metadata)` — Write generic tabular data and metadata to a file using YAML for metadata and CSV for data.

## Implementation note (`autosaxs`)

Prefer `<env>/bin/autosaxs <command>` (or the Python API in each leaf). Other tools remain valid if the user requires them.

## Provenance

- Generated by `autosaxs get-skills` (autosaxs 3.8.0).
- Static trees: bundled `playbooks/` + `examples/` (D-ASX-011).
