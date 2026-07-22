from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml


@dataclass
class HelpTocNode:
    title: str
    page: str = ""
    children: List["HelpTocNode"] = field(default_factory=list)

    def has_page(self) -> bool:
        return bool((self.page or "").strip())


def _node_from_mapping(data: Dict[str, Any]) -> HelpTocNode:
    title = str(data.get("title") or "").strip()
    page = str(data.get("page") or "").strip()
    raw_children = data.get("children")
    children: List[HelpTocNode] = []
    if isinstance(raw_children, list):
        for item in raw_children:
            if isinstance(item, dict):
                children.append(_node_from_mapping(item))
    return HelpTocNode(title=title, page=page, children=children)


def parse_help_manifest(manifest_path: Path) -> tuple[str, List[HelpTocNode]]:
    """Return (home_page_relative_path, toc_nodes) from manifest.yaml."""
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid help manifest: {manifest_path}")
    home = str(data.get("home") or "html/index.html").strip()
    toc_nodes: List[HelpTocNode] = []
    raw_toc = data.get("toc")
    if isinstance(raw_toc, list):
        for item in raw_toc:
            if isinstance(item, dict):
                toc_nodes.append(_node_from_mapping(item))
    return home, toc_nodes


def page_file_url(*, help_root: Path, page: str) -> str:
    rel = (page or "").strip().replace("\\", "/").lstrip("/")
    path = (help_root / rel).resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return path.as_uri()
