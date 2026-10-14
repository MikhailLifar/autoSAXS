#!/usr/bin/env python3
"""Render LiveView architecture maps from YAML SSOT → Mermaid + Graphviz (+ PNG).

SSOT: docs/architecture/liveview/nodes.yaml
Outputs:
  docs/architecture/liveview/diagrams/<id>.{mmd,dot}
  docs/architecture/liveview/generated/<id>.png   (requires `dot`)
  docs/architecture/liveview/owners.md            (generated from YAML)

Usage:
  /home/mikl/.conda/envs/dev_autosaxs/bin/python scripts/render_arch.py
  python scripts/render_arch.py --no-png
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML required: pip/conda install pyyaml") from exc

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_YAML = REPO_ROOT / "docs/architecture/liveview/nodes.yaml"
DIAGRAMS_DIR = REPO_ROOT / "docs/architecture/liveview/diagrams"
GENERATED_DIR = REPO_ROOT / "docs/architecture/liveview/generated"
OWNERS_MD = REPO_ROOT / "docs/architecture/liveview/owners.md"

DEFAULT_KIND_COLORS = {
    "data": "#1f77b4",
    "signal": "#d62728",
    "state": "#2ca02c",
    "exec": "#ff7f0e",
    "ui": "#7f7f7f",
}


def load_ssot(path: Path) -> Dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"SSOT must be a mapping: {path}")
    return data


def kind_color(meta: Mapping[str, Any], kind: str) -> str:
    kinds = meta.get("edge_kinds") or {}
    entry = kinds.get(kind) or {}
    if isinstance(entry, dict) and entry.get("color"):
        return str(entry["color"])
    return DEFAULT_KIND_COLORS.get(kind, "#333333")


def kind_label(meta: Mapping[str, Any], kind: str) -> str:
    kinds = meta.get("edge_kinds") or {}
    entry = kinds.get(kind) or {}
    if isinstance(entry, dict) and entry.get("label"):
        return str(entry["label"])
    return kind


def _esc_dot(text: str) -> str:
    # Real newlines become Graphviz \\n; backslashes/quotes escaped first.
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def _esc_mmd(text: str) -> str:
    # Mermaid node labels: keep simple; avoid raw quotes / newlines.
    return text.replace('"', "'").replace("\n", " ")


def _node_label(node: Mapping[str, Any], nid: str, *, diagram_id: str) -> str:
    label = str(node.get("label") or nid)
    drill = node.get("drilldown")
    # Only advertise drilldown when this diagram is not already that child.
    if drill and str(drill) != diagram_id:
        label = f"{label}\n→ {drill}"
    return label


def render_dot(
    *,
    diagram_id: str,
    diagram: Mapping[str, Any],
    nodes: Mapping[str, Any],
    meta: Mapping[str, Any],
) -> str:
    rankdir = str(diagram.get("rankdir") or "LR")
    title = str(diagram.get("title") or diagram_id)
    include = list(diagram.get("include_nodes") or [])
    edges = list(diagram.get("edges") or [])
    clusters = list(diagram.get("clusters") or [])

    lines: List[str] = [
        "digraph {",
        f'  graph [rankdir={rankdir}, labelloc="t", label="{_esc_dot(title)}", '
        f'fontsize=16, nodesep=0.45, ranksep=0.55, splines=true, overlap=false];',
        '  node [shape=box, style="rounded,filled", fillcolor="#f7f7f7", '
        'fontname="Helvetica", fontsize=11];',
        '  edge [fontname="Helvetica", fontsize=9];',
        "",
    ]

    clustered: set = set()
    for i, cluster in enumerate(clusters):
        cname = str(cluster.get("id") or f"c{i}")
        clabel = str(cluster.get("label") or cname)
        members = list(cluster.get("nodes") or [])
        lines.append(f"  subgraph cluster_{cname} {{")
        lines.append(
            f'    label="{_esc_dot(clabel)}"; style="rounded,dashed"; '
            f'color="#bbbbbb"; fontsize=10;'
        )
        for nid in members:
            if nid not in include:
                continue
            clustered.add(nid)
            node = nodes.get(nid) or {}
            label = _node_label(node, nid, diagram_id=diagram_id)
            lines.append(f'    "{nid}" [label="{_esc_dot(label)}"];')
        lines.append("  }")
        lines.append("")

    for nid in include:
        if nid in clustered:
            continue
        node = nodes.get(nid) or {}
        label = _node_label(node, nid, diagram_id=diagram_id)
        lines.append(f'  "{nid}" [label="{_esc_dot(label)}"];')

    lines.append("")
    for edge in edges:
        src = edge["from"]
        dst = edge["to"]
        kind = str(edge.get("kind") or "data")
        elabel = str(edge.get("label") or "")
        color = kind_color(meta, kind)
        # Prefer xlabel so the kind tag does not stack on the same baseline as the prose.
        lines.append(
            f'  "{src}" -> "{dst}" '
            f'[color="{color}", fontcolor="{color}", '
            f'xlabel="{_esc_dot(elabel)}"];'
        )

    # Legend
    lines.append("")
    lines.append("  subgraph cluster_legend {")
    lines.append('    label="edge kinds"; fontsize=10; style="dashed"; color="#aaaaaa";')
    lines.append('    node [shape=plaintext, fillcolor="white", style=""];')
    legend_rows = []
    for kind in ("data", "signal", "state", "exec", "ui"):
        c = kind_color(meta, kind)
        legend_rows.append(
            f'<tr><td><font color="{c}">━━</font></td>'
            f'<td align="left"><font color="{c}">{_esc_dot(kind_label(meta, kind))}</font></td></tr>'
        )
    html = (
        '<<table border="0" cellborder="0" cellspacing="2">'
        + "".join(legend_rows)
        + "</table>>"
    )
    lines.append(f'    legend [label={html}];')
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_mermaid(
    *,
    diagram_id: str,
    diagram: Mapping[str, Any],
    nodes: Mapping[str, Any],
    meta: Mapping[str, Any],
    edge_filter: Optional[str] = None,
) -> str:
    """Mermaid flowchart. edge_filter=None → all kinds; else one kind."""
    rankdir = str(diagram.get("rankdir") or "LR")
    title = str(diagram.get("title") or diagram_id)
    include = list(diagram.get("include_nodes") or [])
    edges = list(diagram.get("edges") or [])
    if edge_filter:
        edges = [e for e in edges if str(e.get("kind")) == edge_filter]
        title = f"{title} [{edge_filter}]"

    direction = "LR" if rankdir.upper().startswith("L") else "TB"
    lines: List[str] = [
        f"%% {title}",
        f"%% generated from nodes.yaml — do not edit by hand",
        f"flowchart {direction}",
    ]
    for nid in include:
        node = nodes.get(nid) or {}
        label = _esc_mmd(str(node.get("label") or nid))
        drill = node.get("drilldown")
        if drill and str(drill) != diagram_id:
            label = f"{label}<br/>→ {drill}"
        lines.append(f'  {nid}["{label}"]')

    for edge in edges:
        src = edge["from"]
        dst = edge["to"]
        kind = str(edge.get("kind") or "data")
        elabel = _esc_mmd(str(edge.get("label") or kind))
        # Mermaid cannot color edges as richly as Graphviz; encode kind in label.
        lines.append(f"  {src} -->|{kind}: {elabel}| {dst}")

    return "\n".join(lines) + "\n"


def write_owners_md(ssot: Mapping[str, Any], path: Path) -> None:
    nodes = ssot.get("nodes") or {}
    lines = [
        "<!-- Generated by scripts/render_arch.py from nodes.yaml — do not edit by hand. -->",
        "",
        "# LiveView owners (from architecture YAML)",
        "",
        "| Node | Owns | Path | Drilldown |",
        "|------|------|------|-----------|",
    ]
    for nid, node in nodes.items():
        owns = ", ".join(node.get("owns") or []) or "—"
        p = node.get("path") or "—"
        drill = node.get("drilldown") or "—"
        label = node.get("label") or nid
        lines.append(f"| `{nid}` ({label}) | {owns} | `{p}` | {drill} |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def find_dot() -> Optional[str]:
    # Prefer conda env used for autosaxs work, then PATH.
    candidates = [
        Path("/home/mikl/.conda/envs/dev_autosaxs/bin/dot"),
        Path(shutil.which("dot") or ""),
    ]
    for c in candidates:
        if c and c.is_file():
            return str(c)
    return None


def render_png(dot_bin: str, dot_path: Path, png_path: Path) -> None:
    png_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [dot_bin, "-Tpng", str(dot_path), "-o", str(png_path)],
        check=True,
    )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yaml", type=Path, default=DEFAULT_YAML)
    ap.add_argument("--no-png", action="store_true", help="Skip Graphviz PNG render")
    ap.add_argument(
        "--views",
        nargs="*",
        default=None,
        help="Also emit Mermaid views filtered by edge kind (data signal state exec ui)",
    )
    args = ap.parse_args(argv)

    ssot = load_ssot(args.yaml)
    meta = ssot.get("meta") or {}
    nodes = ssot.get("nodes") or {}
    diagrams = ssot.get("diagrams") or {}

    DIAGRAMS_DIR.mkdir(parents=True, exist_ok=True)
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    write_owners_md(ssot, OWNERS_MD)

    dot_bin = None if args.no_png else find_dot()
    if not args.no_png and not dot_bin:
        print("warning: `dot` not found; writing .mmd/.dot only", file=sys.stderr)

    for diagram_id, diagram in diagrams.items():
        dot_text = render_dot(
            diagram_id=diagram_id, diagram=diagram, nodes=nodes, meta=meta
        )
        mmd_text = render_mermaid(
            diagram_id=diagram_id, diagram=diagram, nodes=nodes, meta=meta
        )
        stem = diagram_id.replace("/", "_")
        dot_path = DIAGRAMS_DIR / f"{stem}.dot"
        mmd_path = DIAGRAMS_DIR / f"{stem}.mmd"
        dot_path.write_text(dot_text, encoding="utf-8")
        mmd_path.write_text(mmd_text, encoding="utf-8")
        print(f"wrote {dot_path.relative_to(REPO_ROOT)}")
        print(f"wrote {mmd_path.relative_to(REPO_ROOT)}")

        # Optional per-kind Mermaid views
        for kind in args.views or []:
            view = render_mermaid(
                diagram_id=diagram_id,
                diagram=diagram,
                nodes=nodes,
                meta=meta,
                edge_filter=kind,
            )
            view_path = DIAGRAMS_DIR / f"{stem}.{kind}.mmd"
            view_path.write_text(view, encoding="utf-8")
            print(f"wrote {view_path.relative_to(REPO_ROOT)}")

        if dot_bin:
            png_path = GENERATED_DIR / f"{stem}.png"
            render_png(dot_bin, dot_path, png_path)
            print(f"wrote {png_path.relative_to(REPO_ROOT)}")

    print(f"wrote {OWNERS_MD.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
