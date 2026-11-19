# Decisions journal

**Purpose:** Accepted product/architecture decisions with enough justification that a later reader can reconstruct *why*, not only *what*.  
**Scope split:** **autoSAXS** (package / skills / CLI / science protocols) vs **GUI** (`guisaxs_skills` / liveview).  
**Rule:** see [decisions-journaling-rule.md](decisions-journaling-rule.md) (installed in repo as `.cursor/rules/decisions-journaling.mdc`).  
**Repo SSOT on mikllaptop:** `/home/mikl/KurchatovCoop/autosaxs/docs/DECISIONS.md`.

Entries are append-only. Superseded decisions stay; mark `Status: superseded by D-…`.

---

## autoSAXS

### D-ASX-001 — `process_*` keeps hard quality gates; optional `gate_pass` bypass (2026-11-13)

**Status:** accepted  
**Context:** Code-vs-skills split (ASX-62) proposed demoting `allows_modeling` / `evaluate_modeling_gate` to advisory-only inside `process_monodisperse` / `process_polydisperse`, aligning with the general “metrics inform, never hard-gate” agent preference.

**Decision:** For **`process_monodisperse` and `process_polydisperse` only**, hard gates remain the **default** intended behavior: these skills exist for unattended batch processing without human review. Add an explicit flag (proposed name **`gate_pass`**, exact CLI spelling TBD at implement time) that bypasses quality gates while still **recording the same checks as advice/report fields**.

**Justification:**

1. Product role of `process_*` is full automatic pipelines; stopping on failed TE/ΔRg/shannon-style checks avoids silently shipping bad envelopes/mixtures when no scientist is watching.
2. The broader preference “metrics inform, never hard-gate” still applies to **interactive / agent-orchestrated** single skills (`fit_distances`, `fit_sizes`, `model_dam`, …) and to playbook guidance — not to unattended `process_*` defaults.
3. `gate_pass` preserves the advisory scheme for operators who want “run anyway, but tell me the risk.”

**Not decided here:** exact parameter name/default; whether empty/invalid GNOM output remains a hard abort even with `gate_pass` (recommended: yes).

**Refs:** Mikhail review 2026-11-13; Project store `docs/saxs-code-vs-skills-split.md`; Project store `docs/code-change-citations.md`.

---

### D-ASX-002 — No new user-facing diagnostic skill by default (2026-11-13)

**Status:** superseded in part by D-ASX-013 (plot retirement → fit_porod + analyze_loglog only; still no profile_diagnostics bag)  
**Decision:** Prefer enriching existing skill passports / `core/` helpers over a new `profile_diagnostics` CLI skill. Optional aggregator skill only if later requested (P2).

**Justification:** Keeps the public skill surface small; diagnostics stay attached to the step that produced the data.

**Refs:** Mikhail review 2026-11-13; confirmed with D-ASX-003…010 landing 2026-11-17.

---

### D-ASX-003 — `subtract`: `frac_neg` + `q_first_neg_nm` only (2026-11-17)

**Status:** accepted  
**Context:** Split proposal SB-1/SB-2 asked for a richer under-subtraction suite on `subtract` (baseline / high-q-only / twin with S(q)).

**Decision:** Enrich `subtract` passport / metadata with **`frac_neg`** and **`q_first_neg_nm`** only. Keep legacy string ``correctness`` (`correct` | `over-subtracted`). Do **not** implement a full under-subtraction assessment suite on `subtract`.

**Justification:** Over-subtraction is already visible as high-q negativity (``I+σ<0``). Under-subtraction is better diagnosed via Jacques P(0) after releasing force-zero at r=0 — that check lives on fit passports (D-ASX-004), not on subtract.

**Refs:** Mikhail review 2026-11-17; Jacques & Trewhella 2010 journal p.649 / corpus `5-pro0019-0642.pdf` PDF p.8; Project store `docs/code-change-citations.md`.

---

### D-ASX-004 — P(0) solvent check on `fit_distances` / `fit_sizes` (2026-11-17)

**Status:** accepted  
**Context:** Jacques solvent test: release P(r)→0 at r=0; positive P(0)=under-, negative=over-subtraction.

**Decision:** Emit structured **`p0`** and **`p0_classification`** (`under_subtracted` | `over_subtracted` | `ok` | `unknown`) from a force-zero-**rmin**-off probe at the selected Dmax/Rmax on **`fit_distances` and `fit_sizes`**. Not on `subtract`.

**Human-facing passport (clarified 2026-11-17):** exactly **one** new Metric/Value row. Metric = ``P(0)``. Value = numeric P0; append `` (under_subtracted)`` or `` (over_subtracted)`` **in the same cell only** when so classified. Do **not** show a separate classification row; do **not** bracket ``ok`` / ``unknown``. Example: ``P(0) = 0.012 (over_subtracted)`` or ``P(0) = 0.012``.

**Justification:** The literature test is an IFT boundary-condition probe, owned by the GNOM skills that already run force-zero ensembles. Subtract stays high-q negativity only (D-ASX-003). One passport row keeps the table scannable while still surfacing solvent mismatch when it matters.

**Refs:** Mikhail 2026-11-17 (+ same-day UI correction); Jacques & Trewhella 2010 p.649 / `5-pro0019-0642.pdf` p.8.

---

### D-ASX-005 — Guinier: no change (2026-11-17)

**Status:** accepted  
**Decision:** **No** Guinier `residual_shape` / smile–frown passport work. Existing ``classification`` (upturn / downturn / …) is enough.

**Justification:** Avoid passport duplication; existing low-q curvature class already covers aggregation vs repulsion signaling for playbooks.

**Refs:** Mikhail 2026-11-17; strikes GK-1 code change in citations SSOT.

---

### D-ASX-006 — `average`: no Rg/I(0) drift fits (2026-11-17)

**Status:** accepted  
**Decision:** **No** per-frame Guinier Rg/I(0) drift fits or damage-summary YAML beyond what CorMap already provides.

**Justification:** CorMap selection already rejects damaged frames; drift fits add cost without a requested product owner.

**Refs:** Mikhail 2026-11-17; strikes RA-1 code enrichment in citations SSOT.

---

### D-ASX-007 — `fit_sizes` passport: `powerlaw_exponent` (2026-11-17)

**Status:** accepted  
**Decision:** Add **`powerlaw_exponent`** (high-q ``I∝q^α`` slope, ``q`` in nm⁻¹) to the `fit_sizes` quality passport; warn (do not hard-gate) when slope is near −1/−2 for sphere kernels.

**Justification:** Li 2016 dimensionality / Porod exponents (−1/−2/−4) are the right advisory for refusing silent sphere PSD on rods/disks.

**Refs:** Mikhail 2026-11-17; Li et al. 2016 Chem. Rev. corpus `7-1391950.pdf` letter G–H.

---

### D-ASX-008 — Shannon π/q_min: already present — no change (2026-11-17)

**Status:** accepted  
**Decision:** **No** new Shannon / ``dmax ≈ π/q_min`` soft-flag work; existing passport Shannon fields stand.

**Refs:** Mikhail 2026-11-17.

---

### D-ASX-009 — Kratky: no change (2026-11-17)

**Status:** accepted  
**Decision:** **No** Kratky label / threshold changes in this landing.

**Refs:** Mikhail 2026-11-17.

---

### D-ASX-010 — `detect_iq_peaks` core helper (2026-11-17)

**Status:** accepted  
**Decision:** Add ``autosaxs.core.profile_features.detect_iq_peaks`` (deterministic peak list, ``q`` in nm⁻¹). Wire into an existing output — **owner: `subtract` report fragments / summary YAML** (`iq_peaks`) — not a new user-facing skill (D-ASX-002).

**Justification:** Peak finding is deterministic; Bragg / S(q) / form-factor interpretation stays playbook (Spinozzi 2024 LNP pins ~1.43–1.60 nm⁻¹).

**Refs:** Mikhail 2026-11-17; Spinozzi 2024 JCIS corpus `24-Spinozzi-2024-JCIS-VoR.pdf` p.450 / PDF p.5.

---

### D-ASX-011 — Bundle playbooks/examples via `get-skills`; thin SKILL.md router (2026-11-17)

**Status:** accepted  
**Context:** Phase 2 of the SAXS knowledge/playbooks design: curated Markdown under `src/autosaxs/resources/ai_skills/{playbooks,examples}/` must ship with the agent skill tree. Mikhail approved a **thin** `SKILL.md` router with depth in playbooks/examples (not a long inline workflow essay).

**Decision:**

1. `autosaxs get-skills` exports static `playbooks/` and `examples/` into `saxs-processing/` alongside generated leaf procedure docs.
2. `router_template.md` → generated `SKILL.md` stays a thin orchestrator: sample-context intake, unknown-fallback triage, links to `playbooks/INDEX.md` + `examples/INDEX.md`, visual-diagnosis capability gate, and the D-ASX-001 `process_*` hard-gate note. Long workflow/regime prose lives only in playbooks/examples.
3. Every playbook rule and archetype recommendation carries an **inline literature citation** (author/year + corpus pin and/or journal page from the harvest / `docs/code-change-citations.md` SSOT). No web search for pins at ship time. Units remain `nm` / `nm^-1`; metrics inform (except `process_*` per D-ASX-001).

**Justification:** Progressive disclosure keeps agent token budgets small while still grounding recommendations in the offline corpus; one owner for export (`get-skills`) avoids a second packaging path.

**Refs:** Mikhail approval (thin router); Project store `docs/saxs-knowledge-and-playbooks-design.md` Phase 2; harvest drafts under `saxsprocessing/knowledge_harvest/drafts/`; ASX-66.

---

### D-ASX-012 — `gate_pass` implemented on `process_*` (2026-11-18)

**Status:** accepted  
**Context:** Implements the optional bypass left open in D-ASX-001.

**Decision:** `process_monodisperse` and `process_polydisperse` take boolean ``gate_pass`` (default ``False``; CLI ``--gate-pass`` / ``--no-gate-pass``). Default behavior unchanged: failed ``allows_modeling`` skips DAMMIF / MIXTURE. With ``gate_pass=True``, modeling proceeds despite a failed gate; the same ``modeling_gate_skip_reason`` text is logged and returned as ``modeling_gate_advice`` (not silent). Missing/unusable GNOM ``.out`` remains a hard skip. Poly ``run_mixture`` stays an independent opt-in.

**Justification:** Settles D-ASX-001 naming/default; advice reuse matches existing EventBus + return-dict skip-reason patterns without a new diagnostic subsystem.

**Refs:** D-ASX-001; ASX-68; Mikhail assignment 2026-11-18.

---

## GUI

*(No new GUI decisions in this session.)*

---

### D-ASX-013 — Retire `plot`; add `fit_porod` + `analyze_loglog` (2026-11-19)

**Status:** accepted  
**Context:** ASX-69 / ASX-71 plot-retirement plan. `autosaxs.skill.plot` was a bag-of-plots PNG generator with no science. Guinier and Kratky already had specialised skills; Porod and log–log regime reading did not.

**Decision:**

1. **Delete** public skill `plot` completely (no compatibility wrapper). Keep `plot_2d` (detector images).
2. Add specialised skills **`fit_porod`** (high-q `B+A q^α` asymptote + Porod–Debye; α free primary; fixed−4 Porod constant secondary; omit `porod_volume_nm3` by default) and **`analyze_loglog`** (whole-curve regime tags: `rod_like` / `disk_like` / `coil_like` / `compact_porod_like` / `fractal_like` / `unclear`).
3. Shared math SSOT in `autosaxs.core.profile_features` (`powerlaw_exponent`, `powerlaw_segments`, `fit_porod_asymptote`, `porod_debye_plateau`).
4. Wire both into **`process_monodisperse`** after Guinier when Rg known (soft/advisory only). **No** liveview auto-wire in this landing (package-first).
5. Metrics stay **outside** `allows_modeling` / `gate_pass` hard gates (D-ASX-001 / D-ASX-012 unchanged).

**Narrow supersession of D-ASX-002:** D-ASX-002’s “no new user-facing diagnostic skill by default” is superseded **only** for this retirement — specialised Porod / log–log skills replace the bag skill `plot`. Still **no** grab-bag `profile_diagnostics` aggregator.

**Refs:** Mikhail-approved plan `docs/plot-retirement-porod-loglog-plan.md` (Project store); ASX-71; corpus pins Li 2016, Trewhella 2017, Pauw 2013, Jacques 2010, Rambo & Tainer 2011/2013.
