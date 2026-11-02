# Legacy interactive pipeline (removed)

Historical note for the former package `autosaxs.pipeline` (deleted). Prefer **skills** (`autosaxs.skill`), the `autosaxs` CLI, and liveview `plan_for` for new work.

## What it was

An event-driven multi-step orchestrator centered on `Controller` (`saxs_controller.py`). The controller held an `EventBus` and a matplotlib viewer; it never talked to stdin/GUI directly. Two interfaces subscribed to the bus and answered requests:

- **CLI** (`cli_interface.py`) — stdin/print and file-pattern monitoring
- **CustomTkinter GUI** (`gui.py` / `gui_interface.py`) — dialogs for the same EventBus contract (CustomTkinter was never a declared package dependency)

A thin script API (`api.py`) wired the bus for non-interactive runs: `fast_first_processing` (calibration → plots) and `slow_second_processing` (mixture / bodies / dammif-style late steps).

## Config and step lists

Available steps (order preserved) included: `calibration`, `integration`, `subtraction`, `simple_analysis`, `plots`, `mixture`, `polydispfit`, `bodies`, `dammif`, `ai_analysis`.

The default “protein_v0” subset omitted some late/AI steps. The last user selection was persisted under `~/.cache/autosaxs/latest_steps.yml` (`LATEST_STEPS_PATH` in `autosaxs.core.utils`) so CLI/GUI could offer “latest” as a default.

Working-directory layout and path groups came from a pipeline description / `Context` (raw frames, averaged curves, subtracted profiles, etc.). Step bodies mostly called the same skill functions used today (integrate, subtract, Guinier, GNOM, MIXTURE, BODIES, DAMMIF, …).

## Buffer ↔ sample pairing

2D frames were expected under `raw/` as `*_buffer.tif` / `*_sample.tif` (1D averaged paths used the same basename convention). Pairing used `map_sample_files_to_buffer_files` in `autosaxs.core.utils`: strip `_sample` / `_buffer` suffixes and match when the buffer basename is contained in the sample basename. Mis-alignment (overlaps or unpaired samples) surfaced as EventBus messages; the script API treated those as hard failures.

## LLM / `ai_analysis`

The optional `ai_analysis` step sent plot images plus ATSAS text results to a vision/text LLM via vendored `foreign/aiAssistantFramework`. Prompt templates lived (and still live) under `src/autosaxs/resources/prompts/visual/` (`saxs_1d.txt`, `guinier_plot.txt`, `kratky_plot.txt`, `loglog_plot.txt`). Those prompt files were **not** removed with the pipeline package.

## Why removed

Product orchestration moved to skills + liveview. The interactive Controller was deprecated, untested in the commit gate, and re-exported from `autosaxs.__init__` only for older lab scripts. Removing it avoids pulling a deprecated EventBus orchestrator on every `import autosaxs`.
