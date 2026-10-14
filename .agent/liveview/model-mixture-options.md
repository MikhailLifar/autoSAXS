# model_mixture option naming

## Concepts

- **Skill inputs** (CLI/API): `r_max`, `poly_max`, `q_min_nm`, `q_max_nm`, …
- **Disk handoff / skill returns**: `r_max_nm`, `poly_max_nm`, … (resolved bounds in nm)
- Legacy DR UI once used `q_min` / `q_max` for mixture options

## Owner

`normalize_model_mixture_options` in `guisaxs_skills/modeling/run_params.py` is the single mapper from disk/legacy aliases → skill input names.

Session `model_mixture_options`, DR Confirm opts, and `polydisperse_pipeline.model_mixture_opts` must hold **skill names only**. `RunRequest.cli_argv` maps keys 1:1 (`r_max` → `--r-max`); handoff names like `r_max_nm` become unrecognized CLI flags → exit 2.

## Component init

`fit_sizes` handoff `model_mixture.peak_positions_nm` seeds MIXTURE phase `Rout` starts inside the skill (`mixture._phase_init_radii_ang`). Peaks are not CLI options. DR window surfaces the same hints and may prefill `max_nph` from `n_phases_suggested` when unset.
