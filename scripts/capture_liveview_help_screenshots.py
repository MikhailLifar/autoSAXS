#!/usr/bin/env python3
"""
Capture annotated screenshots for guisaxs-liveview in-app help.

Drives the real PyQt GUI with validation fixtures (same paths as
tests/test_guisaxs_liveview.py), grabs windows via QWidget.grab(), and draws
red rectangles around the controls named in each help recipe.

Usage (needs a display; windows may flash on the desktop):

  /home/mikl/.conda/envs/dev_autosaxs/bin/python \\
    autosaxs/scripts/capture_liveview_help_screenshots.py

Outputs PNGs under:
  autosaxs/src/autosaxs/resources/help/guisaxs_liveview/html/assets/

After regenerating assets, touch the help manifest (this script does) so the
in-app help cache under ~/.cache/autosaxs/help/guisaxs_liveview/ refreshes.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

# Package imports: prefer installed env; fall back to src/ layout.
_SCRIPT_DIR = Path(__file__).resolve().parent
_AUTOSAXS_ROOT = _SCRIPT_DIR.parent
_SRC = _AUTOSAXS_ROOT / "src"
_TESTS = _AUTOSAXS_ROOT / "tests"
for _p in (_SRC, _AUTOSAXS_ROOT, _TESTS):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

WORKSPACE_ROOT = _AUTOSAXS_ROOT.parent
VALIDATION_DIR = WORKSPACE_ROOT / "validation"
VALIDATION_RAW = VALIDATION_DIR / "raw"
WATCHDIR = WORKSPACE_ROOT / "test_liveview"
ASSETS_DIR = (
    _AUTOSAXS_ROOT
    / "src"
    / "autosaxs"
    / "resources"
    / "help"
    / "guisaxs_liveview"
    / "html"
    / "assets"
)
MANIFEST = (
    _AUTOSAXS_ROOT
    / "src"
    / "autosaxs"
    / "resources"
    / "help"
    / "guisaxs_liveview"
    / "manifest.yaml"
)

_TIMEOUT = float(os.environ.get("GUISAXS_LIVEVIEW_HELP_TIMEOUT", "900"))


def _process_events(app: Any) -> None:
    try:
        app.processEvents()
    except Exception:
        return


def _wait_until(app: Any, predicate, timeout_sec: float, *, step_sec: float = 0.05) -> bool:
    deadline = time.monotonic() + float(timeout_sec)
    while time.monotonic() < deadline:
        _process_events(app)
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(max(0.01, float(step_sec)))
    return False


def _settle(app: Any, sec: float = 0.4) -> None:
    deadline = time.monotonic() + max(0.0, float(sec))
    while time.monotonic() < deadline:
        _process_events(app)
        time.sleep(0.03)


def _wait_queue_idle(app: Any, win: Any, timeout_sec: float) -> bool:
    def _idle() -> bool:
        mid = getattr(win, "_middle", None)
        line = getattr(mid, "_status_line", None)
        if line is None or not hasattr(line, "text"):
            return False
        return (line.text() or "").strip() == "Idle"

    return _wait_until(app, _idle, timeout_sec, step_sec=0.05)


def _wait_runcontrols_idle(app: Any, controls: Any, timeout_sec: float) -> bool:
    def _idle() -> bool:
        lbl = getattr(controls, "_state", None)
        if lbl is None or not hasattr(lbl, "text"):
            return False
        return (lbl.text() or "").strip() == "Idle"

    return _wait_until(app, _idle, timeout_sec, step_sec=0.05)


def _rm_tree_contents(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except FileNotFoundError:
                pass


def _atomic_copy_into_watchdir(src: Path, watchdir: Path) -> Path:
    watchdir.mkdir(parents=True, exist_ok=True)
    if not src.is_file():
        raise FileNotFoundError(str(src))
    dest = watchdir / src.name
    fd, tmp = tempfile.mkstemp(prefix=dest.stem + "_", suffix=".part", dir=str(watchdir))
    os.close(fd)
    tmp_p = Path(tmp)
    try:
        shutil.copyfile(src, tmp_p)
        try:
            os.utime(tmp_p, None)
        except Exception:
            pass
        os.replace(str(tmp_p), str(dest))
        return dest
    finally:
        if tmp_p.exists():
            try:
                tmp_p.unlink()
            except Exception:
                pass


def _set_pathfield_text_by_label(form: Any, *, label: str, text: str) -> bool:
    try:
        from guisaxs_skills.ui.path_field import PathField

        meta = getattr(form, "_meta", None)
        if meta is not None:
            pos_params = getattr(meta, "positional_params", [])
            widgets = getattr(form, "_pos_widgets", [])
            for i, p in enumerate(pos_params):
                if str(getattr(p, "name", "")) == label and i < len(widgets):
                    w = widgets[i]
                    if isinstance(w, PathField):
                        w.set_text(text)
                        return True
        opt_fields = getattr(form, "_opt_fields", {}) or {}
        w2 = opt_fields.get(label)
        if isinstance(w2, PathField):
            w2.set_text(text)
            return True
    except Exception:
        return False
    return False


def _get_pathfield_text_by_label(form: Any, *, label: str) -> str:
    try:
        from guisaxs_skills.ui.path_field import PathField

        meta = getattr(form, "_meta", None)
        if meta is not None:
            pos_params = getattr(meta, "positional_params", [])
            widgets = getattr(form, "_pos_widgets", [])
            for i, p in enumerate(pos_params):
                if str(getattr(p, "name", "")) == label and i < len(widgets):
                    w = widgets[i]
                    if isinstance(w, PathField):
                        return (w.text() or "").strip()
        opt_fields = getattr(form, "_opt_fields", {}) or {}
        w2 = opt_fields.get(label)
        if isinstance(w2, PathField):
            return (w2.text() or "").strip()
    except Exception:
        return ""
    return ""


def _set_form_text_field(form: Any, *, name: str, text: str) -> bool:
    try:
        from PyQt5.QtWidgets import QLineEdit

        opt_fields = getattr(form, "_opt_fields", {}) or {}
        w = opt_fields.get(name)
        if isinstance(w, QLineEdit):
            w.setText(str(text))
            return True
    except Exception:
        return False
    return False


def _qpixmap_to_pil(pix: Any):
    from PIL import Image
    from PyQt5.QtGui import QImage

    qimg = pix.toImage().convertToFormat(QImage.Format_RGBA8888)
    w, h = int(qimg.width()), int(qimg.height())
    ptr = qimg.bits()
    ptr.setsize(int(qimg.byteCount()))
    return Image.frombuffer("RGBA", (w, h), bytes(ptr), "raw", "RGBA", 0, 1).copy()


def capture_annotated(
    app: Any,
    root: Any,
    out_path: Path,
    targets: Sequence[Any] = (),
    *,
    settle_sec: float = 0.35,
) -> None:
    """Grab ``root`` and draw red rectangles around ``targets`` (child widgets)."""
    from PIL import ImageDraw
    from PyQt5.QtCore import QPoint

    out_path.parent.mkdir(parents=True, exist_ok=True)
    root.show()
    root.raise_()
    root.activateWindow()
    _settle(app, settle_sec)
    pix = root.grab()
    img = _qpixmap_to_pil(pix)
    draw = ImageDraw.Draw(img)
    dpr = float(root.devicePixelRatioF()) if hasattr(root, "devicePixelRatioF") else 1.0
    for w in targets:
        if w is None:
            continue
        try:
            if not w.isVisible():
                continue
            tl = w.mapTo(root, QPoint(0, 0))
            x0 = int(tl.x() * dpr) - 3
            y0 = int(tl.y() * dpr) - 3
            x1 = int((tl.x() + max(1, w.width())) * dpr) + 3
            y1 = int((tl.y() + max(1, w.height())) * dpr) + 3
            for i in range(5):
                draw.rectangle([x0 - i, y0 - i, x1 + i, y1 + i], outline=(220, 48, 48, 255))
        except Exception:
            continue
    img.convert("RGB").save(out_path, format="PNG", optimize=True)
    print(f"wrote {out_path.name} ({img.size[0]}x{img.size[1]})")


def _subtract_q_range() -> tuple[float, float]:
    try:
        import yaml

        cfg_data = yaml.safe_load((VALIDATION_DIR / "config.conf").read_text(encoding="utf-8"))
        sub = (cfg_data or {}).get("subtract") if isinstance(cfg_data, dict) else None
        if isinstance(sub, dict) and sub.get("q_min") is not None and sub.get("q_max") is not None:
            return float(sub["q_min"]), float(sub["q_max"])
    except Exception:
        pass
    return 4.5, 5.5


def _touch_manifest() -> None:
    if MANIFEST.is_file():
        now = time.time()
        os.utime(MANIFEST, (now, now))
        print(f"touched {MANIFEST}")


def main() -> int:
    if not VALIDATION_DIR.is_dir():
        raise SystemExit(
            f"Validation directory not found: {VALIDATION_DIR}. "
            "Run: python scripts/setup_validation_data.py"
        )

    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication

    from guisaxs_skills.liveview.window import LiveviewMainWindow

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    _rm_tree_contents(WATCHDIR)

    created_app = QApplication.instance() is None
    app = QApplication.instance() or QApplication([])
    win = LiveviewMainWindow(watchdir=WATCHDIR)
    win.showMaximized()
    _settle(app, 0.6)

    left = win._left  # noqa: SLF001
    middle = win._middle  # noqa: SLF001
    right = win._right  # noqa: SLF001
    timeout = _TIMEOUT

    try:
        # --- Main: overview + early highlights (pre-calibration)
        capture_annotated(app, win, ASSETS_DIR / "main_overview.png")
        capture_annotated(
            app, win, ASSETS_DIR / "main_set_calibration.png", [left._cal_open]  # noqa: SLF001
        )
        capture_annotated(
            app,
            win,
            ASSETS_DIR / "main_drop_tif.png",
            [middle.drop_canvas_host(), middle.drop_hint_canvas()],
        )
        capture_annotated(
            app, win, ASSETS_DIR / "main_analysis_icons.png", [right._btn_mono, right._btn_poly]  # noqa: SLF001
        )
        capture_annotated(
            app, win, ASSETS_DIR / "main_mono_icon.png", [right._btn_mono]  # noqa: SLF001
        )
        capture_annotated(
            app, win, ASSETS_DIR / "main_poly_icon.png", [right._btn_poly]  # noqa: SLF001
        )

        # --- Calibration wizard (fill paths first so shots show real image/mask)
        QTest.mouseClick(left._cal_open, Qt.LeftButton)  # noqa: SLF001
        assert left._cal_wizard is not None  # noqa: SLF001
        wiz = left._cal_wizard  # noqa: SLF001
        _settle(app, 0.5)

        calib = VALIDATION_RAW / "AgBh700_96.9_calib.tif"
        mask = VALIDATION_DIR / "mask_fti2d_1225.msk"
        for p in (calib, mask):
            if not p.is_file():
                raise FileNotFoundError(p)

        form = wiz._form  # noqa: SLF001
        assert _set_pathfield_text_by_label(form, label="calibrant_image", text=str(calib))
        try:
            getattr(form, "_on_primary_path_expression_changed")()
        except Exception:
            pass
        _wait_until(
            app,
            lambda: bool(_get_pathfield_text_by_label(form, label="mask")),
            2.0,
            step_sec=0.05,
        )
        assert _set_pathfield_text_by_label(form, label="mask", text=str(mask))
        if _get_pathfield_text_by_label(form, label="config_path").strip():
            assert _set_pathfield_text_by_label(form, label="config_path", text="")
        _settle(app, 0.8)

        capture_annotated(app, wiz, ASSETS_DIR / "cal_overview.png")
        capture_annotated(
            app, wiz, ASSETS_DIR / "cal_create_mask.png", [wiz._btn_create_mask]  # noqa: SLF001
        )
        capture_annotated(
            app, wiz, ASSETS_DIR / "cal_run.png", [wiz._controls.run_button]  # noqa: SLF001
        )

        # --- Mask wizard (from Create mask), then close it
        QTest.mouseClick(wiz._btn_create_mask, Qt.LeftButton)  # noqa: SLF001
        _settle(app, 0.8)
        mw = wiz._mask_wizard  # noqa: SLF001
        assert mw is not None and mw.isVisible()
        capture_annotated(app, mw, ASSETS_DIR / "mask_overview.png")
        capture_annotated(app, mw, ASSETS_DIR / "mask_save.png", [mw._btn_save])  # noqa: SLF001
        mw.close()
        _wait_until(app, lambda: not mw.isVisible(), 3.0)

        # --- Run calibration
        QTest.mouseClick(wiz._controls.run_button, Qt.LeftButton)  # noqa: SLF001
        ok_cal = _wait_until(
            app, lambda: (WATCHDIR / "calibration" / "integrator").is_dir(), timeout
        )
        if not ok_cal:
            raise RuntimeError("Calibration did not produce calibration/integrator")
        if not _wait_runcontrols_idle(app, wiz._controls, timeout):
            raise RuntimeError("Calibration wizard did not become Idle")
        _settle(app, 1.0)
        wiz.close()
        _wait_until(app, lambda: not wiz.isVisible(), 3.0)
        if not _wait_queue_idle(app, win, timeout):
            raise RuntimeError("Queue not Idle after calibration")
        _settle(app, 1.0)

        capture_annotated(
            app, win, ASSETS_DIR / "main_set_buffer.png", [left._buf_open]  # noqa: SLF001
        )

        # --- Buffer TIFF → Set buffer wizard
        buffer_src = VALIDATION_RAW / "ihs27_buffer.tif"
        _atomic_copy_into_watchdir(buffer_src, WATCHDIR)
        int_buf = WATCHDIR / "averaged" / "int_ihs27_buffer.dat"
        if not _wait_until(
            app, lambda: int_buf.is_file() and int_buf.stat().st_size > 0, timeout
        ):
            raise RuntimeError(f"Buffer integration did not produce {int_buf}")
        if not _wait_queue_idle(app, win, timeout):
            raise RuntimeError("Queue not Idle after buffer")
        _settle(app, 1.0)

        QTest.mouseClick(left._buf_open, Qt.LeftButton)  # noqa: SLF001
        assert left._buf_wizard is not None  # noqa: SLF001
        bw = left._buf_wizard  # noqa: SLF001
        bform = bw._form  # noqa: SLF001
        assert _set_pathfield_text_by_label(bform, label="buffer_1d", text=str(int_buf))
        q_min, q_max = _subtract_q_range()
        assert _set_form_text_field(bform, name="q_min", text=str(q_min))
        assert _set_form_text_field(bform, name="q_max", text=str(q_max))
        _settle(app, 0.4)
        capture_annotated(
            app,
            bw,
            ASSETS_DIR / "main_buffer_wizard.png",
            [bw._apply],  # noqa: SLF001
        )
        QTest.mouseClick(bw._apply, Qt.LeftButton)  # noqa: SLF001
        if not _wait_until(app, lambda: win._state.buffer_dat_path is not None, timeout):  # noqa: SLF001
            raise RuntimeError("Buffer did not apply")
        _settle(app, 0.5)
        bw.close()
        _wait_until(app, lambda: not bw.isVisible(), 3.0)

        # --- Arm monodisperse, upload sample
        right.show_monodisperse_wizard()
        _settle(app, 0.5)
        sample_src = VALIDATION_RAW / "ihs27_95.9_sample.tif"
        _atomic_copy_into_watchdir(sample_src, WATCHDIR)
        int_sam = WATCHDIR / "averaged" / "int_ihs27_95.9_sample.dat"
        sub_out = WATCHDIR / "subtracted" / "sub_ihs27_95.9_sample.dat"
        if not _wait_until(
            app,
            lambda: int_sam.is_file()
            and int_sam.stat().st_size > 0
            and sub_out.is_file()
            and sub_out.stat().st_size > 0,
            timeout,
        ):
            raise RuntimeError("Sample integrate/subtract outputs missing")
        if not _wait_queue_idle(app, win, timeout):
            raise RuntimeError("Queue not Idle after sample")
        _settle(app, 1.5)

        # History / Process visible after processed files
        capture_annotated(
            app,
            win,
            ASSETS_DIR / "main_process_history.png",
            [middle._btn_hist_prev, middle._btn_hist_next, middle._btn_process],  # noqa: SLF001
        )

        # S + buffer plot (opens subtraction wizard on click), then the wizard itself
        capture_annotated(
            app,
            win,
            ASSETS_DIR / "main_s_buffer.png",
            [middle._compare_plot],  # noqa: SLF001
        )
        win._open_subtraction_wizard()  # noqa: SLF001
        _settle(app, 0.6)
        subw = win._sub_wizard  # noqa: SLF001
        if subw is None or not subw.isVisible():
            raise RuntimeError("Subtraction wizard did not open")
        capture_annotated(
            app,
            subw,
            ASSETS_DIR / "main_subtraction_wizard.png",
            [subw._scale, subw._apply],  # noqa: SLF001
        )
        subw.close()
        _wait_until(app, lambda: not subw.isVisible(), 3.0)

        # Monodisperse overview (armed window, preferably with plots)
        mono = right._mono_dialog  # noqa: SLF001
        if mono is None:
            right.show_monodisperse_wizard()
            mono = right._mono_dialog  # noqa: SLF001
        assert mono is not None
        capture_annotated(app, mono, ASSETS_DIR / "mono_overview.png")

        # Polydisperse overview
        right.show_polydisperse_window()
        _settle(app, 0.6)
        poly = right._poly_dialog  # noqa: SLF001
        assert poly is not None
        capture_annotated(app, poly, ASSETS_DIR / "poly_overview.png")

        _touch_manifest()
        print("done")
        return 0
    finally:
        try:
            win.close()
        except Exception:
            pass
        _settle(app, 0.2)
        if created_app:
            app.quit()


if __name__ == "__main__":
    raise SystemExit(main())
