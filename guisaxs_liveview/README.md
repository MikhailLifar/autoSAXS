# GUISAXS Liveview — usage (step list)

**What it does:** watches **one** folder. Each **new** `.tif` / `.tiff` there (or dropped on the middle 2D canvas) joins a **queue** and is processed in order.

---

## Installation and update to the latest version

You can install the liveview app using pip:

```sh
pip install "autosaxs[gui] @ git+http://hpc.nano.sfedu.ru:8080/mikhail/saxsprocessing.git@main"
```

You may want to do this inside a virtual environment (venv or conda).

---

## Procedure

0. Make sure a Python environment, where `autosaxs[gui]` is installed, is activated in your terminal.
1. Type **`guisaxs-liveview`**. The GUI starts.
2. If asked, **select a directory** in the opening file browser.  
   - Must exist and be **writable**.  
   - Cancel → app exits.  
   - Next time it may skip this if it still remembers a valid last folder.
3. You can feed TIFFs in two ways:
   - **Drag and drop** `.tif` / `.tiff` onto the **middle** panel (“Latest image (2D) — drop .tif here”), **or**
   - **Copy or save** files **directly into** that watch directory (not into subfolders).
5. Watch the **middle** column for queue status and the latest **2D** image.
6. **If calibration is required** → left column → **Set calibration** → follow the wizard (set calibrant image, config file and mask) → press "Run" → wait until finishes.
After calibration is set, the integration is done with calibrated geometry.
7. **If buffer subtraction is required** → **Set buffer** → choose buffer `.dat` and options in the wizard → **Apply**.
After buffer is set, automatic subtraction follows integration immediately for every new `.tif` file.
8. **If you need extra modeling / fits** → use the **right** column (toggles and actions such as fit distances, as available).
After you do fit_distances once, it is run automatically for every new `.tif` file.
9. **To quit** → close the window.

---

## Rules the watcher follows (so you are not surprised)

- Only the **top-level** watch directory is watched — **not** nested folders.
- Only files that appear **after** the app started count as “new” (plus **moves** into the folder). Old TIFFs already sitting there are **not** auto-queued. But you can drag-and-drop them to the middle column.
