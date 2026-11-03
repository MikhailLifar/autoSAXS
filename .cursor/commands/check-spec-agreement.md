# check-spec-agreement

Specs plus **code under `src/`** are the source of truth. Prefer updating a drifted doc over inventing parallel APIs.

Primary docs:
- `docs/paradigm/skills.md` — skills package contract
- `docs/architecture/liveview/overview.md` — liveview architecture (concepts, owners, plan, code map)
- `docs/architecture/liveview/behavior.md` — liveview product/UX
- `docs/architecture/liveview/invariants.md` — liveview hard invariants
- `docs/architecture/guisaxs-skills.md` — skills GUI eng contract
- `docs/README.md` — docs role index
- `AGENTS.md` — navigation map

Not a single line of product code should contradict these. Not any part of UX, architectural pattern, or significant feature should be missing from the docs that own it. Search for discrepancies between the docs and the codebase and report them (fix docs when the code is intentionally SSOT after a deliberate change).
