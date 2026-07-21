# GUISAXS Liveview

Desktop GUI that watches a folder for new SAXS `.tif` images, queues them, and runs the autosaxs live pipeline (calibration, integration, optional subtraction and analysis).

## Install

```sh
pip install "autosaxs[gui] @ git+https://github.com/MikhailLifar/autoSAXS.git@main"
```

Use a virtual environment (venv or conda) when possible.

## Run

```sh
guisaxs-liveview
```

Start from the directory you want to watch, or pick a folder via **File → Open watch directory…** in the app.

## Documentation

Full usage guide: **Help → guisaxs-liveview Help…** inside the application.

To upgrade: **Update → Update to latest version…** (or re-run the pip install command above).

## Help sources (developers)

Bundled HTML lives under `autosaxs/resources/help/guisaxs_liveview/`:

- `manifest.yaml` — table of contents
- `html/` — help pages
- `style/help.css` — shared styling

No build step is required; edit HTML or the manifest and reinstall (or run from a dev checkout).
