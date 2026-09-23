# Liveview architecture: Session, Sample, Plan

Ownership cheat sheet for `guisaxs_skills.liveview` (code is SSOT).
Full architecture (concepts, code map, scenarios, improvements): [`liveview_architecture.md`](liveview_architecture.md).
Product/UX contracts: [`guisaxs_liveview_spec.md`](guisaxs_liveview_spec.md). Agent map: [`../AGENTS.md`](../AGENTS.md).

## Owners

| Owner | Module | Stores / decides |
|-------|--------|------------------|
| **LiveviewSession** | `session/api.py` + `session/state.py` | Facts + safe mutations (`set_intake`, `stop`/`resume`, `set_buffer`, arming, mask). Does **not** choose step lists. |
| **SampleStore** | `session/sample.py`, `sample_store.py` | History + boarding on each `Sample`; path index; current sample for UI. |
| **Planner** | `pipeline/plan.py` | `plan_for(session, sample, completed=?) → PipelinePlan` — **only** place that chooses integrate_proxy / integrate / subtract / analysis / report. |
| **Job** | `pipeline/jobs.py` | Owns `CompletedWork` (phases, step names, skill results) for one run; executor mutates only via Job API. |
| **Middle sync** | `services/history/middle_from_stem.py` | `sync_middle_view` — layout + disk paint. |
| **Right present** | `services/history/right_artifacts.py` | `present_right` — live skill result or disk discover → presenters. |
| **Revision settle** | `ingest/settle.py` | `RevisionSettler` — one owner of readiness; only stable snaps reach ingress. |
| **Revision ingress** | `ingest/ingress.py` | `RevisionIngress.accept` — single front door for settled revisions (watcher/poll/tree/manual). |

```text
Ingest backends → settle → RevisionIngress → SampleStore / Executor
UI → LiveviewSession
SampleStore + Session.state → plan_for → Job → Executor
SampleStore + Session.state → sync_middle_view → Middle UI
present_right(LIVE|DISK) → Right UI
```

## Types

- **`Sample`**: absolute `path`, `boarding` (`LiveviewIntakeMode`: `FRAME_2D` / `CURVE_1D` / `CURVE_SUB`), `stem`, optional `SampleRevision`.
- **`SampleRevision`**: path + `FileStatSnapshot` (`ingest/sample_revision.py`, `stability.py`) — frame or `.dat`.
- **`PipelinePlan`**: steps, `profile_path`, `output_root`, `source_path`, `boarding`, `sample_stem` (no middle hint).
- **`CompletedWork`**: owned by `Job.completed` — finished `PlanPhase`s (integrate/subtract/report), finished analysis `step_names`, and skill `results` for phase-boundary replan + placeholders.
- **Boarding:** `LiveviewIntakeMode` (`ingest/curve_classify.py` returns the same enum).

## `plan_for` decision table

| boarding | session | steps |
|----------|---------|--------|
| FRAME_2D | ¬calibrated | integrate_proxy |
| FRAME_2D | calibrated ∧ ¬buffer_ready | integrate → analysis? |
| FRAME_2D | calibrated ∧ buffer_ready | integrate → subtract → analysis? |
| CURVE_1D | ¬buffer_ready | analysis on path |
| CURVE_1D | buffer_ready | subtract → analysis on sub_stem |
| CURVE_SUB | any | analysis on path |

Analysis appended only if `analysis_enabled()`. **Auto/Manual does not change the plan** — only whether the executor schedules auto jobs. Manual jobs still run when Manual (`auto_processing=False`).

When `completed` is set, `plan_for` returns only **remaining** steps (filters finished phases / step names). The executor applies this only to `_current_job` (at job start and after each success). Queued jobs keep their enqueue-time plan until they become current.

## Phase-boundary replan

| When | What |
|------|------|
| Enqueue (settled incoming) | `plan_for` full plan → queue (`Job.completed` empty) |
| `_start_job` (auto) | `plan_for(..., completed=job.completed)` → replace **current** job steps only |
| After each successful auto step | `job.mark_step_done(name, result=…)` + replan remaining → replace **current** steps; idx → 0 |
| Cancel-requeue | `job.as_retry(priority=…)` — same `completed` (phases + results), new id |
| Manual jobs | No replan; `mark_step_done` still stores results; frozen steps + index advance |

Arming mono/poly/shape/mixture mid-job therefore affects the current sample’s remaining analysis without re-running finished integrate/subtract, without mutating the queue, and without a separate followup path.

## Auto/Manual

- **Owner:** `LiveviewSession` → `state.auto_processing` (`stop` / `resume`).
- **Executor:** reads the session flag plus transient busy/cancel guards.

## Middle column

Sole sync owner: `services/history/middle_from_stem.sync_middle_view` (via `history.sync_middle`).

- **Layout visibility:** `LiveviewMiddlePanel.apply_intake_layout(intake, buffer_ready)` only.
- **Paint:** `show_curve` / `show_subtraction_*` / `show_image` (no visibility) — only from `_apply_paint`.

### Layout (intake × buffer)

| intake | buffer | Visible |
|--------|--------|---------|
| FRAME_2D | no | 2D + main 1D |
| FRAME_2D | yes | 2D + dual S+buffer / Sub (**no** lone integrated 1D) |
| CURVE_1D | no | main 1D |
| CURVE_1D | yes | main 1D + dual |
| CURVE_SUB | any | main titled Sub only |

## Ingest

Backends (FLAT watcher, TIFF poller, TREE observer, aux dat, drops/Process) emit observations into **`RevisionSettler`**. Only after the on-disk `FileStatSnapshot` is unchanged across settle polls does a `SampleRevision` reach **`RevisionIngress.accept`** → SampleStore boarding + executor admit queue (`_incoming`). Watch topology follows `state.intake_mode`. Known-path baseline at watcher start; change identity is `FileStatSnapshot`. Manual drops after `copy2` may enter ingress directly (file already complete).
