# Architecture extraction maps

Text-first maps of **guisaxs LiveView** ownership and signal paths.  
YAML is the SSOT; Mermaid (for Markdown) and colored Graphviz (for slides) are generated.

## Layout

```text
docs/architecture/
  README.md                 ← this file
  liveview/
    nodes.yaml              ← SSOT: nodes, edges, diagram ids, drilldowns
    invariants.md           ← ownership / Adjust rebind invariants
    owners.md               ← generated from YAML
    scenarios/
      sample-switch-open-adjust.md
    diagrams/               ← generated .mmd + .dot (committed)
    generated/              ← PNG binaries (gitignored; regenerate locally)
scripts/render_arch.py
```

Prose architecture (unchanged SSOT for narrative):  
[`docs/liveview_architecture.md`](../liveview_architecture.md),  
[`docs/liveview_session_sample_plan.md`](../liveview_session_sample_plan.md).

## Edge legend

| Kind | Color | Meaning |
|------|-------|---------|
| `data` | blue `#1f77b4` | Artifacts, profiles, skill results, paint payloads |
| `signal` | red `#d62728` | Control / rebind / UI→controller events |
| `state` | green `#2ca02c` | Identity or fact ownership |
| `exec` | orange `#ff7f0e` | Job / skill execution |
| `ui` | gray `#7f7f7f` | Panel chrome interactions |

Graphviz PNGs color edges by kind (legend in each diagram). Mermaid encodes kind in edge labels (`data: …`).

## Hierarchy

- **Top-level** (`top`): LiveView as black boxes (~watchdir → UI). Nodes with `drilldown:` link to a child diagram id — do **not** flatten the whole tree into one graph.
- **v1 drilldowns:**
  - `sample-presentation` — `SampleStore` → `present_right` → mono presenter / right pane
  - `gnom-adjust-bind` — Adjust rebind vs mono analysis identity (bug class); sizes Adjust noted as poly twin

Further tree nodes (ingest internals, planner phases, modeling IPC, …) are intentionally **not** built yet — add child diagrams when a bug or design task needs them.

## How to regenerate

```bash
# Prefer lab conda (has graphviz `dot`):
/home/mikl/.conda/envs/dev_autosaxs/bin/python scripts/render_arch.py

# Optional: also emit per-kind Mermaid views
/home/mikl/.conda/envs/dev_autosaxs/bin/python scripts/render_arch.py \
  --views data signal state exec ui

# Text-only (no PNG) if `dot` missing:
python scripts/render_arch.py --no-png
```

Requires: PyYAML; PNG output needs Graphviz `dot` on `PATH` or under `dev_autosaxs`.

After editing `nodes.yaml`, re-run the renderer and commit updated `diagrams/*.mmd` + `diagrams/*.dot` (+ `owners.md`). PNGs stay local under `generated/`.

## Index (v1)

| Diagram id | Purpose |
|------------|---------|
| `top` | Watchdir → ingest → session/store → plan/exec → middle/right UI |
| `sample-presentation` | Sample-tied right presentation chain |
| `gnom-adjust-bind` | Mono presenter ↔ GNOM Adjust rebind (+ sizes twin) |
