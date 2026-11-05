# autoSAXS skills reference

This document is the detailed reference for public *skills* exposed by the `autosaxs` package. For a short project overview, install notes, and GUIs, see the package [`README.md`](../README.md).

Skills are Python functions in the `autosaxs.skill` package (`src/autosaxs/skill/`) with a fixed signature designed to be callable both from Python and from the `autosaxs` CLI.

### CLI vs Python (how commands are wired)

The `autosaxs` command dispatches subcommands to the corresponding skill functions by introspecting their signatures. In practice:

- Run a skill from the CLI as `autosaxs <command> ...`.
- Every skill supports `-o` / `--output-dir <path>` (maps to the skill's `output_dir` argument, default: `.`).
- Every skill supports caching; use `--cache` to enable it (maps to `use_cache=True` in Python). Use `--no-cache` to explicitly disable it.
- Positional arguments in the CLI match the skill signature order.
- Keyword options use `--kebab-case` names (underscores become `-`).
- Brief CLI `--help` text for skill-specific options comes from the skill docstring section **`### Short parameter list`** (one bullet per parameter: ``- param_name: help text``).

### Path expansion (important API behavior)

Most skills take a **path expression** rather than a strict “single file”:

- A file path is used as-is.
- A directory expands to matching files (non-recursive):
  - 2D inputs: `*.tif`
  - 1D inputs: `*.dat`
- A glob expression is allowed (including `**`); results are sorted, and **empty expansion is an error**.

Note: `autosaxs integrate` accepts either a single path expression **or** multiple image paths on the CLI (the CLI passes a list; the skill normalizes it).

### Caching (opt-in)

- When `use_cache=True`, a skill may write/read a hidden `.cache` YAML file inside its output directory.
- Re-running with the same inputs and relevant options can reuse previously generated output paths if the files still exist and are recent enough (output-integrity check).
- On cache hits, the returned dict includes `from_cache=True` in addition to the usual output path keys.

### Related GUIs

- **`guisaxs-skills`** — form-driven runner over the skills API (requires `autosaxs[gui]`).
- **`guisaxs-liveview`** — watch-folder live integration / subtraction with optional monodisperse or polydisperse analysis windows.

See the package README for launch and layout details.

---

## `calibrate`



---

## `integrate`



---

## `average`

Radiation-damage-aware averaging of per-frame 1D SAXS curves.

Compares each frame to the first (lexicographic) reference using reduced chi-squared
and CorMap (Franke et al., Nature Methods 2015; Schilling 1990), then merges
accepted frames with inverse-variance weighting.

---

## `integrate_proxy`



---

## `subtract`



---

## `plot`



---

## `plot_2d`



---

## `fit_guinier`



---

## `analyze_kratky`



---

## `fit_distances`

DATGNOM / p(r) skill (``fit_distances`` / ``autosaxs fit-distances``).

---

## `fit_sizes`

GNOM D(R) skill (``fit_sizes`` / ``autosaxs fit-sizes``).

---

## `model_dr_mc`

McSAS3 form-free D(R) skill (``model_dr_mc`` / ``autosaxs model-dr-mc``).

---

## `calc_profile`



---

## `model_lc`



---

## `model_mixture`



---

## `model_bodies`



---

## `model_dam`

Ab initio DAMMIF / DAMAVER bead-model skill (``model_dam`` / ``autosaxs model-dam``).

---

## `model_density`

Ab initio DENSS electron-density skill (``model_density`` / ``autosaxs model-density``).

---

## `process_monodisperse`

Meta-skill: monodisperse single-profile quality pipeline (Guinier onward).

Wires existing leaf skills only — no reimplementation of Guinier / Kratky / GNOM / DAMMIF math.
Sequence follows ``docs/saxs_quality_guide_newest.docx`` steps 4–7 and 9
(omit calibration, radiation averaging, buffer subtraction, and polydisperse step 8).
Optional ``model_dam`` when the shared modeling gate passes
(``autosaxs.core.gnom_quality.allows_modeling``).

---

## `process_polydisperse`

Meta-skill: polydisperse single-profile quality pipeline (Guinier onward).

Wires existing leaf skills only — no reimplementation of Guinier / GNOM / MIXTURE math.
Sequence: fit_guinier → fit_sizes → optional model_mixture (opt-in + modeling gate) →
report_individual. Omits calibration, radiation averaging, buffer subtraction, and
monodisperse-only steps (Kratky / DATGNOM / DAMMIF).

---

## `process_full`

Meta-skill: YAML-driven TIFF directory → analysis PDF report(s).

Thin router: load pipeline config (``analysis: mono|poly``) and delegate to
``process_monodisperse`` / ``process_polydisperse`` with the frames directory as
the first path argument. Those meta-skills infer the TIFF full pipeline from
that path (see ``tiff_pipeline.resolve_frames_dir_for_tiff_pipeline``).

---

## `report_individual`



---

## `report_summary`



---
