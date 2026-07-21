"""
Decentralized SAXS report fragments: per-skill Markdown + summary YAML.

Skills call ``write_skill_report_fragments``. Assemblers ``report_individual`` /
``report_summary`` discover files under a pipeline root and merge them.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import uuid
from typing import Any, Dict, List, Optional, Tuple

# Optional YAML (autosaxs already depends on yaml in multiple skills)
try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

import csv as _csv

from autosaxs.core.utils import _strip_sub_int_prefix, read_saxs, read_data

INDIVIDUAL_SUFFIX = "_report_individual.md"
SUMMARY_SUFFIX = "_report_summary.yaml"
FRAGMENT_SCHEMA_VERSION = 1

# Sort keys: lower first. Aligns with pipeline-oriented bands from the plan.
SKILL_DEFAULT_ORDER: Dict[str, int] = {
    "plot_2d": 0,
    "integrate_proxy": 1,
    "calibrate": 10,
    "integrate": 20,
    "average": 25,
    "subtract": 30,
    "plot": 40,
    "fit_guinier": 41,
    "analyze_kratky": 42,
    "fit_distances": 43,
    "fit_sizes": 44,
    "model_mixture": 50,
    "fit_mixture": 50,  # deprecated alias id (legacy fragments)
    "model_bodies": 51,
    "fit_bodies": 51,  # deprecated alias id (legacy fragments)
    "model_dam": 52,
    "fit_dammif": 52,  # deprecated alias id (legacy fragments)
    "model_density": 53,
}


def default_sort_order(skill_id: str) -> int:
    return SKILL_DEFAULT_ORDER.get(skill_id, 1000)


def _yaml_dump(doc: Dict[str, Any], path: str) -> None:
    if yaml is None:
        raise RuntimeError("report_fragments: PyYAML is required to write summary YAML")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, sort_keys=False, allow_unicode=True)


def write_skill_report_fragments(
    output_dir: str,
    basename: str,
    skill_id: str,
    md_body: str,
    *,
    summary_references: Optional[List[Dict[str, Any]]] = None,
    summary_extra: Optional[Dict[str, Any]] = None,
    order: Optional[int] = None,
    write_summary_yaml: bool = True,
) -> Tuple[str, Optional[str]]:
    """
    Write ``{basename}_report_individual.md`` and optionally ``{basename}_report_summary.yaml`` under ``output_dir``.

    Paths inside ``md_body`` and in ``summary_references[*].path`` must be relative to ``output_dir``.
    Optional ``summary_extra`` keys are merged into the summary YAML (e.g. ``correctness`` for subtract).

    Returns ``(md_path, yaml_path_or_none)``.
    """
    os.makedirs(output_dir, exist_ok=True)
    order_eff = int(order) if order is not None else int(default_sort_order(skill_id))
    safe_base = basename.replace(os.sep, "_").replace("/", "_")
    md_path = os.path.join(output_dir, f"{safe_base}{INDIVIDUAL_SUFFIX}")
    yaml_path: Optional[str] = None

    fm = (
        "---\n"
        f"skill_id: {skill_id}\n"
        f"order: {order_eff}\n"
        f"basename: {safe_base}\n"
        f"schema_version: {FRAGMENT_SCHEMA_VERSION}\n"
        "---\n\n"
    )
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(fm + (md_body or "").rstrip() + "\n")

    if write_summary_yaml:
        yaml_path = os.path.join(output_dir, f"{safe_base}{SUMMARY_SUFFIX}")
        refs = list(summary_references) if summary_references is not None else []
        doc: Dict[str, Any] = {
            "skill_id": skill_id,
            "schema_version": FRAGMENT_SCHEMA_VERSION,
            "basename": safe_base,
            "order": order_eff,
            "references": refs,
        }
        if summary_extra:
            doc.update(summary_extra)
        _yaml_dump(doc, yaml_path)
    return md_path, yaml_path


def parse_front_matter(md_text: str) -> Tuple[Dict[str, Any], str]:
    """Return (meta dict, body). Meta keys are lower-case string values where parsable."""
    lines = md_text.splitlines()
    meta: Dict[str, Any] = {}
    if not lines or lines[0].strip() != "---":
        return meta, md_text
    end = None
    for i in range(1, min(len(lines), 200)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return meta, md_text
    block = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :])
    if yaml is not None:
        try:
            loaded = yaml.safe_load(block) or {}
            if isinstance(loaded, dict):
                meta = dict(loaded)
        except Exception:
            pass
    else:
        # Minimal key: value parser
        for ln in block.splitlines():
            m = re.match(r"^([A-Za-z0-9_]+)\s*:\s*(.*)$", ln.strip())
            if m:
                k, v = m.group(1), m.group(2).strip()
                if k == "order":
                    try:
                        meta[k] = int(v)
                    except ValueError:
                        meta[k] = v
                elif k == "schema_version":
                    try:
                        meta[k] = int(v)
                    except ValueError:
                        meta[k] = v
                else:
                    meta[k] = v
    return meta, body


_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _sanitize_individual_fragment_body(body: str) -> str:
    """Remove obsolete prose lines from older on-disk fragments (filenames / candidate summaries)."""
    drop_prefixes = (
        "source image:",
        "integrated curve:",
        "subtracted 1d curve:",
        "input profile:",
    )
    out: List[str] = []
    for ln in body.splitlines():
        low = ln.strip().lower()
        if low.startswith(drop_prefixes):
            continue
        if "guinier table written to" in low:
            continue
        if low.startswith("summary:") and (
            "fit_sizes" in low or "fit_sizes_best" in low or "fit_sizes_log" in low or "fit_sizes_fits" in low
        ):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def rewrite_markdown_image_paths_relative_to(body: str, base_dir: str) -> str:
    """Resolve relative ``![alt](path)`` targets against ``base_dir`` to **absolute** paths (legacy)."""

    def repl(m: re.Match[str]) -> str:
        alt, raw = m.group(1), m.group(2).strip().strip('"').strip("'")
        if not raw or raw.startswith("file:"):
            return m.group(0)
        if os.path.isabs(raw):
            return m.group(0)
        abs_path = os.path.normpath(os.path.join(base_dir, raw))
        return f"![{alt}]({abs_path})"

    return _MD_IMAGE.sub(repl, body)


def embed_individual_report_images(body: str, frag_dir: str, md_dir: str, dedup: Dict[str, str]) -> str:
    """Copy each referenced image next to the assembled ``.md`` and use basename-only ``![](_rptimg_….png)`` links (no ``..`` / directory paths in the Markdown)."""

    md_dir = os.path.abspath(md_dir)

    def repl(m: re.Match[str]) -> str:
        alt, raw = m.group(1), m.group(2).strip().strip('"').strip("'")
        if not raw or raw.startswith("file:"):
            return m.group(0)
        if os.path.isabs(raw):
            abs_path = os.path.normpath(raw)
        else:
            abs_path = os.path.normpath(os.path.join(frag_dir, raw))
        if not os.path.isfile(abs_path):
            return m.group(0)
        if abs_path not in dedup:
            ext = os.path.splitext(abs_path)[1].lower()
            if ext not in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
                ext = ".png"
            dedup[abs_path] = f"_rptimg_{uuid.uuid4().hex[:14]}{ext}"
            shutil.copy2(abs_path, os.path.join(md_dir, dedup[abs_path]))
        return f"![{alt}]({dedup[abs_path]})"

    return _MD_IMAGE.sub(repl, body)


def discover_individual_fragment_paths(pipeline_root: str, basename: str) -> List[str]:
    """All ``*_report_individual.md`` paths whose stem matches ``basename`` after prefix strip."""
    target = _strip_sub_int_prefix(basename)
    out: List[str] = []
    for root, _dirs, files in os.walk(pipeline_root):
        for fn in files:
            if not fn.endswith(INDIVIDUAL_SUFFIX):
                continue
            stem = fn[: -len(INDIVIDUAL_SUFFIX)]
            if _strip_sub_int_prefix(stem) == target:
                out.append(os.path.join(root, fn))
    return sorted(out)


def assemble_individual_markdown(fragment_paths: List[str], *, assembly_md_dir: Optional[str] = None) -> str:
    """Sort by front matter ``order`` then ``skill_id``, concatenate fragment bodies only (no internal paths or sort metadata).

    When ``assembly_md_dir`` is set, each ``![…](…)`` image is copied into that directory and referenced by **basename
    only** (``_rptimg_….png``), so the Markdown avoids directory paths such as ``../plots/…``.
    """
    if not fragment_paths:
        return (
            "# SAXS individual report\n\n"
            "No per-step report fragments were found for this sample in the pipeline output.\n"
        )
    md_dir_a: Optional[str] = None
    img_dedup: Dict[str, str] = {}
    if assembly_md_dir:
        md_dir_a = os.path.abspath(assembly_md_dir)
        os.makedirs(md_dir_a, exist_ok=True)
        for fn in list(os.listdir(md_dir_a)):
            if fn.startswith("_rptimg_") and fn.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
                try:
                    os.remove(os.path.join(md_dir_a, fn))
                except OSError:
                    pass
    chunks: List[Tuple[int, str, str, str]] = []
    for p in fragment_paths:
        try:
            with open(p, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        meta, body = parse_front_matter(text)
        order = int(meta.get("order", 1000)) if meta.get("order") is not None else 1000
        sid = str(meta.get("skill_id", ""))
        frag_dir = os.path.dirname(p)
        body_stripped = _sanitize_individual_fragment_body(body.strip())
        if md_dir_a is not None:
            body_resolved = embed_individual_report_images(body_stripped, frag_dir, md_dir_a, img_dedup)
        else:
            body_resolved = rewrite_markdown_image_paths_relative_to(body_stripped, frag_dir)
        chunks.append((order, sid, p, body_resolved))
    chunks.sort(key=lambda t: (t[0], t[1], t[2]))
    bodies = [body.rstrip() for _, _, _, body in chunks if body.strip()]
    if not bodies:
        return (
            "# SAXS individual report\n\n"
            "Report fragments were found but their bodies were empty.\n"
        )
    return "\n\n---\n\n".join(bodies).strip() + "\n"


def discover_summary_yaml_paths(pipeline_root: str) -> List[str]:
    out: List[str] = []
    for root, _dirs, files in os.walk(pipeline_root):
        for fn in files:
            if fn.endswith(SUMMARY_SUFFIX):
                out.append(os.path.join(root, fn))
    return sorted(out)


def _read_summary_yaml(path: str) -> Optional[Dict[str, Any]]:
    if yaml is None:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _resolve_path(yaml_dir: str, rel: str) -> str:
    return os.path.normpath(os.path.join(yaml_dir, rel))


def resolve_reference_value(yaml_dir: str, ref: Dict[str, Any]) -> str:
    """
    Turn one reference entry into a short display string for summary Markdown.

    - ``cell``: single cell by header column name, data row index ``cell.row`` (0 = first data row).
    - ``columns`` (+ optional ``row``): subset of columns from one row.
    - ``format`` ``dat`` / ``saxs_dat``: first line preview.
    - ``png`` / ``other``: path existence only.
    """
    rel = str(ref.get("path", "")).strip()
    if not rel:
        return ""
    fmt = str(ref.get("format", "other")).lower()
    abs_path = _resolve_path(yaml_dir, rel)
    if not os.path.isfile(abs_path):
        return f"(missing file: {rel})"

    if fmt == "png":
        return rel

    if fmt in ("csv", "generic_table"):
        cell = ref.get("cell")
        columns = ref.get("columns")
        row_idx = ref.get("row", 0)
        try:
            row_idx_i = int(row_idx) if row_idx is not None else 0
        except (TypeError, ValueError):
            row_idx_i = 0
        try:
            with open(abs_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        except Exception as e:
            return f"(csv read error: {e})"
        if isinstance(cell, dict) and "column" in cell and "row" in cell:
            try:
                r = int(cell["row"])
                col = str(cell["column"])
            except (TypeError, ValueError, KeyError):
                return "(invalid cell spec)"
            if r < 0 or r >= len(rows):
                return "(cell row out of range)"
            val = rows[r].get(col)
            return "" if val is None else str(val)
        if not rows:
            return "(empty csv)"
        if row_idx_i < 0 or row_idx_i >= len(rows):
            return "(row out of range)"
        row = rows[row_idx_i]
        if columns:
            parts = []
            for c in columns:
                if c in row:
                    parts.append(f"{c}={row[c]}")
            return "; ".join(parts) if parts else str(row)
        return str(row)

    # dat / saxs_dat / text: first non-empty lines as preview
    try:
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            head = []
            for _ in range(5):
                line = f.readline()
                if not line:
                    break
                s = line.strip()
                if s:
                    head.append(s)
        return " | ".join(head) if head else "(empty)"
    except Exception as e:
        return f"(read error: {e})"


def _iter_summary_docs(pipeline_root: str) -> List[Tuple[str, str, int, str, Dict[str, Any]]]:
    """Yield ``(basename, skill_id, order, yaml_dir, doc)`` for each readable summary YAML."""
    out: List[Tuple[str, str, int, str, Dict[str, Any]]] = []
    for yp in discover_summary_yaml_paths(pipeline_root):
        doc = _read_summary_yaml(yp)
        if not doc:
            continue
        base = doc.get("basename")
        base_s = str(base) if base is not None else "__global__"
        order = int(doc.get("order", 1000)) if doc.get("order") is not None else 1000
        sid = str(doc.get("skill_id", ""))
        yaml_dir = os.path.dirname(yp)
        out.append((base_s, sid, order, yaml_dir, doc))
    out.sort(key=lambda t: (t[0], t[2], t[1]))
    return out


def _dedupe_preserve_order(pairs: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    seen: set = set()
    res: List[Tuple[str, str]] = []
    for path, lbl in pairs:
        if not path or path in seen:
            continue
        seen.add(path)
        res.append((path, lbl))
    return res


def _infer_kratky_loglog_from_guinier(guinier_abs: str) -> Tuple[Optional[str], Optional[str]]:
    bname = os.path.basename(guinier_abs)
    if not bname.startswith("guinier_") or not bname.endswith(".dat"):
        return None, None
    stem = bname[len("guinier_") : -len(".dat")]
    d = os.path.dirname(guinier_abs)
    k = os.path.join(d, f"kratky_{stem}.dat")
    l = os.path.join(d, f"loglog_{stem}.dat")
    return (k if os.path.isfile(k) else None, l if os.path.isfile(l) else None)


def _try_mpl_overlay_saxs(
    pairs: List[Tuple[str, str]],
    out_png: str,
    *,
    title: str,
    xlabel: str,
    ylabel: str,
    log_y: bool,
) -> bool:
    pairs = [(p, lb) for p, lb in pairs if p and os.path.isfile(p)]
    if not pairs:
        return False
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(8.0, 5.0))
        for path, lbl in pairs:
            try:
                q, I, _, _ = read_saxs(path)
                ax.plot(q, I, label=str(lbl)[:48])
            except Exception:
                continue
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        if log_y:
            ax.set_yscale("log")
        ax.legend(fontsize=7, loc="best", ncol=2)
        fig.tight_layout()
        fig.savefig(out_png, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return os.path.isfile(out_png) and os.path.getsize(out_png) > 50
    except Exception:
        try:
            import matplotlib.pyplot as plt

            plt.close("all")
        except Exception:
            pass
        return False


def _try_mpl_overlay_columns(
    pairs: List[Tuple[str, str]],
    out_png: str,
    *,
    title: str,
    xlabel: str,
    ylabel: str,
    x_candidates: Tuple[str, ...],
    y_candidates: Tuple[str, ...],
) -> bool:
    pairs = [(p, lb) for p, lb in pairs if p and os.path.isfile(p)]
    if not pairs:
        return False
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(8.0, 5.0))
        for path, lbl in pairs:
            try:
                df, _, _ = read_data(path)
                xc = next((c for c in x_candidates if c in df.columns), None)
                yc = next((c for c in y_candidates if c in df.columns), None)
                if xc is None or yc is None:
                    if len(df.columns) >= 2:
                        xc, yc = str(df.columns[0]), str(df.columns[1])
                    else:
                        continue
                ax.plot(df[xc].to_numpy(), df[yc].to_numpy(), label=str(lbl)[:48])
            except Exception:
                continue
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.legend(fontsize=7, loc="best", ncol=2)
        fig.tight_layout()
        fig.savefig(out_png, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return os.path.isfile(out_png) and os.path.getsize(out_png) > 50
    except Exception:
        try:
            import matplotlib.pyplot as plt

            plt.close("all")
        except Exception:
            pass
        return False


def _try_mpl_overlay_dr_csv(pairs: List[Tuple[str, str]], out_png: str, *, title: str) -> bool:
    pairs = [(p, lb) for p, lb in pairs if p and os.path.isfile(p)]
    if not pairs:
        return False
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(8.0, 5.0))
        for path, lbl in pairs:
            try:
                xs: List[float] = []
                ys: List[float] = []
                with open(path, newline="", encoding="utf-8") as f:
                    r = _csv.DictReader(f)
                    if not r.fieldnames or "R_nm" not in r.fieldnames or "D_R" not in r.fieldnames:
                        continue
                    for row in r:
                        try:
                            xs.append(float(row["R_nm"]))
                            ys.append(float(row["D_R"]))
                        except (TypeError, ValueError, KeyError):
                            continue
                if xs:
                    ax.plot(xs, ys, label=str(lbl)[:48])
            except Exception:
                continue
        ax.set_title(title)
        ax.set_xlabel("R (nm)")
        ax.set_ylabel("D(R) (a.u.)")
        ax.legend(fontsize=7, loc="best", ncol=2)
        fig.tight_layout()
        fig.savefig(out_png, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return os.path.isfile(out_png) and os.path.getsize(out_png) > 50
    except Exception:
        try:
            import matplotlib.pyplot as plt

            plt.close("all")
        except Exception:
            pass
        return False


def _md_escape_cell(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def _calibration_geometry_table(docs: List[Tuple[str, str, int, str, Dict[str, Any]]]) -> str:
    for _b, sid, _o, ydir, doc in docs:
        if sid != "calibrate":
            continue
        refs = doc.get("references") or []
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            if str(ref.get("role", "")) != "refined_geometry":
                continue
            fmt = str(ref.get("format", "")).lower()
            if fmt not in ("text", "yaml", "yml"):
                continue
            abs_p = _resolve_path(ydir, str(ref.get("path", "")))
            if not os.path.isfile(abs_p) or yaml is None:
                continue
            try:
                with open(abs_p, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            lines = ["## Detector geometry\n\n", "| Parameter | Value |\n", "| --- | --- |\n"]
            for k in sorted(data.keys()):
                v = data[k]
                if isinstance(v, (dict, list)):
                    continue
                lines.append(f"| {_md_escape_cell(str(k))} | {_md_escape_cell(str(v))} |\n")
            lines.append("\n")
            return "".join(lines)
    return ""


def _overview_table(docs: List[Tuple[str, str, int, str, Dict[str, Any]]]) -> str:
    by_base: Dict[str, List[str]] = {}
    for base, sid, _order, _ydir, _doc in docs:
        by_base.setdefault(base, []).append(sid)
    lines = ["## Overview\n\n", "| Sample | Pipeline steps |\n", "| --- | --- |\n"]
    for base in sorted(by_base.keys()):
        steps = ", ".join(sorted(set(by_base[base])))
        lines.append(f"| {_md_escape_cell(base)} | {_md_escape_cell(steps)} |\n")
    lines.append("\n")
    return "".join(lines)


def _subtraction_correctness_table(docs: List[Tuple[str, str, int, str, Dict[str, Any]]]) -> str:
    rows_out: List[List[str]] = []
    for base, sid, _o, ydir, doc in docs:
        if sid != "subtract":
            continue
        correctness = doc.get("correctness")
        if not correctness:
            refs = doc.get("references") or []
            if isinstance(refs, list):
                sub_ref = next(
                    (
                        r
                        for r in refs
                        if isinstance(r, dict)
                        and str(r.get("role", "")) in ("subtracted_curve", "sub")
                    ),
                    None,
                )
                if sub_ref:
                    abs_p = _resolve_path(ydir, str(sub_ref.get("path", "")))
                    if os.path.isfile(abs_p):
                        try:
                            _, _, meta = read_saxs(abs_p)
                            subm = meta.get("subtract") if isinstance(meta, dict) else {}
                            if isinstance(subm, dict):
                                correctness = subm.get("correctness")
                        except Exception:
                            pass
        if correctness:
            rows_out.append([base, str(correctness)])
    if not rows_out:
        return ""
    hdr = ["Sample", "Subtraction quality"]
    lines = [
        "## Buffer subtraction quality\n\n",
        "| " + " | ".join(hdr) + " |\n",
        "| " + " | ".join(["---"] * len(hdr)) + " |\n",
    ]
    for row in rows_out:
        lines.append("| " + " | ".join(_md_escape_cell(c) for c in row) + " |\n")
    lines.append("\n")
    return "".join(lines)


def _gnom_best_table(docs: List[Tuple[str, str, int, str, Dict[str, Any]]]) -> str:
    rows_out: List[List[str]] = []
    for base, sid, _o, ydir, doc in docs:
        if sid != "fit_sizes":
            continue
        refs = doc.get("references") or []
        if not isinstance(refs, list):
            continue
        summ = next((r for r in refs if isinstance(r, dict) and r.get("role") == "fit_sizes_summary"), None)
        if not summ:
            continue
        abs_p = _resolve_path(ydir, str(summ.get("path", "")))
        if not os.path.isfile(abs_p) or yaml is None:
            continue
        try:
            with open(abs_p, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        sel = data.get("selected") or {}
        if not isinstance(sel, dict):
            sel = {}
        te = sel.get("total_estimate")
        rows_out.append(
            [
                base,
                str(sel.get("shape", "")),
                str(sel.get("system", "")),
                f"{float(sel['rg_nm']):.4f}" if isinstance(sel.get("rg_nm"), (int, float)) else str(sel.get("rg_nm", "")),
                f"{float(sel['rmax_nm']):.4f}" if isinstance(sel.get("rmax_nm"), (int, float)) else str(sel.get("rmax_nm", "")),
                f"{float(te):.4f}" if isinstance(te, (int, float)) else str(te or ""),
            ]
        )
    if not rows_out:
        return ""
    hdr = ["Sample", "shape", "system", "Rg (nm)", "rmax (nm)", "total_estimate"]
    lines = ["## GNOM / fit_sizes — selected run\n\n", "| " + " | ".join(hdr) + " |\n", "| " + " | ".join(["---"] * len(hdr)) + " |\n"]
    for row in rows_out:
        lines.append("| " + " | ".join(_md_escape_cell(c) for c in row) + " |\n")
    lines.append("\n")
    return "".join(lines)


def assemble_summary_markdown(
    pipeline_root: str,
    *,
    markdown_output_dir: Optional[str] = None,
) -> str:
    """
    Build a pipeline summary: overlaid I(q) / derived plots for all samples, unified tables,
    and a compact overview. Image links are **relative** to ``markdown_output_dir`` (defaults to
    ``<pipeline_root>/reports``) so the Markdown file avoids absolute filesystem paths.
    """
    docs = _iter_summary_docs(pipeline_root)
    if not docs:
        return "# SAXS pipeline summary\n\nNo summary YAML fragments were found for this pipeline.\n"

    reports_dir = os.path.join(pipeline_root, "reports")
    md_dir = markdown_output_dir if markdown_output_dir else reports_dir
    md_dir = os.path.abspath(md_dir)
    assets_dir = os.path.join(reports_dir, "summary_assets")
    os.makedirs(assets_dir, exist_ok=True)
    for fn in os.listdir(assets_dir):
        if fn.startswith("summary_") and fn.endswith(".png"):
            try:
                os.remove(os.path.join(assets_dir, fn))
            except OSError:
                pass

    integrated: List[Tuple[str, str]] = []
    subtracted: List[Tuple[str, str]] = []
    guinier: List[Tuple[str, str]] = []
    kratky: List[Tuple[str, str]] = []
    loglog: List[Tuple[str, str]] = []
    dr_csv: List[Tuple[str, str]] = []

    for base, _sid, _o, ydir, doc in docs:
        refs = doc.get("references") or []
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            role = str(ref.get("role", ""))
            fmt = str(ref.get("format", "")).lower()
            rel = str(ref.get("path", "")).strip()
            if not rel:
                continue
            abs_p = _resolve_path(ydir, rel)
            if not os.path.isfile(abs_p):
                continue
            if role in ("integrated_curve", "integrated_proxy_curve") and fmt in ("saxs_dat", "dat"):
                integrated.append((abs_p, base))
            elif role in ("subtracted_curve", "sub") and fmt in ("saxs_dat", "dat"):
                subtracted.append((abs_p, base))
            elif role == "guinier_dat":
                guinier.append((abs_p, base))
                k1, l1 = _infer_kratky_loglog_from_guinier(abs_p)
                if k1:
                    kratky.append((k1, base))
                if l1:
                    loglog.append((l1, base))
            elif role == "dr_csv" and fmt == "csv":
                dr_csv.append((abs_p, base))

    integrated = _dedupe_preserve_order(integrated)
    subtracted = _dedupe_preserve_order(subtracted)
    guinier = _dedupe_preserve_order(guinier)
    kratky = _dedupe_preserve_order(kratky)
    loglog = _dedupe_preserve_order(loglog)
    dr_csv = _dedupe_preserve_order(dr_csv)

    parts: List[str] = ["# SAXS pipeline summary\n\n"]
    parts.append(_overview_table(docs))
    cal_tbl = _calibration_geometry_table(docs)
    if cal_tbl:
        parts.append(cal_tbl)

    parts.append("## Curve comparison (all samples)\n\n")

    def _emit_fig(rel_name: str, title: str, caption: str) -> None:
        p = os.path.join(assets_dir, rel_name)
        if os.path.isfile(p) and os.path.getsize(p) > 50:
            rel = os.path.relpath(os.path.abspath(p), md_dir).replace(os.sep, "/")
            parts.append(f"### {title}\n\n![{caption}]({rel})\n\n")

    ip = os.path.join(assets_dir, "summary_integrated.png")
    if _try_mpl_overlay_saxs(
        integrated,
        ip,
        title="Integrated intensity",
        xlabel="q (nm^-1)",
        ylabel="I (a.u.)",
        log_y=True,
    ):
        _emit_fig("summary_integrated.png", "Integrated I(q)", "Integrated intensity (all samples)")

    sp = os.path.join(assets_dir, "summary_subtracted.png")
    if _try_mpl_overlay_saxs(
        subtracted,
        sp,
        title="Subtracted intensity",
        xlabel="q (nm^-1)",
        ylabel="I (a.u.)",
        log_y=True,
    ):
        _emit_fig("summary_subtracted.png", "Subtracted I(q)", "Subtracted intensity (all samples)")

    gp = os.path.join(assets_dir, "summary_guinier.png")
    if _try_mpl_overlay_columns(
        guinier,
        gp,
        title="Guinier region",
        xlabel="q^2 (nm^-2)",
        ylabel="ln(I)",
        x_candidates=("q^2", "q2"),
        y_candidates=("log(I)", "ln(I)"),
    ):
        _emit_fig("summary_guinier.png", "Guinier (ln I vs q^2)", "Guinier region (all samples)")

    kp = os.path.join(assets_dir, "summary_kratky.png")
    if _try_mpl_overlay_columns(
        kratky,
        kp,
        title="Kratky",
        xlabel="q (nm^-1)",
        ylabel="I * q^2 (a.u.)",
        x_candidates=("q",),
        y_candidates=("I * q^2", "I*q^2"),
    ):
        _emit_fig("summary_kratky.png", "Kratky", "Kratky (all samples)")

    lp = os.path.join(assets_dir, "summary_loglog.png")
    if _try_mpl_overlay_columns(
        loglog,
        lp,
        title="Log-log",
        xlabel="log(q)",
        ylabel="log(I)",
        x_candidates=("log(q)", "ln(q)"),
        y_candidates=("log(I)", "ln(I)"),
    ):
        _emit_fig("summary_loglog.png", "Log-log", "Log-log (all samples)")

    dp = os.path.join(assets_dir, "summary_dr.png")
    if _try_mpl_overlay_dr_csv(dr_csv, dp, title="GNOM D(R)"):
        _emit_fig("summary_dr.png", "D(R) distributions", "GNOM D(R) (all samples)")

    fig_names = (
        "summary_integrated.png",
        "summary_subtracted.png",
        "summary_guinier.png",
        "summary_kratky.png",
        "summary_loglog.png",
        "summary_dr.png",
    )
    had_any_curve_fig = False
    for n in fig_names:
        fp = os.path.join(assets_dir, n)
        if os.path.isfile(fp) and os.path.getsize(fp) > 50:
            had_any_curve_fig = True
            break
    if not had_any_curve_fig:
        parts.append("_No plottable 1D curves were found in summary references._\n\n")

    sub_quality = _subtraction_correctness_table(docs)
    if sub_quality:
        parts.append(sub_quality)

    gnom_best = _gnom_best_table(docs)
    if gnom_best:
        parts.append(gnom_best)

    return "".join(parts).rstrip() + "\n"


def load_report_summary_schema() -> Optional[Dict[str, Any]]:
    """Load bundled JSON Schema if present."""
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, "..", "resources", "schemas", "report_summary.schema.json")
    p = os.path.normpath(p)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def validate_summary_yaml_doc(doc: Dict[str, Any]) -> List[str]:
    """Return list of validation errors (empty if ok). Best-effort without jsonschema dependency."""
    errs: List[str] = []
    if not isinstance(doc, dict):
        return ["document is not an object"]
    for k in ("skill_id", "schema_version", "order", "references"):
        if k not in doc:
            errs.append(f"missing required key: {k}")
    if "references" in doc and not isinstance(doc["references"], list):
        errs.append("references must be a list")
    return errs
