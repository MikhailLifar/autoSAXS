# Liveview session write discipline + finish path (P1)

## Purpose

One LIVE right paint per analysis finish; Session owns durable session fact writes; `Job.completed` stays the sole progress owner.

## Owners

- **Middle after skill step:** `LiveviewSkillOutcomesHandler.on_latest_artifacts` → `sync_middle_view` / profile hints only (no `present_right`).
- **Right LIVE:** sole call site `LiveviewSkillOutcomesHandler._handle_success` → `present_right(LIVE)` after executor enrichment.
- **Right DISK:** history browse → `present_right(DISK)` (unchanged).
- **Progress:** `Job.completed` via `mark_step_done` only.
- **Session facts:** `LiveviewSession` helpers for intake/stop/buffer/mask/arming, `last_*`, calibration outcomes, inferred shape/mixture modes.

## Finish path

```
runner.finished
  → enrich result (fit_distances / fit_guinier)
  → latest_artifacts (middle + profile hints)
  → skill_finished → _handle_success → present_right(LIVE)  # analysis skills only
  → mark_step_done / replan  # Job.completed
```

## Intentional exceptions (wizard UI)

Right-panel coordinators / config_sync may still write ephemeral wizard params, conf paths, and shape/mixture mode toggles directly on `state` during UI Apply. Those are UI-owned ephemeral sync, not a third pipeline write path. Prefer Session helpers when adding new durable facts.

## Pruned executor facade

Removed unused wrappers: `monodisperse_steps_*`, `monodisperse_step_shape`, `_analysis_steps_for_profile`, `_subtraction_output_root`, `_model_mixture_run_options`, `_load_yaml_options`. Callers use `build_monodisperse_steps` / `manual_jobs` / `yaml_options.load_yaml_options` directly.
