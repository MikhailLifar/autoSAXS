# `guisaxs-skills` (app)

`guisaxs-skills` is a single-window desktop GUI that acts as a **strict interface to `autosaxs` skills**: it lets you select a working directory, discover available skills, run them in an **isolated process** via the `autosaxs` CLI, and then inspect the produced artifacts (paths + image previews) without re-implementing any processing logic in the GUI.

---

## Main scenarios (general)

### Select a working directory (session start)

- On startup the app prompts you to **select a working directory**.
- The directory **may be non-empty**; the app will warn that:
  - with `use_cache=False` some skills may overwrite outputs, and
  - with `use_cache=True` cache hits may avoid recomputation.
- The selected working directory is shown at the top of the window as `Workdir: ...`.

### Select a skill (catalog)

- The left column contains the **skill catalog**, discovered dynamically from public functions in `autosaxs.skill` (excluding report skills).
- Selecting a skill updates the middle column:
  - the skill name (header),
  - the input form generated from the skill signature,
  - default `output_dir` (a skill-specific subdirectory under the working directory).
- The **“?” help button** shows the skill’s full docstring.

### Provide inputs and options (skill form)

- **Positional inputs** are shown in the `Inputs` group.
  - Each positional field accepts a file/dir/glob string and supports drag & drop + browsing + manual entry.
- **Options** are shown in the `Options` group.
  - `output_dir` is always present and defaults to a recommended subdirectory under the working directory.
  - `use_cache` is exposed as a checkbox. Note: in the current UI it defaults to **unchecked**, which means the app will pass `--no-cache` to the CLI (i.e. caching is **opt-in** in the GUI).
- **Copy inputs into working directory** (checkbox):
  - when enabled, file inputs outside the working directory are copied into `<workdir>/inputs/` (with de-duplication) and the skill is run against the copied paths;
  - glob expressions are kept as-is (the GUI does not expand them);
  - some option values that look like file paths may also be copied (directories are not copied).

### Run, watch logs, cancel

- Press **Run** to execute the selected skill.
  - The GUI launches a separate process: `python -m autosaxs.cli <skill> ...`.
  - `stdout`/`stderr` are streamed live into the log view.
- Press **Cancel** to terminate the running process (best-effort termination via `QProcess.terminate()`).
- **Multi-file inputs:** if you drop multiple files into an input field, the GUI encodes them into a single comma-separated path expression and invokes the skill **once**. The skill entry point expands the expression and performs any per-file iteration/batching internally.

### Inspect artifacts and previews

- After a run finishes, the right column shows:
  - **Artifact list**: key/value pairs parsed from the CLI output lines `key=value`. Values can be a single path or a list of paths.
  - **Preview panel**: selecting an artifact path shows a preview for common image formats (`.png`, `.jpg`, …) and also generates previews for `.dat`, `.tif/.tiff`, and `.csv` by rendering them into a temporary `.png`. Clicking the preview opens a larger viewer dialog.

### Where run records are stored

For traceability, the latest run metadata is written into the working directory:

- `<workdir>/runs/latest/request.yml` (skill name, positional args, options, timestamp)
- `<workdir>/runs/latest/stdout.log`
- `<workdir>/runs/latest/stderr.log`
- `<workdir>/runs/latest/result.yml` (parsed `key=value` outputs)

---

## Scenario: `calibrate` (typical workflow)

This is the common “create an integrator directory from a calibrant image” flow.

1. **Start the app** and choose a working directory.
2. In the catalog, select **`calibrate`**.
3. In `Inputs`, provide the required positional arguments (typically a calibrant 2D image; other required paths depend on the skill signature).
4. In `Options`:
   - set `output_dir` to a stable subdirectory (default: `<workdir>/calibration/`),
   - decide whether to enable `use_cache`.
5. Click **Run** and watch `stderr`/`stdout` in the log view.
6. After completion, use the **Artifacts** list to find paths produced by calibration (commonly an `integrator_dir` and plot/config outputs), and preview any `.png` artifacts.

Result: you get an **integrator directory** under your working directory that can be reused as an input to `integrate`.

---

## Scenario: `integrate` (use calibration to make 1D curves)

This is the common “integrate one or many 2D images into 1D curves” flow.

1. Ensure you have an integrator directory from a previous **`calibrate`** run (typically a path like `<workdir>/calibration/...`).
2. In the catalog, select **`integrate`**.
3. In `Inputs`:
   - provide the 2D input image(s) (a file path, a directory path, a glob expression, or a multi-file drop),
   - provide `integrator_dir` (the directory produced by `calibrate`).
4. In `Options`:
   - set `output_dir` (default: `<workdir>/averaged/`),
   - set `use_cache` as desired.
5. Click **Run**.
6. After completion, inspect the artifact roles for produced 1D curves and plot images, and preview any `.png` outputs in the right-side preview panel.

Result: you get integrated **1D curve files** (and optionally plots) inside the chosen `output_dir`, plus a persisted run record in `<workdir>/runs/latest/`.

