# LiveView architecture overview

Concepts, owners, `plan_for` / replan, middle layout, code/symbol map, and remaining debt for **guisaxs-liveview** (implementation: `guisaxs_skills.liveview`).

**Code SSOT:** `guisaxs_skills.liveview` (paths below are relative to `src/guisaxs_skills/liveview/`).  
**Product / UX behavior:** [`behavior.md`](behavior.md).  
**Hard invariants:** [`invariants.md`](invariants.md).  
**Maps (YAML → Mermaid/Graphviz):** [`nodes.yaml`](nodes.yaml), index [`../README.md`](../README.md).  
**Scenarios:** [`scenarios/README.md`](scenarios/README.md).  
**Package map:** [`../../../AGENTS.md`](../../../AGENTS.md).

The installable entry package `guisaxs_liveview` is a thin launcher only (`__main__.py` → `app.run_liveview_app()`). Help HTML lives under `autosaxs/resources/help/guisaxs_liveview/`.

---

## 1. Architecture: concepts, components, ownership

### 1.1 Purpose in one line

A single-window desktop app that watches a working directory, boards samples (2D frames and/or 1D curves), runs them **sequentially** through `autosaxs` skills (CLI subprocess), and keeps middle/right plots in sync with session facts and on-disk artifacts.

### 1.2 Layering

```text
┌─────────────────────────────────────────────────────────────┐
│  UI  (left / middle / right panels, wizards, plot widgets)  │
├─────────────────────────────────────────────────────────────┤
│  Controller facade + handlers  (Qt signal fan-out)          │
├──────────────┬──────────────────┬───────────────────────────┤
│  LiveviewSession│  SampleStore  │  plan_for                 │
│  (facts + safe  │  (history)    │  (step lists)             │
│   mutations)    │               │                           │
├──────────────┴──────────────────┴───────────────────────────┤
│  RevisionIngress ← settle ← watcher / poll / tree / manual backends  │
│  Executor facade (+ manual_jobs / enrichment)               │
│  History sync: sync_middle_view ; present_right             │
├─────────────────────────────────────────────────────────────┤
│  autosaxs skills via SkillRunner (QProcess, --no-cache)     │
└─────────────────────────────────────────────────────────────┘
```

Non-negotiable: **no science compute in the UI thread** except GNOM adjust / sizes adjust **preview** (`atsas_gnom` in-process). Disk persistence of fits still goes through skills.

### 1.3 Core concepts

| Concept | Meaning |
|---------|---------|
| **Watchdir** | Process working directory; session + history YAML under `<watchdir>/.guisaxs_liveview/`. |
| **Intake / boarding** | `LiveviewIntakeMode`: `FRAME_2D`, `CURVE_1D`, `CURVE_SUB`. |
| **Sample** | Absolute path + boarding + stem + optional `SampleRevision`. |
| **SampleRevision** | Path + `FileStatSnapshot` (identity of “this file changed”). |
| **Session facts** | Calibrated?, buffer ready?, Auto/Manual, analysis arming, shape/mixture modes, conf paths — **not** which skill steps to run. |
| **PipelinePlan** | Ordered `JobStep`s plus profile/output paths (no middle-view hint). |
| **Job** | Executable unit (steps + context). Manual jobs carry `context["manual"]`. |
| **Auto vs Manual** | Whether the executor may advance **auto** jobs; plan content is unchanged. |

### 1.4 Ownership (one owner per concept)

| Concept | Owner | Module |
|---------|-------|--------|
| Session facts + safe mutations | `LiveviewSession` (wraps `LiveviewSessionState`) | `session/api.py`, `session/state.py` |
| Sample history / boarding index | `SampleStore` (+ `history.yaml`) | `session/sample_store.py`, `session/history_persistence.py` |
| Auto-process step choice | `plan_for` (+ optional `completed`) | `pipeline/plan.py` |
| Middle layout + paint | `sync_middle_view` | `services/history/middle_from_stem.py` |
| Right analysis presentation | `present_right` | `services/history/right_artifacts.py` |
| Modeling mini-apps (shape / DR) | `ModelingChildManager` → `ModelingContext` | `modeling_children.py`, `guisaxs_skills/modeling/` |
| Revision settle | `RevisionSettler.observe` → stable snap | `ingest/settle.py` |
| Revision acceptance | `RevisionIngress.accept` | `ingest/ingress.py` |
| Queue / skill run | `LiveviewJobExecutor` (+ `manual_jobs`, `artifact_enrichment`) | `pipeline/` |
| Controller wiring | `LiveviewController` + handlers | `controller/` |

```text
Ingest backends → settle → RevisionIngress → Executor
UI / handlers → LiveviewSession (set_intake, stop/resume, set_buffer, arming, mask)
SampleStore + Session.state → plan_for → Job → Executor → SkillRunner
  (executor re-calls plan_for(completed=…) on the *current* auto job only)
SampleStore + Session.state → sync_middle_view → Middle UI
present_right(source=live|disk) → presenters._ingest_* → Right UI
ModelingChildManager → ModelingContext (sample_id + family/<stem>) → child apps
```

### 1.4.1 Column ownership (session vs sample)

| Area | Tied to | Rule |
|------|---------|------|
| Left: calibration, buffer, mask | **Session** | Independent of history selection. Soft hints (e.g. empty buffer ← `last_integrated`) stay session UX. |
| Arming / shape / mixture mode (right chrome) | **Session preference** | How to run *next*; must **not** pull another sample’s disk results into the view. |
| Center history selection | **`SampleStore.current`** | Sole identity for anything “about a sample.” |
| Middle paint for selected file | **Current sample** | Via `sync_middle_view` (buffer curve itself is still the session buffer). |
| Right Guinier / GNOM / shape / sizes / mixture views | **Current sample** | Only that stem’s artifacts under the analysis root; else **empty**. |
| Modeling mini-apps (shape / DR) | **Current sample** | Push/replace whole `ModelingContext`; empty profile ⇒ blank PathFields, no sibling-dir load. |

**Invariant:** “No usable analysis profile for this sample” means empty sample-tied views — never session `last_*`, sticky prior profile, or newest sibling under a shared `dammif/` / `mixture/` family. Calibrant TIFF, buffer `.dat`, and mask are **not** sample analysis curves.

### 1.5 Session mutation surface

GUI events may still *trigger* changes; they must call **Session API** methods so persist/notify stay consistent:

- `set_intake` / `stop` / `resume` / `sync_processing_ui`
- `set_buffer` / `set_mask_path` / `set_mask_preview_path`
- `set_monodisperse_armed` / `set_polydisperse_armed` / `disarm_analysis`
- `apply_inferred_shape_mode` (disk present path only)

Reads: `session.state` (or controller `.state`).

### 1.6 `plan_for` decision table

Analysis steps append only if `session.analysis_enabled()`. Auto/Manual does **not** change this table.

| boarding | session | steps |
|----------|---------|--------|
| `FRAME_2D` | ¬calibrated | `integrate_proxy` |
| `FRAME_2D` | calibrated ∧ ¬buffer_ready | `integrate` → analysis? |
| `FRAME_2D` | calibrated ∧ buffer_ready | `integrate` → `subtract` → analysis? |
| `CURVE_1D` | ¬buffer_ready | analysis on path |
| `CURVE_1D` | buffer_ready | `subtract` → analysis on sub stem |
| `CURVE_SUB` | any | analysis on path |

**Phase-boundary replan:** `plan_for(..., completed=CompletedWork)` returns remaining steps only. The executor:

1. Plans fully at enqueue (queue snapshot).
2. Replans the **current** auto job at `_start_job` and after each successful step.
3. Never mutates queued jobs; manual jobs skip replan.

`CompletedWork` (owned by `Job.completed`) tracks finished coarse phases (`INTEGRATE` / `SUBTRACT` / `REPORT`), finished analysis step names, and skill `results` so mid-job arming can append later analysis without re-running finished work, and cancel-requeue can resume with placeholders intact. The executor never keeps a parallel progress store — it only calls `Job.mark_step_done` / `with_remaining_steps` / `as_retry`.

Shape / mixture arming is the same path: session facts change → next `plan_for(..., completed=job.completed)` on the current auto job. There is no post-job followup enqueue. Modeling skills (`model_dam` / `model_bodies` / `model_density` / `model_mixture`) stay **out** of SkillRunner. When the shape or DR mini-app is open **and Auto**, the pipeline appends a synthetic `confirm_shape` / `confirm_dr` step after distances / sizes; the executor emits `modeling_confirm_requested` → `ModelingChildManager.request_confirm_*`. Context push (history browse / path sync) never Confirm.

#### Phase-boundary replan (table)

| When | What |
|------|------|
| Enqueue (settled incoming) | `plan_for` full plan → queue (`Job.completed` empty) |
| `_start_job` (auto) | `plan_for(..., completed=job.completed)` → replace **current** job steps only |
| After each successful auto step | `job.mark_step_done(name, result=…)` + replan remaining → replace **current** steps; idx → 0 |
| Cancel-requeue | `job.as_retry(priority=…)` — same `completed` (phases + results), new id |
| Manual jobs | No replan; `mark_step_done` still stores results; frozen steps + index advance |

Arming mono/poly/shape/mixture mid-job therefore affects the current sample’s remaining analysis without re-running finished integrate/subtract, without mutating the queue, and without a separate followup path.

### 1.7 Middle layout (intake × buffer)

Owned only by `MiddlePanel.apply_intake_layout` inside `sync_middle_view`. Paint APIs (`show_*`) are fillers only, called from `_apply_paint`.

| intake | buffer | Visible |
|--------|--------|---------|
| `FRAME_2D` | no | 2D + main 1D |
| `FRAME_2D` | yes | 2D + dual S+buffer / Sub |
| `CURVE_1D` | no | main 1D |
| `CURVE_1D` | yes | main 1D + dual |
| `CURVE_SUB` | any | main titled Sub only |

---

### 1.8 Types

- **`Sample`**: absolute `path`, `boarding` (`LiveviewIntakeMode`: `FRAME_2D` / `CURVE_1D` / `CURVE_SUB`), `stem`, optional `SampleRevision`.
- **`SampleRevision`**: path + `FileStatSnapshot` (`ingest/sample_revision.py`, `stability.py`) — frame or `.dat`.
- **`PipelinePlan`**: steps, `profile_path`, `output_root`, `source_path`, `boarding`, `sample_stem` (no middle hint).
- **`CompletedWork`**: owned by `Job.completed` — finished `PlanPhase`s (integrate/subtract/report), finished analysis `step_names`, and skill `results` for phase-boundary replan + placeholders.
- **Boarding:** `LiveviewIntakeMode` (`ingest/curve_classify.py` returns the same enum).

### 1.9 Auto/Manual

- **Owner:** `LiveviewSession` → `state.auto_processing` (`stop` / `resume`).
- **Executor:** reads the session flag plus transient busy/cancel guards.
- **Process while Manual:** history Process resumes Auto (incoming promote only when Auto).
- **Shortcuts:** Ctrl+X Auto→Manual; Ctrl+P Manual→Auto (application-wide via `LiveviewMainWindow` `QApplication` event filter, so mono/poly and other liveview wizards — modal or not — receive them; skipped while typing in text fields).

### 1.10 Ingest

Backends (FLAT watcher, TIFF poller, TREE observer, aux dat, drops/Process) emit observations into **`RevisionSettler`**. Only after the on-disk `FileStatSnapshot` is unchanged across settle polls does a `SampleRevision` reach **`RevisionIngress.accept`** → SampleStore boarding + executor admit queue (`_incoming`). Watch topology follows `state.intake_mode`. Known-path baseline at watcher start; change identity is `FileStatSnapshot`. Manual drops after `copy2` may enter ingress directly (file already complete).

## 2. Implementation map

### 2.1 Package tree

```text
guisaxs_liveview/          # thin CLI entry
guisaxs_skills/liveview/
├── app.py, window.py
├── controller/            # facade + handlers (no ProcessingMode)
├── session/               # state, api (LiveviewSession), samples, session/history persistence
├── pipeline/              # plan, jobs (CompletedWork), executor facade, manual_jobs, artifact_enrichment
├── ingest/                # ingress, watchers, stability, classify
├── modeling_children.py   # shape/DR child processes + ModelingContext
├── services/history/      # sync_middle_view, present_right, right_artifacts
└── ui/                    # panels, wizards, widgets
```

Modeling apps live under `guisaxs_skills/modeling/` (entries `guisaxs-shape` / `guisaxs-dr`).

### 2.2 Key symbols

| Symbol | Role |
|--------|------|
| `LiveviewSession` | Fact bag owner + mutation/persist/signals |
| `LiveviewSessionHandler` | UI apply/reset after calib/buffer wipes |
| `RevisionIngress` | Single accept path for revisions |
| `plan_for` / `PipelinePlan` | Sole auto-process planner (+ remaining-step filter via `completed=`) |
| `Job` / `CompletedWork` | Sole owner of finished progress for a run (phases, step names, results) |
| `LiveviewJobExecutor` | Qt facade: tick, queues, skill steps; mutates current job via Job API only |
| `_manual_jobs` / `_artifacts` | Semantic collaborators behind the facade |
| `sync_middle_view` | Sole middle sync |
| `present_right` | Sole right presentation entry (`LIVE` / `DISK`); current-stem discovery only |
| `ModelingChildManager` | Owns shape/DR children; `ModelingContext` for current sample |

### 2.3 Skills invocation

| Path | Mechanism |
|------|-----------|
| Default / auto | Executor → `SkillRunner` → QProcess CLI |
| Manual | `enqueue_manual_skill` / manual jobs |
| GNOM / sizes **preview** | In-process `atsas_gnom`; Confirm still via skills |

---

## 3. Scenarios

Scenario summary table and overlays: [`scenarios/README.md`](scenarios/README.md).

---

## 4. Perspective: remaining architecture improvements

**Done (do not re-open):** unused `MiddleViewHint` / `ProcessingMode` removed; Session API for intake/stop/buffer/mask/arming; middle paint only via `sync_middle_view`; right entry via `present_right`; ingest via `RevisionIngress`; executor collaborators (`manual_jobs`, `artifact_enrichment`); phase-boundary replan (`plan_for(..., completed=)` on current auto job only); `Job.completed` as sole progress owner; post-job shape/mixture followups deleted (arming is replan-only); modeling Confirm is pipeline-owned (`confirm_shape` / `confirm_dr` → `ModelingChildManager.request_confirm_*`), never context-push.

**Still worth doing when touching the area:**

1. **UI module size** — still the largest debt: `ui/widgets/plots.py` (~1k), `ui/wizards/left.py`, mask dialog, left/right panels, mono presenter. Split along paint vs wizard vs chrome when those files are edited.
2. **Executor facade size** — `pipeline/executor.py` still owns tick + admit queue + job lifecycle + skill outcomes. Extract `incoming.py` / `job_lifecycle.py` if that loop grows again; meanwhile prune dead facade helpers (`monodisperse_steps_*`, unused mixture option helper) and stop poking `_load_yaml_options` from controllers.
3. **Session write discipline** — intake/stop/buffer/mask/arming go through `LiveviewSession`; wizard params, shape/mixture modes, calibration outcomes, and executor `last_*` paths still write `state` directly. Add Session helpers (or explicitly document intentional exceptions) if that surface keeps spreading.
4. **One analysis-step assembler** — `plan._analysis_steps` and `manual_jobs.analysis_steps_for_profile` are near-duplicates. One owner (planner-side) with manual/auto call sites would match “one owner per concept.”
5. **Single LIVE right ingest** — `LiveviewSkillOutcomesHandler` can call `present_right(LIVE)` from both `on_latest_artifacts` and success handling; collapse to one call site so analysis finishes are not double-presented.

**Optional / low urgency (fine to leave):**

- FLAT watcher/poll/tree backends as pluggable registrations behind ingress (algorithms unchanged).
- Structural merge of mono/poly leaf presenters (entry is already unified; merge only if passport logic drifts).
- Drop unused `apply_middle_view_from_*` exports once nothing imports them.
