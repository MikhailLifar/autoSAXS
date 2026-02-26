#!/usr/bin/env python3
"""
Convert pipeline/result XML files (e.g. result.xml, results.xml) to valid HTML
with tables and sections for readable viewing in a browser.
"""
import argparse
import html
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, List, Tuple

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def escape(text: str) -> str:
    return html.escape(str(text).strip()) if text else ""


def tag_local(el: ET.Element) -> str:
    t = el.tag
    return t.split("}", 1)[1] if "}" in t else t


def get_text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    t = (el.text or "").strip()
    if t:
        return t
    return "".join(el.itertext()).strip()


def get_attr(el: ET.Element, name: str, default: str = "") -> str:
    return el.get(name, default).strip()


def value_rows(parent: ET.Element) -> List[Tuple[str, str]]:
    """From a parent containing <value name="...">...</value>, return [(name, text), ...]."""
    rows = []
    for v in parent:
        if tag_local(v) != "value":
            continue
        name = get_attr(v, "name") or "(no name)"
        rows.append((escape(name), escape(get_text(v))))
    return rows


def block_to_table(caption: str, parent: ET.Element, unit_attr: str = "unit") -> str:
    """Render a block of <value name="..."> as a 2-column table. Optional unit in caption."""
    rows = value_rows(parent)
    if not rows:
        return ""
    unit = get_attr(parent, unit_attr) if unit_attr else ""
    cap = escape(caption)
    if unit:
        cap += f" ({escape(unit)})"
    lines = [
        f'<table><caption>{cap}</caption>',
        "<thead><tr><th>Parameter</th><th>Value</th></tr></thead>",
        "<tbody>",
    ]
    for name, val in rows:
        lines.append(f"<tr><td>{name}</td><td>{val}</td></tr>")
    lines.append("</tbody></table>")
    return "\n".join(lines)


def section_heading(level: int, text: str) -> str:
    return f"<h{level}>{escape(text)}</h{level}>"


# ---------------------------------------------------------------------------
# Pipeline format (result.xml): <pipeline> with <runinfo> and <processed><file>...
# ---------------------------------------------------------------------------


def render_runinfo(runinfo: ET.Element) -> str:
    rows = value_rows(runinfo)
    if not rows:
        return ""
    lines = ["<table><caption>Run info</caption>", "<thead><tr><th>Key</th><th>Value</th></tr></thead>", "<tbody>"]
    for name, val in rows:
        lines.append(f"<tr><td>{name}</td><td>{val}</td></tr>")
    lines.append("</tbody></table>")
    return "\n".join(lines)


def render_file_block(file_el: ET.Element) -> str:
    """One <file>: summary row + <details> with per-block tables."""
    name = get_attr(file_el, "name") or "file"
    # Collect summary from known blocks
    dmax = ""
    rggnom = ""
    total = ""
    model = ""
    volume = ""
    for block in file_el:
        tag = tag_local(block)
        if tag == "distances":
            for v in block:
                if tag_local(v) != "value":
                    continue
                n = get_attr(v, "name")
                if n == "dmax":
                    dmax = get_text(v)
                elif n == "rggnom":
                    rggnom = get_text(v)
                elif n == "total":
                    total = get_text(v)
        elif tag == "abinitio":
            for v in block:
                if tag_local(v) == "value" and get_attr(v, "name") == "model":
                    model = get_text(v)
                    break
        elif tag in ("porod", "mow"):
            for v in block:
                if tag_local(v) == "value" and get_attr(v, "name") == "volume":
                    volume = get_text(v)
                    break

    details_id = "f-" + str(abs(hash(name)))  # simple unique id
    details_body = []
    for block in file_el:
        tag = tag_local(block)
        tbl = block_to_table(tag, block, unit_attr="")
        if tbl:
            details_body.append(tbl)
    details_inner = "\n".join(details_body) if details_body else "<p>No parameters</p>"

    return f"""
<section class="file-block">
  <details id="{details_id}">
    <summary>
      <strong>{escape(name)}</strong>
      <span class="summary">dmax={escape(dmax)} · Rg(gnom)={escape(rggnom)} · total={escape(total)} · model={escape(model)[:50]}{'…' if len(model) > 50 else ''} · volume={escape(volume)}</span>
    </summary>
    <div class="file-details">
      {details_inner}
    </div>
  </details>
</section>"""


def render_pipeline(root: ET.Element) -> str:
    parts = ['<div class="pipeline">', section_heading(1, "Pipeline result")]
    runinfo = root.find(".//runinfo")
    if runinfo is not None:
        parts.append(render_runinfo(runinfo))
    processed = root.find(".//processed")
    if processed is not None:
        parts.append(section_heading(2, "Processed files"))
        parts.append('<div class="file-list">')
        for f in processed:
            if tag_local(f) == "file":
                parts.append(render_file_block(f))
        parts.append("</div>")
    parts.append("</div>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Log format (results.xml): <log> with <started>, <measurements><file>...
# ---------------------------------------------------------------------------


def one_cell(el: Optional[ET.Element]) -> str:
    if el is None:
        return "<td></td>"
    unit = get_attr(el, "unit", "")
    text = escape(get_text(el))
    if unit:
        text += f" {escape(unit)}"
    return f"<td>{text}</td>"


def find_one(parent: Optional[ET.Element], tag: str) -> Optional[ET.Element]:
    if parent is None:
        return None
    for c in parent:
        if tag_local(c) == tag:
            return c
    return None


def render_log(root: ET.Element) -> str:
    parts = ['<div class="log">', section_heading(1, "Results log")]

    started = find_one(root, "started")
    if started is not None:
        parts.append(f'<p class="started">Started: {escape(get_text(started))}</p>')

    measurements = find_one(root, "measurements")
    if measurements is None:
        parts.append("</div>")
        return "\n".join(parts)

    parts.append(section_heading(2, "Measurements"))
    parts.append("""
<table class="measurements">
<thead>
<tr>
  <th>File</th>
  <th>Run #</th>
  <th>Timestamp</th>
  <th>Description</th>
  <th>Conc.</th>
  <th>MW</th>
  <th>Rg</th>
  <th>Dmax</th>
  <th>Volume</th>
  <th>Quality</th>
</tr>
</thead>
<tbody>""")

    for file_el in measurements:
        if tag_local(file_el) != "file":
            continue
        name = get_attr(file_el, "name") or get_attr(file_el, "href") or "—"
        run_num = find_one(file_el, "run-number")
        ts = find_one(file_el, "timestamp")
        desc = find_one(file_el, "description")
        conc_el = find_one(file_el, "concentration")
        conc = get_text(conc_el) if conc_el is not None else ""
        conc_unit = get_attr(conc_el, "unit") if conc_el is not None else ""
        autosub = find_one(file_el, "autosub")
        mw_el = find_one(autosub, "molecular-weight") if autosub is not None else None
        mw = get_text(mw_el) if mw_el is not None else ""
        autorg = find_one(file_el, "autorg")
        rg_el = find_one(autorg, "radius-of-gyration") if autorg is not None else None
        rg = get_text(rg_el) if rg_el is not None else ""
        autognom = find_one(file_el, "autognom")
        dmax_el = find_one(autognom, "maximum-distance") if autognom is not None else None
        dmax = get_text(dmax_el) if dmax_el is not None else ""
        dammif = find_one(file_el, "dammif")
        vol_el = find_one(dammif, "volume") if dammif is not None else None
        vol = get_text(vol_el) if vol_el is not None else ""
        qual_el = find_one(autorg, "quality") if autorg is not None else None
        qual = get_text(qual_el) if qual_el is not None else ""

        parts.append("<tr>")
        parts.append(f"<td>{escape(name)}</td>")
        parts.append(one_cell(run_num))
        parts.append(one_cell(ts))
        parts.append(f"<td>{escape(get_text(desc) if desc else '')}</td>")
        parts.append(f"<td>{escape(conc)} {escape(conc_unit)}</td>")
        parts.append(f"<td>{escape(mw)}</td>")
        parts.append(f"<td>{escape(rg)}</td>")
        parts.append(f"<td>{escape(dmax)}</td>")
        parts.append(f"<td>{escape(vol)}</td>")
        parts.append(f"<td>{escape(qual)}</td>")
        parts.append("</tr>")

    parts.append("</tbody></table>")
    parts.append("</div>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Fallback: unknown root → generic key-value from first level
# ---------------------------------------------------------------------------
def elem_to_html_raw(el: ET.Element, indent: int = 0) -> str:
    """Raw tag view (original behavior) for fallback."""
    tag = tag_local(el)
    attrs = " " + " ".join(f'{html.escape(k)}="{html.escape(v)}"' for k, v in sorted(el.attrib.items())) if el.attrib else ""
    children = list(el)
    text = (el.text or "").strip()
    tail = (el.tail or "").strip()
    margin = "  " * indent
    parts = []
    if not children and not text:
        parts.append(f'{margin}<span class="tag">&lt;{tag}{attrs}/&gt;</span>\n')
    else:
        parts.append(f'{margin}<span class="tag">&lt;{tag}{attrs}&gt;</span>\n')
        if text:
            parts.append(f'{margin} <span class="text">{escape(text)}</span>\n')
        for child in children:
            parts.append(elem_to_html_raw(child, indent + 1))
        parts.append(f'{margin}<span class="tag">&lt;/{tag}&gt;</span>\n')
    if tail:
        parts.append(f'{margin}<span class="text">{escape(tail)}</span>\n')
    return "".join(parts)


def render_fallback(root: ET.Element) -> str:
    return '<pre class="raw">' + elem_to_html_raw(root, 0) + "</pre>"


# ---------------------------------------------------------------------------
# Main: detect format and build HTML document
# ---------------------------------------------------------------------------

STYLES = """
body { font-family: system-ui, sans-serif; font-size: 15px; margin: 1rem 2rem; max-width: 1200px; }
h1 { font-size: 1.4rem; margin-top: 1.5em; }
h2 { font-size: 1.15rem; margin-top: 1.2em; }
table { border-collapse: collapse; margin: 0.5em 0 1em; width: 100%; }
th, td { border: 1px solid #ccc; padding: 0.35em 0.6em; text-align: left; }
th { background: #f0f0f0; }
caption { font-weight: bold; text-align: left; padding: 0.3em 0; }
.file-list { margin-top: 0.5em; }
.file-block { margin: 0.4em 0; }
.file-block details { border: 1px solid #ccc; border-radius: 4px; padding: 0.4em 0.6em; }
.file-block summary { cursor: pointer; }
.file-block summary .summary { color: #666; font-weight: normal; font-size: 0.9em; margin-left: 0.5em; }
.file-details { margin-top: 0.6em; }
.file-details table { margin: 0.4em 0; max-width: 600px; }
.measurements { font-size: 0.9rem; }
.measurements td, .measurements th { padding: 0.25em 0.4em; }
.started { color: #444; margin: 0.5em 0; }
.raw { white-space: pre-wrap; font-family: ui-monospace, monospace; font-size: 13px; }
"""


def strip_processing_instructions(content: str) -> str:
    lines = []
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("<?") and s.endswith("?>"):
            continue
        lines.append(line)
    return "\n".join(lines)


def convert(xml_path: str, out_path: Optional[str]) -> str:
    xml_path = Path(xml_path)
    if not xml_path.exists():
        raise FileNotFoundError(f"Input file not found: {xml_path}")

    raw = xml_path.read_text(encoding="utf-8", errors="replace")
    raw = strip_processing_instructions(raw)
    root = ET.fromstring(raw)
    root_tag = tag_local(root)

    if root_tag == "pipeline":
        body_html = render_pipeline(root)
    elif root_tag == "log":
        body_html = render_log(root)
    else:
        body_html = render_fallback(root)

    title = xml_path.name
    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
{STYLES}
</style>
</head>
<body>
{body_html}
</body>
</html>
"""
    if out_path is None:
        out_path = xml_path.with_suffix(".html")
    else:
        out_path = Path(out_path)
    out_path.write_text(html_doc, encoding="utf-8")
    return str(out_path)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert pipeline/result XML (result.xml, results.xml) to HTML with tables."
    )
    ap.add_argument("xml_file", nargs="?", default=None, help="Input .xml file")
    ap.add_argument("-o", "--output", default=None, help="Output .html file")
    args = ap.parse_args()

    if args.xml_file is None:
        print("Usage: xml_to_html.py <file.xml> [-o out.html]", file=sys.stderr)
        sys.exit(1)

    try:
        out = convert(args.xml_file, args.output)
        print(f"Wrote {out}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
