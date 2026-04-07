from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt5.QtWidgets import QCheckBox, QFormLayout, QGroupBox, QLineEdit, QWidget, QVBoxLayout

from ..core.models import RunRequest, SkillMeta
from ..logic.path_normalize import normalize_pathish
from .path_field import PathField


class SkillForm(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._meta: Optional[SkillMeta] = None
        self._pos_fields: List[PathField] = []
        self._opt_fields: Dict[str, QWidget] = {}

        self._copy_inputs = QCheckBox("Copy inputs into working directory")

        self._pos_group = QGroupBox("Inputs")
        self._pos_layout = QFormLayout(self._pos_group)

        self._opt_group = QGroupBox("Options")
        self._opt_layout = QFormLayout(self._opt_group)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._pos_group)
        lay.addWidget(self._opt_group)
        lay.addWidget(self._copy_inputs)

    def copy_inputs_enabled(self) -> bool:
        return self._copy_inputs.isChecked()

    def set_skill(self, meta: SkillMeta, *, default_output_dir: str) -> None:
        self._meta = meta
        self._clear_layout(self._pos_layout)
        self._clear_layout(self._opt_layout)
        self._pos_fields = []
        self._opt_fields = {}

        for p in meta.positional_params:
            f = PathField(mode="any")
            self._pos_fields.append(f)
            self._pos_layout.addRow(p.name, f)

        # Options: always include output_dir + use_cache in UI (even if skill has them as optional/kwonly)
        output = PathField(mode="dir")
        output.set_text(default_output_dir)
        self._opt_fields["output_dir"] = output
        self._opt_layout.addRow("output_dir", output)

        use_cache = QCheckBox("")
        # Default: caching disabled (user can opt-in).
        use_cache.setChecked(False)
        self._opt_fields["use_cache"] = use_cache
        self._opt_layout.addRow("Use cache", use_cache)

        for opt in meta.option_params:
            if opt.name in ("output_dir", "use_cache"):
                continue
            # Minimal typing: strings/numbers as line edits, booleans as checkboxes when default is bool.
            if isinstance(opt.default, bool):
                cb = QCheckBox(opt.name)
                cb.setChecked(bool(opt.default))
                self._opt_fields[opt.name] = cb
                self._opt_layout.addRow(opt.name, cb)
            else:
                le = QLineEdit()
                if opt.default is not None:
                    le.setText(str(opt.default))
                self._opt_fields[opt.name] = le
                self._opt_layout.addRow(opt.name, le)

    def state(self) -> dict:
        return {
            "skill_name": self._meta.name if self._meta else None,
            "copy_inputs": self._copy_inputs.isChecked(),
            "positional": [f.state() for f in self._pos_fields],
            "options": {k: self._widget_state(v) for k, v in self._opt_fields.items()},
        }

    def set_state(self, state: dict) -> None:
        if not state:
            return
        self._copy_inputs.setChecked(bool(state.get("copy_inputs", False)))
        pos_states = state.get("positional") or []
        for f, s in zip(self._pos_fields, pos_states):
            if isinstance(s, dict):
                f.set_state(s)
        opt_states = state.get("options") or {}
        if isinstance(opt_states, dict):
            for k, v in opt_states.items():
                w = self._opt_fields.get(k)
                if w is not None:
                    self._set_widget_state(w, v)

    def build_request(self) -> RunRequest:
        reqs = self.build_requests()
        if len(reqs) != 1:
            raise ValueError("This input represents multiple files; use Run to execute as a batch.")
        return reqs[0]

    def build_requests(self) -> List[RunRequest]:
        if not self._meta:
            raise ValueError("No skill selected")

        # Support: multi-file DnD in exactly one positional field => run skill in a loop.
        pos_lists: List[List[str]] = []
        for f in self._pos_fields:
            items = [normalize_pathish(p) for p in f.paths() if normalize_pathish(p)]
            pos_lists.append(items)

        if any(len(items) == 0 for items in pos_lists):
            raise ValueError("All positional inputs must be provided")

        multi_fields = [i for i, items in enumerate(pos_lists) if len(items) > 1]
        if len(multi_fields) > 1:
            raise ValueError("Multiple multi-file inputs are not supported yet. Use one multi-file drop at a time.")

        options: Dict[str, Any] = {}
        for k, w in self._opt_fields.items():
            if isinstance(w, PathField):
                options[k] = normalize_pathish(w.text())
            elif isinstance(w, QCheckBox):
                options[k] = w.isChecked()
            elif isinstance(w, QLineEdit):
                raw = w.text().strip()
                if raw == "":
                    continue
                options[k] = raw

        if not multi_fields:
            positional = [items[0] for items in pos_lists]
            return [RunRequest(skill_name=self._meta.name, positional=positional, options=options)]

        idx = multi_fields[0]
        reqs: List[RunRequest] = []
        for p in pos_lists[idx]:
            positional = [items[0] for items in pos_lists]
            positional[idx] = p
            reqs.append(RunRequest(skill_name=self._meta.name, positional=positional, options=dict(options)))
        return reqs

    @staticmethod
    def _clear_layout(layout: QFormLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                break
            w = item.widget()
            if w is not None:
                w.setParent(None)

    @staticmethod
    def _widget_state(w: QWidget):
        if isinstance(w, PathField):
            return w.state()
        if isinstance(w, QCheckBox):
            return bool(w.isChecked())
        if isinstance(w, QLineEdit):
            return w.text()
        return None

    @staticmethod
    def _set_widget_state(w: QWidget, value) -> None:
        if isinstance(w, PathField) and isinstance(value, dict):
            w.set_state(value)
        elif isinstance(w, QCheckBox):
            w.setChecked(bool(value))
        elif isinstance(w, QLineEdit):
            w.setText("" if value is None else str(value))

