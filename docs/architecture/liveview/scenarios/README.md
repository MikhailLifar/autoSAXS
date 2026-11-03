# LiveView scenarios (index)

Summary table relocated from the former liveview architecture essay. Bug/fix overlays live as YAML diagram ids (`scenario-mono-adjust-bug`, `scenario-mono-adjust-fix`); prose walkthrough: [`sample-switch-open-adjust.md`](sample-switch-open-adjust.md).

Maps: [`../nodes.yaml`](../nodes.yaml). Overview: [`../overview.md`](../overview.md). Invariants: [`../invariants.md`](../invariants.md).

## Summary

| Scenario | User sees | Internal |
|----------|-----------|----------|
| Cold start | Restored calib/buffer/intake; files not auto-queued | `LiveviewSession` loads YAML; watchers baseline; `sync_middle` |
| New frame | Queue advances; plots update | Backend → settle → `RevisionIngress.accept` → `plan_for` → skills |
| Drop `.dat` | May auto-switch from 2D | Classify → `session.set_intake` → manual revision via ingress |
| Drop / watch `.tif` while 1D/Sub | Option A → 2D | TIFF detector stays on in curve intake → `_on_tiff_while_curve` → same drop path |
| Calibrate | Left shows success; later integrate | Manual skill; outcomes write calib facts |
| Buffer set | Dual layout | `session.set_buffer` → `buffer_changed` → middle sync |
| Stop / Resume | Auto queue held / released | `session.stop` / `resume` |
| History `<`/`>` | Middle + right for that stem | `sync_middle` + `present_right(DISK)`; empty profile ⇒ empty right |
| Select calibrant (proxy only) | Right analysis empty | No usable profile; no foreign-stem fallback |
| Arm analysis | Current job’s remaining steps grow analysis | Session arming; executor replans current via `plan_for(completed=…)` |
| Live skill finish | Right panes update | `present_right(LIVE)` → `ingest_skill_result` → `_ingest_*` |
| Open shape/DR modeling | Child shows current sample paths; Confirm only via pipeline step or user click | `ModelingChildManager` pushes `ModelingContext` (`family/<stem>`); `confirm_shape` / `confirm_dr` after analysis |

---
