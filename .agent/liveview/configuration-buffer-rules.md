# Liveview Configuration (`buffer_rules`)

## Ownership

| Store | Owns |
|-------|------|
| `<dir>/config.conf` | Policy `buffer_rules` only (process-full compatible). Hierarchical: nearest file with a `buffer_rules` key **replaces** parent. |
| `.guisaxs_liveview/session.yaml` | Bindings: `buffer_overrides` (`Sample.key` → buffer path), `subtract_options`, calib/mask. **No `buffer_rules`.** |
| Left **Configuration** | Sole rules editor (comment-preserving text; YAML+schema validate on Save). |
| Left **Set buffer** | Per-sample overlay + session subtract options (not Fixed-for-all). |

## Processing chain

```
watchdir bind → seed root config.conf if missing → project root_rules
Sample → effective rules (nearest) → matcher → if overlay[key]: force PAIRED
CONFLICT → executor dispatch_hold → ApplicationModal Configuration editor
```

## Invariants

- Fixed everyone = literal catch-all rule in `config.conf` only.
- Overlay wins over rules for that sample (escape hatch for CONFLICT / UNMATCHED).
- Configuration does not drive analysis/arming.
- Coach: after calib pulse Set configuration until opened once; pulse Set buffer when current is UNMATCHED.
- Left order: Configuration → Calibrate → Set mask → Set buffer.

## End-user `config.conf` comments

Seed and silent session→root migrate share one teaching header (`_CONFIG_BUFFER_RULES_COMMENT_HEADER`): how to map sample globs to buffer globs, empty RHS, order, examples. Globs match **basename filenames including extension** (`match_basename` / `os.path.basename`), not stems. No migration/session.yaml/legacy banners in file text or Configuration UI copy.

Configuration editor uses a small `QSyntaxHighlighter` (keys / comments / strings; high-contrast colors local to the highlighter). App QSS must not set `color` on `QPlainTextEdit`/`QTextEdit` — that overrides `QTextCharFormat` and hides highlighting (palette supplies default text color).

## Code

- Matcher SSOT: `autosaxs/core/buffer_rules.py`
- Hierarchy/seed: `guisaxs_skills/liveview/session/watchdir_config.py`
- Overlay resolve: `guisaxs_skills/liveview/pipeline/buffer_match.py`
- Plan: `guisaxs_skills/liveview/pipeline/plan.py`
