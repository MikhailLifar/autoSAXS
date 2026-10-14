# Modeling mini-app session controls

## Ownership

| Concept | Owner |
|---------|--------|
| Sample paths (profile, GNOM `.out`, output dir), catalogs, artifacts | Liveview → `ModelingContext` IPC push → `apply_context` |
| User run knobs (`n_runs`, mixture max_nph/r_max/q/init, …) | **Modeling app session** (spinboxes) |
| Auto/Manual | `ModelingAutoMode` in the child |
| Confirm run-params | Current UI values (`_options_for_confirm`) |

## Invariant

`apply_context` / context YAML push may update paths and reload on-disk catalogs. It must **not** clobber session-owned knobs or rewrite them from liveview session defaults or the new sample’s `*_run_params.yml`.

`options` in `ModelingContext` may seed knobs only on first bind: `apply_context(..., seed_controls=True)` from window `__init__`. Later pushes use `seed_controls=False` (default).

## Why it matters

Busy sample A + pending Confirm for B → after A finishes, deferred context re-push for B used to reset `n_runs` to liveview default (1). Pending flush then Confirmed B with the wrong params.

## Related

- Confirm protocol: Project store `docs/modeling-confirm-protocol.md`
- Code: `modeling/shape/window.py`, `modeling/dr/window.py`, `liveview/modeling_children.py`
