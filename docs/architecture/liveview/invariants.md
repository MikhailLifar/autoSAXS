# LiveView invariants (architecture maps)

Ownership cheat sheet and prose: [`docs/liveview_session_sample_plan.md`](../../liveview_session_sample_plan.md), [`docs/liveview_architecture.md`](../../liveview_architecture.md).  
Map SSOT: [`nodes.yaml`](nodes.yaml).

## Sample identity

1. **`SampleStore.current` is the sole sample identity** for anything sample-tied (middle paint for the selected file, right Guinier/GNOM/shape/sizes, modeling mini-apps).
2. **No usable analysis profile for that sample ⇒ empty sample-tied views** — never session `last_*`, a sticky prior profile, or the newest sibling under a shared modeling family dir.
3. **Left chrome is session-tied** (calib / buffer / mask). Arming / shape / mixture mode are **session preferences for next runs**, not a license to load another sample’s artifacts.

## Adjust rebind (mono vs GNOM I(q) bug class)

**Invariant:** While a sample-tied Adjust dialog is open (GNOM Adjust, sizes Adjust), it must stay bound to the **same analysis identity** the corresponding right pane shows for `SampleStore.current`:

| Identity field | Owner while viewing a sample |
|----------------|------------------------------|
| `profile_path` | Mono/poly **artifact presenter** (from `present_right` LIVE/DISK) |
| GNOM `.out` (`last_gnom_out`) | Presenter |
| Passport / quality (`last_gnom_result` / sizes quality) | Presenter |
| Working refine params on open / sample switch | Seeded from **disk** `.out` + quality; session `*_wizard_params` fill **gaps only** |

**Required rebind sites** (full `_push_*_adjust_context`, not a partial `.out` refresh):

- `apply_bundle`
- `load_from_disk`
- `ingest_skill_result` (when Adjust is visible)
- Open Adjust (`open_*_adjust_wizard`)

**Forbidden on bind / sample switch:**

- Leaving Adjust on a previous sample’s `profile_path` / `.out` while the mono/poly pane already shows another stem.
- Unconditional live `_run_preview()` at the end of Adjust `set_context` (that re-runs GNOM from session-global working params and can replace the disk `.out` the pane shows). Live preview starts only after the **user edits**.

**Allowed divergence:** After the user edits Adjust, preview may diverge from disk until Confirm / skill write-back. On the next sample switch or disk present, rebind resets to the new identity.

**Session wizard params** (`monodisperse_wizard_params`, `polydisperse_window_params`) are defaults / committed refine bags — **not** the source of truth for which sample’s I(q)/P(r) an open Adjust plots.

## GNOM-owned intensity curve

**Invariant:** While a disk GNOM `.out` is bound (mono/poly right pane or Adjust), I(q) experimental overlay and Adjust live preview must use the **intensity curve that produced that `.out`**, not a differently scaled session `subtracted/sub_*.dat`.

| Concept | Owner |
|---------|--------|
| GNOM `.out` + sibling `*_atsas.dat` | On-disk fit_distances / fit_sizes result |
| Session analysis profile (`subtracted/sub_*.dat`) | Presenter / `resolve_analysis_profile` — may be newer or differently buffered |

Helper: `gnom_owned_curve_path` in `ui/panels/right/gnom_overlays.py`.  
If session profile and owned `*_atsas.dat` disagree in absolute scale, painting session profile under the disk `.out` yields fake “current vs best (disk)” P(r) mismatch and I(0) Guinier vs P(r) disagreement until a preview/rebind uses the owned curve.

## Related bug writeup

Project store: `docs/mono-vs-gnom-iq-plot.md` (Adjust on ihs27 while mono showed ihs29).  
Deeper first-paint scale bug: `docs/gnom-adjust-owned-curve.md`.  
Code: `ui/panels/right/monodisperse/coordinator.py` (`_disk_gnom_working_params`, `_push_gnom_adjust_context`); poly twin `_push_sizes_adjust_context`; `gnom_owned_curve_path`.
