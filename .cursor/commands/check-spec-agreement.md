# check-spec-agreement

Specs plus **code under `src/`** are the source of truth. Prefer updating a drifted doc over inventing parallel APIs.

Primary docs:
- `docs/skills_paradigm.md` — skills package contract
- `docs/liveview_architecture.md` — liveview architecture (full)
- `docs/liveview_session_sample_plan.md` — liveview ownership cheat sheet
- `docs/guisaxs_liveview_spec.md` — liveview product/UX
- `docs/guisaxs_skills_spec.md` — skills GUI product/UX
- `AGENTS.md` — navigation map

Not a single line of product code should contradict these. Not any part of UX, architectural pattern, or significant feature should be missing from the docs that own it. Search for discrepancies between the docs and the codebase and report them (fix docs when the code is intentionally SSOT after a deliberate change).
