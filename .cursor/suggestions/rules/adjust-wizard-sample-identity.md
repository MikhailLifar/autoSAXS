# Observation
P(r)/GNOM Adjust (and sizes Adjust) can stay bound to a prior sample's profile / `.out` / passport while the mono/poly right pane already shows another sample. Session-global wizard params plus `set_context` → immediate live `_run_preview` made the I(q) overlay and passport disagree with the pane (e.g. ihs27 passport in Adjust vs ihs29 in mono).

# Proposed change
**Project rule** (or skill note for liveview adjust wizards): On open and on every sample rebind (`apply_bundle` / `load_from_disk` / skill ingest while visible), Adjust must take the presenter's current analysis identity (profile + disk GNOM out + quality). Do not leave a partial `show_from_gnom_out` refresh that keeps the old profile/handoff. Live preview only after user edits — never as the default bind step.
