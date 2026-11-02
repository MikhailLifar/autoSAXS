# CLI ↔ GUI contract (P0)

## Purpose

Single request/result contract between `RunRequest` / `SkillRunner` and `autosaxs.cli.cli`.

## Owners

- **Argv codec:** `autosaxs.cli.request_codec.options_to_argv` (used by `RunRequest.cli_argv`).
- **CLI argparse:** `autosaxs.cli.cli._add_skill_subparser` — bools are `BooleanOptionalAction`; `use_cache` is `--cache`/`--no-cache`.
- **Result frame:** CLI writes framed JSON via `format_result_frame`; GUI parses only via `guisaxs_skills.logic.result_parser.parse_skill_result_stdout` (no `key=value` scan).
- **Outcome:** `RunOutcome.process_ok` vs `RunOutcome.science_ok` (`SkillRunner`); Liveview executor finishes the queue on science fail without treating it as a process crash.

## Invariants

- Cache polarity is always explicit in argv (`--cache` or `--no-cache`).
- Bool options always emit `--flag` or `--no-flag`.
- Human stdout outside `<<<AUTOSAXS_RESULT_JSON>>>` … `<<<END_AUTOSAXS_RESULT_JSON>>>` is ignored by the GUI parser.
- `process_ok` = subprocess exit success; `science_ok` = GNOM/ATSAS fit validity when applicable (else true when process_ok for ordinary skills).

## Tests

`tests/must-run/skills/test_cli_request_roundtrip.py`
