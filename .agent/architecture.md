# Architecture memory

## LiveView

- **Purpose:** Single-window watchdir app; sequential `autosaxs` skills; middle/right plots follow session facts + on-disk artifacts.
- **Owners:** `LiveviewSession` (facts), `SampleStore` (current sample identity), `plan_for` (steps), `RevisionSettler`/`RevisionIngress` (ingest), `sync_middle_view` / `present_right` (presentation), `LiveviewJobExecutor` (run).
- **Invariant (sample-tied):** Right panes + Adjust wizards follow `SampleStore.current` analysis identity; session wizard params are defaults/gaps only. See `docs/architecture/liveview/invariants.md`.
- **Maps SSOT:** `docs/architecture/liveview/nodes.yaml` → `scripts/render_arch.py` → Mermaid/Graphviz. Index: `docs/architecture/README.md`.
- **Prose:** `docs/liveview_architecture.md`, `docs/liveview_session_sample_plan.md`.

## Related

- Adjust rebind drilldown: diagram id `gnom-adjust-bind` (mono; sizes twin).
- Scenario overlays (bug/fix): `scenario-mono-adjust-bug`, `scenario-mono-adjust-fix` (`emphasize: broken|fixed` in renderer).
- I(q)/Adjust intensity SSOT: presenter/session `sub_*.dat` (never sibling `*_atsas.dat` as UI owner). See `docs/architecture/liveview/invariants.md`.
- Code root: `src/guisaxs_skills/liveview/`.
- Mixture option names / component init (`init_radii_nm` / fractions / poly): `.agent/liveview/model-mixture-options.md`.
- DR results navigation: `modeling/catalogs/mixture_models.py` (ensemble fit browser; same role as `dam_models` for shape).
- 3D preview chrome: `LiveviewViewer3D` owns CIF/MRC path checks via `pathlib`; revealing folders is `ui/os_open.open_path_in_file_manager` (shared with DR).
- Modeling mini-apps: `.agent/liveview/modeling-session-controls.md` (paths from context push; run knobs session-owned).
