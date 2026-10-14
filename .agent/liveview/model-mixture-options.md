# model_mixture option naming

## Concepts

- **Skill inputs** (CLI/API): `r_max`, `poly_max`, `q_min_nm`, `q_max_nm`, …
- **Disk handoff / skill returns**: `r_max_nm`, `poly_max_nm`, … (resolved bounds in nm)
- Legacy DR UI once used `q_min` / `q_max` for mixture options

## Owner

`normalize_model_mixture_options` in `guisaxs_skills/modeling/run_params.py` is the single mapper from disk/legacy aliases → skill input names.

Session `model_mixture_options`, DR Confirm opts, and `polydisperse_pipeline.model_mixture_opts` must hold **skill names only**. `RunRequest.cli_argv` maps keys 1:1 (`r_max` → `--r-max`); handoff names like `r_max_nm` become unrecognized CLI flags → exit 2.

## Component init

Skill CLI/API (comma-separated strings, nm / fractions):

- `init_radii_nm` — per-phase ATSAS MIXTURE `Rout` starts
- `init_fractions` — volume fractions (normalized to 1)
- `init_poly_nm` — per-phase polydispersity starts

Priority inside the skill: explicit `init_*` → `fit_sizes` handoff `peak_positions_nm` (radii only) → defaults. ATSAS also fixes inner radius/contrast, outer contrast, sticky=0; hard-sphere radius is derived from Rout (not a separate user input).

`normalize_model_mixture_options` keeps these as skill keys. DR advanced section edits them before Confirm; run-params YAML persists the same strings.
