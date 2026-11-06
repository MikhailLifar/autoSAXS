# Changelog

## 3.8.0

### Highlights since 3.7.0

#### Skills / analysis
- Fast AutoGNOM H32 Stage-1 + ATSAS top-K Stage-2 for `fit_distances` and `fit_sizes`
- Redesigned `process-full` DAM/MIXTURE modeling gates
- Removed legacy interactive pipeline and deprecated `fit_*` skill aliases

#### Liveview
- Configuration pane for hierarchical `buffer_rules` (YAML seed/migrate, dark-theme highlighter)
- Soft-wait when buffer is not yet present; stop “Set buffer” coach after Configuration
- Unified effective buffer for coach + subtraction wizard
- `buffer_rules` `{capture}` matching with conflict hold; rules UI in Buffer wizard
- Confirm/Apply + go Auto dual buttons
- Single LIVE `present_right` + session write discipline; stem→path resolve without `SampleAnalysisContext`
- 3D mesh QThread ownership / teardown fixes

#### Core / packaging / docs
- Gradual `core/utils` split: `saxs_io` + `yaml_config` (P5); restored missing `import re` in `saxs_io`
- Lazy ATSAS probe / thinner skill deps; shared CLI–GUI request codec + framed JSON results
- Portable commit gate (`helpers/run_portable_gate.sh`) + liveview importlinter edges
- Docs reorganized under `docs/paradigm`, `docs/architecture`, `docs/archive`
- `autosaxs get-docs` uses eager `get_skill` so `skills_reference.md` keeps full docstrings under lazy skill loading

### Known limitations
- Real-data light mono/poly pipeline validation vs reference remains authorized debt (`test_monodisperse_pipeline_validation`, `test_polydisperse_pipeline_validation`). Skills suite and calib→integrate→subtract light path are green.

### Install

```bash
pip install autosaxs==3.8.0
# or from GitHub tag once published:
pip install "autosaxs @ git+https://github.com/MikhailLifar/autoSAXS.git@v3.8.0"
```

Installer ZIPs (optional GitHub release assets): rebuild with `scripts/pack_installers.sh`.

After install: `autosaxs doctor`, `autosaxs --help`.
