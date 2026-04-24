import os
from typing import Any, Optional

from .utils import get_pipeline_paths, load_config, save_config


class Context:
    def __init__(self):
        """
        Lightweight context for pipeline configuration and paths.

        If pipe_descr_path is provided, directory structure is interpreted using
        the pipeline description file (original behavior).

        If pipe_descr_path is None, only configuration is loaded from
        '<directory>/config.conf' and no automatic path groups are created.
        """
        self.directory = None
        self.pipe_descr_path = None
        # self.interface = interface
        self.config_path = None

        self.config = None
        
        self.paths = {}
        # self.group_names = []
        # self.path_iterators = []

    def set_directory(self, directory):
        assert os.path.exists(directory)
        self.directory = directory
    
    def set_from_description(self, pipe_descr_path):
        assert self.directory is not None
        self.paths = get_pipeline_paths(pipe_descr_path, self.directory, check=False)
        # self.group_names = list(self.paths.keys())
        # self.path_iterators = [0] * len(self.group_names)
        
        assert 'config' in self.paths
        config_path = self.paths["config"][0][0]
        self.set_config(config_path)
    
    def set_config(self, config_path):
        self.config_path = config_path
        self.config = load_config(self.config_path)

    # def get_path(self, group_name, obligatory: bool = False, query: Optional[str] = None,
    #              pattern: str = "*", interaction_mode: str = "offline"):
    #     assert interaction_mode in ("online", "offline")

    #     # If no pipeline description was provided, path groups are unavailable
    #     if not self.paths:
    #         raise RuntimeError("Context was created without a pipeline description; path groups are unavailable.")

    #     if interaction_mode == "offline":
    #         idx0 = self.group_names.index(group_name)
    #         idx1 = self.path_iterators[idx0]
    #         group_paths = self.paths[group_name][0]
    #         if idx1 < len(group_paths):
    #             ret = group_paths[idx1]
    #             self.path_iterators[idx0] += 1
    #             return ret
    #         else:
    #             return ""

    #     if interaction_mode == "online":
    #         assert self.interface is not None, "Interface must be provided for online interaction mode"
    #         ret = self.interface.wait_for_file(
    #             self.directory,
    #             obligatory=obligatory,
    #             query=query,
    #             filepattern=pattern,
    #         )
    #         if ret:
    #             assert len(ret) == 1
    #             self.paths[group_name][0].append(ret[0])
    #         return ret

    def append_path(self, group_name, path):
        assert self.directory is not None
        if group_name not in self.paths:
            self.paths[group_name] = [[], (1, float("inf")), os.path.join(self.directory, "*")]
        paths = self.paths[group_name][0]
        if path not in paths:  # do not allow duplicate paths
            paths.append(path)
    
    def extend_paths(self, group_name, paths):
        assert self.directory is not None
        if group_name not in self.paths:
            self.paths[group_name] = [[], (1, float("inf")), os.path.join(self.directory, "*")]
        self.paths[group_name][0].extend(paths)

    def __getitem__(self, keys):
        if not isinstance(keys, tuple):
            keys = (keys,)
        
        if keys[0] == "paths":
            assert self.paths is not None
            assert len(keys) >= 2, "Paths group name is required"
            paths_group = keys[1]
            if paths_group not in self.paths:
                assert len(keys) == 2
                return []
            group_paths = self.paths[paths_group][0]
            if len(keys) == 3:
                idx = keys[2]
                return group_paths[idx]
            return group_paths

        assert self.config is not None
        ret = self.config
        for k in keys:
            ret = ret[k]

        return ret

    def __setitem__(self, keys, value):
        assert self.config is not None
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
        assert self.config is not None
        keys = list(keys)

        conf = self.config
        for k in keys:
            if k not in conf:
                conf[k] = {}
            conf = conf[k]

        conf.update(values)
        save_config(self.config, self.config_path)

    def __contains__(self, key):
        assert self.config is not None
        return key in self.config


