# Scenario: sample switch while GNOM Adjust is open

Maps:
- Correct bind: [`gnom-adjust-bind`](../diagrams/gnom-adjust-bind.mmd)
- BUG overlay: [`scenario-mono-adjust-bug`](../diagrams/scenario-mono-adjust-bug.mmd) / PNG `generated/scenario-mono-adjust-bug.png`
- FIX overlay: [`scenario-mono-adjust-fix`](../diagrams/scenario-mono-adjust-fix.mmd) / PNG `generated/scenario-mono-adjust-fix.png`

Invariants: [`../invariants.md`](../invariants.md).

## Setup

1. Sample **A** is current; mono right pane shows A’s profile + disk GNOM `.out` + passport.
2. User opens **GNOM Adjust** → `_push_gnom_adjust_context` binds Adjust to A (disk-seeded params; no auto preview).
3. User switches history to sample **B** (or intake/boarding updates current).

## Correct path

```text
SampleStore.current → B
  → present_right(DISK|LIVE)
    → mono presenter apply_bundle / load_from_disk  (profile/out/quality = B)
      → if Adjust visible: _push_gnom_adjust_context
        → GnomAdjust.set_context(profile=B, gnom_out=B, working=disk(B), …)
        → plots/passport match mono pane for B
```

Session `monodisperse_wizard_params` may still hold keys from A’s refine; they may **fill gaps only**, never override disk identity fields that exist for B.

## Failure class (pre-fix)

- `apply_bundle` / `load_from_disk` updated mono presenter to B but **did not** rebind open Adjust → Adjust still plotted A (or ran preview from session params from A).
- Or Adjust `set_context` always called `_run_preview()`, replacing disk `.out` visuals with a session-param GNOM run.

## Poly twin

Same scenario for **sizes Adjust** via `_push_sizes_adjust_context` after poly `apply_bundle` / `load_from_disk` / visible ingest.
