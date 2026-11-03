# Liveview import boundaries

## Purpose

Keep liveview layer ownership enforceable: UI panels present; session holds facts; pipeline plans/runs — without panels reaching into skill implementations or session/pipeline depending on Qt panels.

## Contracts (`.importlinter`)

Direct edges only (`allow_indirect_imports = True`); `exclude_type_checking_imports = True`.

| Edge | Rule |
|------|------|
| `liveview.ui.panels` ↛ `autosaxs.skill` | Panels use `liveview.services.skill_present` for readout helpers |
| `liveview.session` ↛ `ui.panels` / `ui.widgets` / `guisaxs_skills.ui` | Session may use `QtCore` signals; importlinter cannot forbid `PyQt5.QtWidgets` as an external subpackage |
| `liveview.pipeline` ↛ `ui.panels` | Pipeline does not import panel modules |

## Ownership

| Concern | Owner |
|---------|-------|
| Skill presentation helpers for panels | `liveview.services.skill_present` |
| Skill execution | `SkillRunner` → CLI (not panels) |

## Related

- Portable gate runs `lint-imports`: `helpers/run_portable_gate.sh`
- Architecture plan P4
