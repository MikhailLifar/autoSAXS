import os
from typing import Any, Optional

from utils import get_pipeline_paths, load_config, save_config


class Context:
    def __init__(self, directory: str, pipe_descr_path: Optional[str] = None, interface: Optional[Any] = None):
        """
        Lightweight context for pipeline configuration and paths.

        If pipe_descr_path is provided, directory structure is interpreted using
        the pipeline description file (original behavior).

        If pipe_descr_path is None, only configuration is loaded from
        '<directory>/config.conf' and no automatic path groups are created.
        """
        self.directory = directory
        self.pipe_descr_path = pipe_descr_path
        self.interface = interface

        if pipe_descr_path is not None:
            # Original behavior: infer paths from pipeline description file
            self.paths = get_pipeline_paths(self.pipe_descr_path, self.directory, check=False)
            self.group_names = list(self.paths.keys())
            self.path_iterators = [0] * len(self.group_names)

            self.config_path = self.paths["config"][0][0]
        else:
            # Minimal behavior: just use a config file in the working directory
            self.paths = {}
            self.group_names = []
            self.path_iterators = []
            self.config_path = os.path.join(self.directory, "config.conf")

        try:
            self.config = load_config(self.config_path)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Probably there is no configuration (.conf) file in the provided directory: {self.directory}"
            )

    def get_path(self, group_name, obligatory: bool = False, query: Optional[str] = None,
                 pattern: str = "*", interaction_mode: str = "offline"):
        assert interaction_mode in ("online", "offline")

        # If no pipeline description was provided, path groups are unavailable
        if not self.paths:
            raise RuntimeError("Context was created without a pipeline description; path groups are unavailable.")

        if interaction_mode == "offline":
            idx0 = self.group_names.index(group_name)
            idx1 = self.path_iterators[idx0]
            group_paths = self.paths[group_name][0]
            if idx1 < len(group_paths):
                ret = group_paths[idx1]
                self.path_iterators[idx0] += 1
                return ret
            else:
                return ""

        if interaction_mode == "online":
            assert self.interface is not None, "Interface must be provided for online interaction mode"
            ret = self.interface.wait_for_file(
                self.directory,
                obligatory=obligatory,
                query=query,
                filepattern=pattern,
            )
            if ret:
                self.paths[group_name][0].append(ret)
            return ret

    def append_path(self, group_name, path):
        if group_name not in self.paths:
            self.paths[group_name] = [[], (1, float("inf")), os.path.join(self.directory, "*")]
        self.paths[group_name][0].append(path)

    def __getitem__(self, keys):
        if not isinstance(keys, tuple):
            keys = (keys,)
        
        if keys[0] == "paths":
            assert len(keys) >= 2, "Paths group name is required"
            paths_group = keys[1]
            group_paths = self.paths[paths_group][0]
            if len(keys) == 3:
                idx = keys[2]
                return group_paths[idx]
            return group_paths

        ret: Any = self.config
        for k in keys:
            ret = ret[k]

        return ret

    def __setitem__(self, keys, value):
        if not isinstance(keys, tuple):
            keys = (keys,)

        keys, final_key = keys[:-1], keys[-1]
        data = self.config
        for k in keys:
            if k not in data:
                data[k] = {}
            data = data[k]
        data[final_key] = value

        save_config(self.config, self.config_path)

    def update_config(self, *keys, values: dict):
        keys = list(keys)

        conf = self.config
        for k in keys:
            if k not in conf:
                conf[k] = {}
            conf = conf[k]

        conf.update(values)
        save_config(self.config, self.config_path)

    def __contains__(self, key):
        return key in self.config


