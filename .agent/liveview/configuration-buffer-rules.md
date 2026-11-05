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
AWAITING_BUFFER (0 hits) → executor parks in memory `_awaiting_buffer` (no hold)
CONFLICT (multi-LHS / >1 hits) → dispatch_hold → escapable conflict dialog
```

Soft-wait: rematch all waiters after each job finish and on `configuration_changed` /
`buffer_changed`. Idle-with-waiters status: “Not all integrated. Waiting for buffer…”
(keep glow; no soft-wait toasts). CLI/`process-full` hard-fails `AWAITING_BUFFER`.

## Invariants

- Fixed everyone = literal catch-all rule in `config.conf` only.
- Overlay wins over rules for that sample (escape hatch for CONFLICT / UNMATCHED / soft-wait).
- Soft-wait is executor runtime only (not session / Sample / history).
- Configuration does not drive analysis/arming.
- **Effective buffer / match for current sample** is owned by `pipeline/buffer_match.py`
  (`resolve_liveview_match`, `effective_buffer_path_for_sample`):
  same curve-identity probe as `plan_for` (planned `int_<stem>.dat`, then source).
  `buffer_dat_path` is last-picked wizard seed only — Buffer pane preview shows it
  only when there is no current sample (never as a fake PAIRED path).
- Coach: after calib pulse Set configuration until opened once; then analysis.
  Never pulse the closed-panel **Set buffer** button from match kind (`UNMATCHED`
  includes buffer TIFFs / calibrants after a PAIRED sample run). Set buffer remains
  an unprompted overlay escape hatch; in-dialog coaching still runs while the wizard
  is open.
- Subtraction wizard opens when `buffer_ready()` and effective buffer path resolves for
  `SampleStore.current` (rules or overlay) — never requires a Set-buffer hint.
- Left order: Configuration → Calibrate → Set mask → Set buffer.

## End-user `config.conf` comments

Seed and silent session→root migrate share one teaching header (`_CONFIG_BUFFER_RULES_COMMENT_HEADER`): how to map sample globs to buffer globs, empty RHS, order, examples. Globs match **basename filenames including extension** (`match_basename` / `os.path.basename`), not stems. No migration/session.yaml/legacy banners in file text or Configuration UI copy.

Configuration editor uses a small `QSyntaxHighlighter` (keys / comments / strings; high-contrast colors local to the highlighter). App QSS must not set `color` on `QPlainTextEdit`/`QTextEdit` — that overrides `QTextCharFormat` and hides highlighting (palette supplies default text color).

## Code

- Matcher SSOT: `autosaxs/core/buffer_rules.py`
- Hierarchy/seed: `guisaxs_skills/liveview/session/watchdir_config.py`
- Liveview effective match + buffer path: `guisaxs_skills/liveview/pipeline/buffer_match.py`
- Plan: `guisaxs_skills/liveview/pipeline/plan.py` (raises CONFLICT / AWAITING on that match)
