# guisaxs-liveview — Technical Specification

Product and behavior contract for **guisaxs-liveview** (implementation: `guisaxs_skills.liveview`).  
**Architecture:** [`liveview_architecture.md`](liveview_architecture.md) (full); ownership cheat sheet [`liveview_session_sample_plan.md`](liveview_session_sample_plan.md) (Session, SampleStore, `plan_for`, middle sync).  
**Package map:** [`../AGENTS.md`](../AGENTS.md). End-user help: `autosaxs/resources/help/guisaxs_liveview/`.

Code is authoritative when this document and the tree disagree; update this file to match code.

---

## 1. Purpose and non-goals

### 1.1 Purpose

**guisaxs-liveview** is a single-window desktop GUI that watches a **working directory**, boards samples as **2D frames** and/or **1D curves**, processes them **sequentially** via `autosaxs` skills, and updates live plots plus optional monodisperse / polydisperse analysis.

### 1.2 Non-goals

- Not a general skill console (`guisaxs-skills`).
- Not an in-process science library: orchestration + UI over skills (CLI subprocess), except GNOM adjust preview (see §8).
- No parallel per-sample processing; FIFO correctness over throughput.

---

## 2. Non-negotiable constraints

### 2.1 Shared GUI stack

Liveview lives in `guisaxs_skills.liveview` and reuses `guisaxs_skills` style, path fields, and `SkillRunner` (`logic/runner_qprocess.py`). Entry: `guisaxs-liveview` → `guisaxs_skills.liveview.app.run_liveview_app()`.

### 2.2 Skills-only compute

No direct `autosaxs.processor` / pyFAI / subtract math in the UI thread. Skills via CLI with **`use_cache=False` / `--no-cache`**.

Typical skills: `integrate_proxy`, `calibrate`, `integrate`, `subtract`, `fit_guinier`, `fit_distances`, `fit_sizes`, `model_bodies`, `model_dam`, `model_density`, `model_mixture`, report helpers as wired.

### 2.3 Responsiveness and ordering

- UI thread stays responsive; one sequential queue worker.
- Eligible files are processed in FIFO order by detection time after **settle** (`FileStatSnapshot` unchanged across checks before ingress).

---

## 3. Architecture (summary)

Three owners — details in [`liveview_session_sample_plan.md`](liveview_session_sample_plan.md); fuller map in [`liveview_architecture.md`](liveview_architecture.md):

| Owner | Role |
|-------|------|
| **LiveviewSession** (`session/api.py`) | `intake_mode`, `auto_processing`, calibrated?, `buffer_ready()`, analysis arming, watch mode, persistence; safe mutation API |
| **SampleStore** | Ordered history of `Sample` (path + boarding + stem + revision) |
| **`plan_for`** | Sole builder of per-sample job steps (`completed=` for remaining) |

Middle column: **`sync_middle_view`** / `history.sync_middle` only.  
Right analysis: **`present_right`**. Ingest: **`RevisionIngress.accept`**.

---

## 4. Session narrative

### 4.1 Start

1. Watch directory = process cwd (must exist and be writable); else exit without a window.
2. Load `<watchdir>/.guisaxs_liveview/session.yaml` when present (intake, calib, buffer/subtract options, watch mode, …).
3. Load `<watchdir>/.guisaxs_liveview/history.yaml` when present (ordered sample history, last history index, analysis arming). Missing source paths are dropped; outputs are re-read from disk when navigating.
4. Start watchers for the restored **intake mode**. Files already present are baselined as **known** (not auto-queued). Revisions or new paths enqueue after settle.

### 4.2 Change watch directory

**File → Open working directory…**: refuse if a skill is running; persist current session + history; spawn a new liveview process on the new cwd; quit. Equivalent to quit + cold start (same family as post-update relaunch).

### 4.3 Intake mode (boarding)

Right-column **Intake**: **2D** / **1D** / **Sub** (`LiveviewIntakeMode`). Persisted. Controls watchers and middle layout.

| Intake | Boards | Pipeline (via `plan_for`) |
|--------|--------|---------------------------|
| **2D** | `.tif`/`.tiff` (+ root `.dat` may auto-switch) | proxy or integrate → optional subtract → analysis? |
| **1D** | `.dat` (watchdir root + `averaged/`; reject proxy) | optional subtract → analysis? |
| **Sub** | `.dat` under `subtracted/` (or classified sub) | analysis on path |

Drops may auto-switch intake (Option A): `.dat` in 2D → classify to 1D/Sub; `.tif` in 1D/Sub → 2D. If already in 1D or Sub, dropping `.dat` does **not** auto-switch 1D↔Sub (manual toggle wins). History **Process** reuses that sample’s boarding from SampleStore.

### 4.4 Calibration and buffer (session facts)

- Successful **calibrate** → session calibrated (`integrator_dir`, …). Uncalibrated 2D jobs use `integrate_proxy`.
- **Set buffer** + subtract options → `buffer_ready()`. Then 2D/1D plans include subtract; middle uses dual S+buffer / Sub (2D hides lone integrated 1D).
- Changes apply to **subsequent** auto jobs (no mandatory backlog reprocess).

### 4.5 Auto vs Manual

Session owns `auto_processing` (default Auto; **in-memory only** — not restored from `session.yaml`). **Stop** / interventions set Manual; **Resume** restores Auto. Manual holds **auto** queue advance; manual jobs still run.

---

## 5. Watching and queue

### 5.1 Watch rules

- **2D:** TIFF watch (flat top-level or tree recursive) + optional root `.dat` aux watch (Option A → 1D/Sub).
- **1D / Sub:** `.dat` under watchdir with path filters (`averaged/` or `subtracted/` as appropriate; not `averaged_proxy/`), **plus** TIFF detection so a new `.tif` can Option A switch back to 2D (same as a drop).
- Stability before enqueue; FIFO; single worker; large queues allowed.
- Per-path revision: same path with new `FileStatSnapshot` re-queues.

### 5.2 Failure policy

Skip failed sample, log, continue queue.

### 5.3 UI queue status

Middle status: Idle / Queue · N, current path, progress affordance as implemented.

---

## 6. UI layout

Three columns (horizontal splitter):

### 6.1 Left — setup

- Calibration wizard (mask optional; shared Mask wizard; mask survives calib reset).
- Mask panel.
- Buffer / subtract config.
- Groups unused for current intake are hidden; coaching pulses relevant controls.

**Session-owned:** left setup does **not** follow history selection. Soft hints (e.g. empty buffer field ← last integrated curve) are session UX, not sample-tied views.

### 6.2 Middle — live stage

- History nav (`<` / `>` / Process) when session history non-empty.
- **2D** image when intake is 2D.
- Curve panels per [`liveview_session_sample_plan.md`](liveview_session_sample_plan.md) layout table.
- Drop target for TIFF/`.dat`.
- Queue status.

Content + layout: `history.sync_middle` only. Paint for the **currently selected** sample; the buffer curve shown in dual layout is the session buffer (not another sample’s curve).

### 6.3 Right — intake, analysis, log

- Intake toggles.
- Monodisperse / polydisperse analysis openers (separate windows).
- Live log.
- Shape / DR modeling mini-apps (child processes) receive context for the **current** sample only.

Analysis windows start **disarmed** on cold start; arming while open enables analysis steps in `plan_for` for the **current** auto job’s remaining phases and for subsequent samples. Closing the window disarms.

**Sample-tied:** right Guinier / GNOM / shape (and modeling PathFields) show only artifacts for `SampleStore.current`. Calibrant / `averaged_proxy/`-only curves are not analysis profiles — those panes stay empty rather than borrowing another sample. See [`liveview_session_sample_plan.md`](liveview_session_sample_plan.md) column ownership.

---

## 7. Outputs and naming

Under the sample output root (watchdir flat, or beside TIFF in tree mode), use existing autosaxs / `session/output_paths.py` conventions:

- `calibration/`, `averaged_proxy/`, `averaged/`, `subtracted/`
- Analysis dirs: `guinier/`, `fit_distances/`, `fit_sizes/`, `model_*`, …
- Run logs under conventions shared with skills GUI

Do not invent a parallel naming scheme.

---

## 8. Analysis modes and skill contracts

### 8.1 When analysis runs

Only if session analysis is armed **and** boarding/session allow a profile (`plan_for`). Never analysis-only path changes the meaning of Auto/Manual.

Profile input: integrated q-space, subtracted, or boarded curve path per plan table.

### 8.2 Black-box + GNOM exceptions

Skills are black boxes. **Exceptions:** monodisperse P(r) and polydisperse D(R) adjust wizards may call `autosaxs.core.atsas_gnom` in-process for preview; persistence via `SkillRunner` (`fit_distances` / `fit_sizes`).

### 8.3 Mode sequences (as implemented)

| Mode | Auto chain | Manual extras |
|------|------------|---------------|
| Off / disarmed | (none beyond integrate/subtract) | — |
| Monodisperse | `fit_guinier` → `fit_distances` | shape: BODIES / DAMMIF / DENSS via Re-run shape |
| Polydisperse | `fit_guinier` → `fit_sizes` | optional `model_mixture` |

Wizard control changes that suspend the queue call `session.set_auto_processing(False)` (via processing-mode bridge). Resume required for auto advance.

P(r) / D(R) plot y-limits stay within **[-5, 20]** (may be tighter to data).

### 8.4 Inline analysis

Analysis steps run **inline** in the same FIFO job as integrate/subtract (determinism). Use Off/disarm for faster throughput.

---

## 9. Edge cases

- Calib / buffer / intake / analysis arming changes affect **future** auto jobs by default.
- Burst: queue may grow large; UI stays responsive.
- Overwrites follow skill naming; UI shows latest artifacts for the current history sample.
- Restart: known baseline again; prior disk files not auto-queued until revised or dropped/Process.

---

## 10. Related docs

| Doc | Role |
|-----|------|
| `liveview_architecture.md` | Full architecture: concepts, code map, scenarios |
| `liveview_session_sample_plan.md` | Ownership cheat sheet (three owners + middle) |
| `skills_paradigm.md` | Skills contract for the package |
| `guisaxs_skills_spec.md` | Skill-console product requirements |
| Help HTML under `resources/help/guisaxs_liveview/` | End-user concepts (intake, queue, revisions) |
