#!/usr/bin/env python3
"""
Validation script: compare descriptors from foreign pipeline XML files (result.xml / results.xml)
with descriptors computed by our simple_analysis step.

- Finds subtracted curves that were analyzed by foreign pipelines (have entries in XML).
- Runs simple_analysis on those curves.
- Compares only descriptors that exist in the reference XML.
- Produces an HTML report with one table: reference vs calculated, with visual agreement indicators.

Edge cases:
- Subtracted curves with no reference in XML: listed as "no reference" (optional section).
- XML rows for which no curve file is found: listed as "curve not found".
- Only descriptors present in the reference XML are compared.
"""

import argparse
import html
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import xml.etree.ElementTree as ET

try:
    from tqdm import tqdm
    _HAS_TQDM = True
except ImportError:
    _HAS_TQDM = False
    def tqdm(iterable, desc="", leave=True, disable=False, position=0, unit="", **kwargs):
        return iterable

# Project root; we need repos/autosaxs on path for Controller and _parse_descriptors_from_results
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_AUTOSAXS = SCRIPT_DIR / "repos" / "autosaxs"
if str(REPO_AUTOSAXS) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR / "repos"))

from autosaxs.saxs_controller import Controller, _parse_descriptors_from_results
from autosaxs.context import Context
from autosaxs.event_bus import EventBus, EventType
from autosaxs import viewer


# ---------------------------------------------------------------------------
# XML parsing: extract (curve_identifier, reference_descriptors) from each format
# ---------------------------------------------------------------------------

def _tag_local(el: ET.Element) -> str:
    t = el.tag
    return t.split("}", 1)[1] if "}" in t else t


def _get_text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    t = (el.text or "").strip()
    if t:
        return t
    return "".join(el.itertext()).strip()


def _get_attr(el: ET.Element, name: str, default: str = "") -> str:
    return (el.get(name) or default).strip()


def _find_one(parent: Optional[ET.Element], tag: str) -> Optional[ET.Element]:
    if parent is None:
        return None
    for c in parent:
        if _tag_local(c) == tag:
            return c
    return None


def _value_in_block(parent: ET.Element, name: str) -> str:
    for v in parent:
        if _tag_local(v) == "value" and _get_attr(v, "name") == name:
            return _get_text(v)
    return ""


def parse_log_xml(root: ET.Element) -> List[Tuple[str, Dict[str, str]]]:
    """Extract (curve_basename, ref_descriptors) from <log><measurements><file>.
    Curve identifier is the file name (e.g. 5alp_002.dat).
    """
    out = []
    measurements = _find_one(root, "measurements")
    if measurements is None:
        return out
    for file_el in measurements:
        if _tag_local(file_el) != "file":
            continue
        name = _get_attr(file_el, "name") or _get_attr(file_el, "href") or ""
        if not name:
            continue
        # Ensure .dat extension for lookup
        if not name.lower().endswith(".dat"):
            name = name + ".dat" if "." not in name else name
        ref = {}
        autorg = _find_one(file_el, "autorg")
        if autorg is not None:
            rg_el = _find_one(autorg, "radius-of-gyration")
            if rg_el is not None:
                ref["Rg (nm)"] = _get_text(rg_el)
            i0_el = _find_one(autorg, "zero-angle-intensity")
            if i0_el is not None:
                ref["I(0)"] = _get_text(i0_el)
            q_el = _find_one(autorg, "quality")
            if q_el is not None:
                ref["Quality"] = _get_text(q_el)
        autognom = _find_one(file_el, "autognom")
        if autognom is not None:
            dmax_el = _find_one(autognom, "maximum-distance")
            if dmax_el is not None:
                ref["Dmax (nm)"] = _get_text(dmax_el)
        autosub = _find_one(file_el, "autosub")
        if autosub is not None:
            mw_el = _find_one(autosub, "molecular-weight")
            if mw_el is not None:
                ref["MW (kDa)"] = _get_text(mw_el)
        dammif = _find_one(file_el, "dammif")
        if dammif is not None:
            vol_el = _find_one(dammif, "volume")
            if vol_el is not None:
                ref["Volume (nm^3)"] = _get_text(vol_el)
        out.append((name, ref))
    return out


def parse_pipeline_xml(root: ET.Element) -> List[Tuple[str, Dict[str, str]]]:
    """Extract (curve_basename, ref_descriptors) from <pipeline><processed><file>.
    Curve identifier is the 'input' path basename (e.g. BSA_BSA_buffer_0004.dat).
    """
    out = []
    processed = root.find(".//processed")
    if processed is None:
        return out
    for file_el in processed:
        if _tag_local(file_el) != "file":
            continue
        # Get input .dat path from distances/value name="input"
        input_path = ""
        for block in file_el:
            if _tag_local(block) == "distances":
                input_path = _value_in_block(block, "input")
                break
        if not input_path:
            # Fallback: derive from file name (e.g. analysis/gnom/XXX.out -> XXX.dat)
            name_attr = _get_attr(file_el, "name")
            if name_attr and name_attr.endswith(".out"):
                input_path = os.path.basename(name_attr).replace(".out", ".dat")
            else:
                continue
        basename = os.path.basename(input_path)
        if not basename.lower().endswith(".dat"):
            basename = basename + ".dat" if "." not in basename else basename
        ref = {}
        for block in file_el:
            tag = _tag_local(block)
            if tag == "distances":
                dmax = _value_in_block(block, "dmax")
                if dmax:
                    ref["Dmax (nm)"] = dmax
                rggnom = _value_in_block(block, "rggnom")
                if rggnom:
                    ref["Rg (nm)"] = rggnom
                total = _value_in_block(block, "total")
                if total:
                    ref["Quality"] = total  # GNOM total estimate as quality proxy
            elif tag in ("porod", "mow"):
                vol = _value_in_block(block, "volume")
                if vol:
                    # Pipeline often has volume in different units; store as-is for comparison note
                    ref["Volume"] = vol
        out.append((basename, ref))
    return out


def detect_xml_format(root: ET.Element) -> str:
    tag = _tag_local(root)
    if tag == "log":
        return "log"
    if tag == "pipeline":
        return "pipeline"
    return "unknown"


# ---------------------------------------------------------------------------
# Resolve curve path from XML dir and curve identifier
# ---------------------------------------------------------------------------

def _candidate_dirs(xml_path: Path) -> List[Path]:
    """Directories to search for .dat files relative to XML location."""
    d = xml_path.parent
    return [
        d,
        d / "subtracted",
        d / "subtracted_data",
        d / "processed",
        d / "analysis" / "processed",
        d.parent / "subtracted_data",
        d.parent / "subtracted",
        d.parent / "processed",
    ]


def find_curve_path(xml_path: Path, curve_id: str) -> Optional[Path]:
    """Return path to .dat file for curve_id (basename like 5alp_002.dat), or None."""
    base = curve_id if curve_id.lower().endswith(".dat") else curve_id + ".dat"
    for cand_dir in _candidate_dirs(xml_path):
        if not cand_dir.is_dir():
            continue
        p = cand_dir / base
        if p.is_file():
            return p
    return None


def list_subtracted_dats(xml_path: Path) -> List[Path]:
    """List all .dat files in directories that might contain subtracted curves for this XML."""
    seen = set()
    out = []
    for cand_dir in _candidate_dirs(xml_path):
        if not cand_dir.is_dir():
            continue
        for f in cand_dir.glob("*.dat"):
            if f.name not in seen:
                seen.add(f.name)
                out.append(f)
    return sorted(out, key=lambda p: p.name)


# ---------------------------------------------------------------------------
# Run simple_analysis (get_descriptors) for one curve
# ---------------------------------------------------------------------------

def run_simple_analysis_for_curve(curve_path: Path, descriptors_dir: Path, fast_forward: bool = False) -> Optional[Dict[str, str]]:
    """Run get_descriptors and return parsed descriptor dict, or None on failure."""
    event_bus = EventBus()
    # Absorb MESSAGE so controller doesn't block
    def _noop(_):
        pass
    event_bus.subscribe(EventType.MESSAGE, _noop)
    controller = Controller(event_bus, viewer.PLTViewer())
    context = Context()
    context.set_directory(str(curve_path.parent))
    try:
        res_path, _gnom_path = controller.get_descriptors(
            context,
            str(curve_path),
            dest_dir=str(descriptors_dir),
            fast_forward=fast_forward,
        )
    except Exception:
        return None
    parsed = _parse_descriptors_from_results(res_path)
    return parsed


# ---------------------------------------------------------------------------
# Compare reference vs calculated (only keys present in reference)
# ---------------------------------------------------------------------------

# Normalize for comparison: strip whitespace; compare numerically with relative tolerance when possible
def _try_float(s: str) -> Optional[float]:
    if not s or s in ("N/A", "-", ""):
        return None
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        return None


def _values_agree(ref_val: str, calc_val: str, key: str) -> bool:
    """True if ref and calc are considered in agreement. Units: Rg/Dmax nm, Volume nm^3, MW kDa."""
    r = _try_float(ref_val)
    c = _try_float(calc_val)
    if r is None or c is None:
        return (ref_val or "").strip() == (calc_val or "").strip()
    # Quality: reference XML often 0–100 (percent), our pipeline writes R^2 (0–1)
    if "Quality" in key:
        if r > 1 and c <= 1:
            r = r / 100.0  # ref was percent
        elif c > 1 and r <= 1:
            c = c / 100.0
        return abs(r - c) <= 0.05 or (abs(r - c) / max(abs(r), 1e-9)) <= 0.05
    # Volume: reference may be in Å³ (values >> 1000); pipeline reports nm³ (1 nm³ = 1000 Å³)
    if "Volume" in key and r > 1000 and c is not None and 0 < c < 1e7:
        r = r / 1000.0  # ref was Å³ → nm³
    rel_tol = 0.02
    if r == 0:
        return c == 0
    return abs(r - c) / abs(r) <= rel_tol


# Map reference key to possible calculated keys (our pipeline uses slightly different names)
CALC_KEY_MAP = {
    "MW (kDa)": ["MW from DATMW (kDa)", "MW from Rg (kDa)"],  # prefer DATMW for log XML
    "Volume (nm^3)": ["Porod Volume (nm^3)"],
    "Volume": ["Porod Volume (nm^3)"],
}


def get_calc_value(calc: Dict[str, str], ref_key: str) -> Optional[str]:
    if ref_key in calc and calc[ref_key]:
        return calc[ref_key]
    for cand in CALC_KEY_MAP.get(ref_key, [ref_key]):
        if cand in calc and calc[cand]:
            return calc[cand]
    return None


def _possible_q_unit_mismatch(ref: Dict[str, str], calc: Dict[str, str]) -> bool:
    """True if Rg or Dmax ref/calc ratio is between 5 and 20 (suggests q in Å^-1 vs nm^-1)."""
    for key, calc_key in [("Rg (nm)", "Rg (nm)"), ("Dmax (nm)", "Dmax (nm)")]:
        r = _try_float(ref.get(key, ""))
        c = _try_float(calc.get(calc_key, "") or get_calc_value(calc, key))
        if r is not None and c is not None and r > 0 and c > 0:
            ratio = max(r, c) / min(r, c)
            if 5 <= ratio <= 20:
                return True
    return False


# ---------------------------------------------------------------------------
# Discover all result.xml / results.xml in project
# ---------------------------------------------------------------------------

def find_xml_files(project_root: Path) -> List[Path]:
    out = []
    for name in ("result.xml", "results.xml"):
        for p in project_root.rglob(name):
            if "repos" in p.parts or ".git" in p.parts:
                continue
            out.append(p)
    return sorted(out, key=lambda p: (len(p.parts), str(p)))


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

STYLES = """
body { font-family: system-ui, sans-serif; font-size: 14px; margin: 1rem 2rem; max-width: 1400px; }
h1 { font-size: 1.4rem; margin-top: 1em; }
h2 { font-size: 1.15rem; margin-top: 1.2em; }
table { border-collapse: collapse; margin: 0.5em 0 1em; width: 100%; }
th, td { border: 1px solid #ccc; padding: 0.35em 0.5em; text-align: left; }
th { background: #e8e8e8; }
.agree { background: #d4edda; }
.disagree { background: #f8d7da; }
.missing { background: #fff3cd; color: #856404; }
.no-ref { color: #666; }
.meta { color: #555; font-size: 0.9em; margin: 0.5em 0; }
"""


def build_report_html(
    results: List[Dict[str, Any]],
    xml_no_curve: List[Tuple[Path, str, Dict]],
    curves_no_ref: List[Tuple[Path, Path, str]],  # (xml_path, curve_path, basename)
    out_path: Path,
) -> None:
    def esc(s: str) -> str:
        return html.escape(str(s)) if s else ""

    lines = [
        "<!DOCTYPE html>",
        "<html><head><meta charset='utf-8'/><title>Descriptor validation</title>",
        f"<style>{STYLES}</style></head><body>",
        "<h1>Descriptor validation: reference (XML) vs simple_analysis</h1>",
        "<p class='meta'>Only descriptors present in the reference XML are compared. "
        "Green = agreement, red = disagreement, yellow = missing calculated value.</p>",
        "<p class='meta'><strong>Units:</strong> Rg, Dmax in nm; I(0) in a.u.; Quality 0–1 or 0–100%; "
        "Volume in nm³; MW in kDa. Reference Volume in Å³ is converted to nm³ for comparison.</p>",
    ]

    for item in results:
        xml_path = item["xml_path"]
        format_name = item["format"]
        lines.append(f"<h2>{esc(xml_path.name)} ({xml_path.parent})</h2>")
        lines.append(f"<p class='meta'>Format: {esc(format_name)}</p>")

        rows = item.get("rows", [])
        if not rows:
            lines.append("<p>No comparable rows.</p>")
            continue

        # One table per XML: Curve | Descriptor | Reference | Calculated | Match | Note
        lines.append("<table><thead><tr><th>Curve</th><th>Descriptor</th><th>Reference</th><th>Calculated</th><th>Match</th><th>Note</th></tr></thead><tbody>")
        for r in rows:
            curve = esc(r["curve"])
            ref_d = r.get("ref", {})
            calc_d = r.get("calc", {})
            q_note = _possible_q_unit_mismatch(ref_d, calc_d)
            for idx, (desc, ref_val, calc_val, agree) in enumerate(r["descriptors"]):
                ref_s = esc(ref_val) if ref_val else "—"
                calc_s = esc(calc_val) if calc_val else "—"
                if agree is True:
                    cell = "<span class='agree'>✓</span>"
                elif agree is False:
                    cell = "<span class='disagree'>✗</span>"
                else:
                    cell = "<span class='missing'>missing</span>"
                note_cell = esc("Possible q-unit mismatch (Å⁻¹ vs nm⁻¹)?") if (idx == 0 and q_note) else ""
                lines.append(f"<tr><td>{curve}</td><td>{esc(desc)}</td><td>{ref_s}</td><td>{calc_s}</td><td>{cell}</td><td>{note_cell}</td></tr>")
        lines.append("</tbody></table>")

    if xml_no_curve:
        lines.append("<h2>XML rows with no curve found</h2>")
        lines.append("<table><thead><tr><th>XML</th><th>Curve id</th><th>Reference descriptors</th></tr></thead><tbody>")
        for xml_path, curve_id, ref in xml_no_curve:
            ref_str = ", ".join(f"{k}={v}" for k, v in ref.items()) if ref else "—"
            lines.append(f"<tr><td>{esc(xml_path)}</td><td>{esc(curve_id)}</td><td>{esc(ref_str)}</td></tr>")
        lines.append("</tbody></table>")

    if curves_no_ref:
        lines.append("<h2>Subtracted curves with no reference in XML</h2>")
        lines.append("<p class='meta'>These .dat files exist next to the XML but have no entry in the reference.</p>")
        lines.append("<table><thead><tr><th>XML</th><th>Curve path</th><th>Basename</th></tr></thead><tbody>")
        for xml_path, path, basename in curves_no_ref:
            lines.append(f"<tr><td>{esc(str(xml_path))}</td><td>{esc(str(path))}</td><td>{esc(basename)}</td></tr>")
        lines.append("</tbody></table>")

    lines.append("</body></html>")
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def strip_pi(content: str) -> str:
    return re.sub(r"\s*<\?[^?]*\?>\s*", "\n", content)


def main():
    ap = argparse.ArgumentParser(description="Validate descriptors: run simple_analysis on curves from XML and compare to reference.")
    ap.add_argument("--project", type=Path, default=SCRIPT_DIR, help="Project root to search for result.xml / results.xml")
    ap.add_argument("--out", type=Path, default=SCRIPT_DIR / "validation_report.html", help="Output HTML path")
    ap.add_argument("--no-run", action="store_true", help="Only parse XML and report matches; do not run simple_analysis")
    ap.add_argument("--fast-forward", action="store_true", help="Skip simple_analysis if descriptors already exist")
    ap.add_argument("--limit", type=int, default=None, metavar="N", help="Max curves to analyze per XML (for testing)")
    ap.add_argument("-v", "--verbose", action="store_true", help="Print progress per XML and per curve")
    args = ap.parse_args()

    project_root = args.project.resolve()
    xml_files = find_xml_files(project_root)
    if not xml_files:
        print("No result.xml / results.xml found under", project_root)
        return 1

    print(f"Found {len(xml_files)} XML file(s).")

    results = []
    xml_no_curve = []
    curves_no_ref = []

    outer_bar = tqdm(
        xml_files,
        desc="XML",
        position=0,
        leave=True,
        unit="xml",
        disable=args.no_run and not args.verbose,
    )
    for xml_path in outer_bar:
        getattr(outer_bar, "set_postfix_str", lambda _: None)(str(xml_path.name)[:48])
        try:
            raw = xml_path.read_text(encoding="utf-8", errors="replace")
            raw = strip_pi(raw)
            root = ET.fromstring(raw)
        except Exception as e:
            getattr(outer_bar, "write", print)(f"Skip {xml_path}: parse error — {e}")
            continue

        fmt = detect_xml_format(root)
        if fmt == "log":
            entries = parse_log_xml(root)
        elif fmt == "pipeline":
            entries = parse_pipeline_xml(root)
        else:
            getattr(outer_bar, "write", print)(f"Skip {xml_path}: unknown format")
            continue

        if args.verbose:
            getattr(outer_bar, "write", print)(f"  {xml_path.name}: format={fmt}, entries={len(entries)}")

        # Resolve curve paths; only run analysis for curves that have reference descriptors to compare
        matched = []  # (curve_id, curve_path, ref_descriptors)
        for curve_id, ref in entries:
            if not ref:
                continue  # skip: no reference values to compare, don't waste time running analysis
            path = find_curve_path(xml_path, curve_id)
            if path is None:
                xml_no_curve.append((xml_path, curve_id, ref))
                continue
            matched.append((curve_id, path, ref))

        if args.verbose:
            getattr(outer_bar, "write", print)(f"  matched={len(matched)}, no_curve={len(entries) - len(matched)}")

        # Optional: curves that exist but have no ref
        all_dats = {p.name: p for p in list_subtracted_dats(xml_path)}
        ref_basenames = {e[0].lower().replace(".dat", "") + ".dat" for e in entries}
        for basename, path in all_dats.items():
            norm = basename.lower()
            if not any(norm == r.lower() for r in ref_basenames):
                curves_no_ref.append((xml_path, path, basename))

        if not matched:
            results.append({
                "xml_path": xml_path,
                "format": fmt,
                "rows": [],
            })
            continue

        # Run simple_analysis for each matched curve
        descriptors_dir = xml_path.parent / "validation_descriptors"
        descriptors_dir.mkdir(parents=True, exist_ok=True)
        if args.limit is not None:
            matched = matched[: args.limit]
        rows = []
        run_analysis = not args.no_run and matched
        inner_bar = tqdm(
            matched,
            desc="  curves",
            position=1,
            leave=False,
            disable=not run_analysis,
            unit="curve",
        )
        for curve_id, curve_path, ref in inner_bar:
            if run_analysis:
                getattr(inner_bar, "set_postfix_str", lambda _: None)(curve_id[:36] + ("…" if len(curve_id) > 36 else ""))
            if args.no_run:
                calc = None
            else:
                calc = run_simple_analysis_for_curve(curve_path, descriptors_dir, fast_forward=args.fast_forward)
            row_descriptors = []
            for ref_key, ref_val in ref.items():
                calc_val = get_calc_value(calc or {}, ref_key) if calc else None
                agree = None
                if calc_val is not None and ref_val:
                    agree = _values_agree(ref_val, calc_val, ref_key)
                elif calc_val is None and ref_val:
                    agree = None  # missing
                elif ref_val:
                    agree = False
                row_descriptors.append((ref_key, ref_val or "", calc_val or "", agree))
            rows.append({
                "curve": curve_id,
                "descriptors": row_descriptors,
                "ref": ref,
                "calc": calc or {},
            })
        results.append({
            "xml_path": xml_path,
            "format": fmt,
            "rows": rows,
        })

    build_report_html(results, xml_no_curve, curves_no_ref, args.out)
    print(f"Report written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
