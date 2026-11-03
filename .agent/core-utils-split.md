# `core/utils.py` gradual split (P5)

## Purpose

Shrink the mixed `autosaxs.core.utils` hotspot by responsibility, without a big-bang rewrite.

## Ownership (current slices)

| Concern | Owner module | Temporary facade |
|---------|--------------|------------------|
| SAXS 1D / tabular I/O (`read_saxs`, `write_saxs`, ATSAS `.dat`, `ensure_q_nm`, `load_saxs_1d_any`, YAML+CSV `read_data`/`write_data`) | `autosaxs.core.saxs_io` | re-exported from `utils` |
| YAML config load/save/update | `autosaxs.core.yaml_config` | re-exported from `utils` |
| Remaining mixed helpers (geometry, shape density, pipeline path maps, report stem helpers, …) | `autosaxs.core.utils` | — |

## Invariants

- Callers of `autosaxs.core.utils` keep working via re-exports until follow-up PRs retarget imports.
- No behavior change in extracted functions (AST-equivalent move).
- Prefer new imports against `saxs_io` / `yaml_config`.

## Deleted (dead)

- `get_pipeline_description` — no live callers; `resources/pipelines/` already gone. `get_pipeline_paths` / `map_sample_files_to_buffer_files` still used (legacy `Context` / pairing) and stay in `utils` for now.

## Next seams (not this PR)

Detector / circle / annulus geometry → shape / density / bodies CIF → retire remaining pipeline path helpers once callers are gone.

## Related

- Plan W6 / P5 in architecture update plan (store docs).
