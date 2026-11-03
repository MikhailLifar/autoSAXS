# Package imports (lightweight)

## Purpose

Keep `import autosaxs` and skills-console cold start free of ATSAS probes and eager skill-body imports.

## Ownership

| Concern | Owner |
|---------|--------|
| Soft ATSAS warn (once) | `skill/skill_wrap.warn_atsas_once` / `ensure_atsas_installed` (`@require_atsas`); also `autosaxs doctor` |
| Package re-exports | `autosaxs.__init__` lazy `__getattr__` (no eager utils/viewer/pipeline/skill) |
| Skill discovery names | `skill.list_skill_names` / `list_skills` (lazy entrypoints; no module import) |
| One skill load | `skill.get_skill(name)` |
| Console catalog | `guisaxs_skills.logic.skill_catalog`: `list_catalog_skill_names` + `skill_meta_for`; `CatalogTabs` loads meta on select |
| deps hub | `skill.deps` — eager: event_bus, skill_wrap, config; heavy symbols via `__getattr__` |

## Invariants

- `import autosaxs` does not spawn `dammif` / call `probe_atsas`.
- `list_skills()` does not `importlib.import_module` skill implementations.
- Skills console cold start imports only the selected skill body (default `calibrate`), not the full catalog.
- CLI argparse still uses `get_skill` (loads all when building the parser — intentional).

## Related

- Plan W3 / P2 in architecture update plan (store docs).
- Liveview wizards may still call `discover_skills()` (loads each meta once, cached).
