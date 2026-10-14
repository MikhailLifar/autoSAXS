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
- Code root: `src/guisaxs_skills/liveview/`.
- Mixture option names / init peaks: `.agent/liveview/model-mixture-options.md`.
