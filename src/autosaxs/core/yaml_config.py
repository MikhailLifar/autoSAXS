"""YAML config file load/save helpers.

Extracted from ``autosaxs.core.utils`` (P5 gradual split). Prefer importing from
this module for new code; ``utils`` re-exports these names temporarily.
"""
from __future__ import annotations

import yaml


def load_config(src_path):
    with open(src_path, 'r') as f:
        return yaml.safe_load(f)


def save_config(config, dest_path):
    with open(dest_path, 'w') as f:
        yaml.dump(config, f)


def update_config(config, config_file, *keys, values: dict):
    keys = list(keys)

    conf = config
    for k in keys:
        if k not in conf:
            conf[k] = {}
        conf = conf[k]
    
    conf.update(values)
    save_config(config, config_file)

