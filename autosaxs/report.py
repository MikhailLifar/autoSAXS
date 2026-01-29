"""
Per-profile PDF report builder for the SAXS pipeline.
Builds a single PDF from a report-data dictionary; only sections for which data is present are included.
See pipeline_interactive_spec.md §6 Report.
"""
import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import yaml
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Image,
    Table,
    TableStyle,
    KeepTogether,
)

# Optional: matplotlib for integrated curve figure from .dat
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    _HAS_MPL = True
except Exception:
    _HAS_MPL = False

from .utils import read_saxs


REPORT_IMAGE_WIDTH = 14 * cm  # fit to A4 width with margins


def _fig_from_curve_dat(dat_path: str) -> Optional[str]:
    """Create a temporary PNG from a 1D SAXS .dat file; return path or None on failure."""
    if not _HAS_MPL or not os.path.isfile(dat_path):
        return None
    path = None
    try:
        q, I, _, _ = read_saxs(dat_path)
        fig, ax = plt.subplots()
        ax.plot(q, I)
        ax.set_xlabel('q (nm⁻¹)')
        ax.set_ylabel('I (a.u.)')
        ax.set_title('Integrated curve')
        fig.tight_layout()
        fd, path = tempfile.mkstemp(suffix='.png')
        os.close(fd)
        fig.savefig(path, dpi=100, bbox_inches='tight')
        plt.close(fig)
        return path
    except Exception:
        if path is not None:
            try:
                os.unlink(path)
            except Exception:
                pass
        return None


def _add_image_if_exists(
    story: list, path: Optional[str], caption: str, temp_paths: list, styles: Any
) -> None:
    """Append a caption + image + spacer as one KeepTogether block to avoid header/figure split across pages."""
    if not path or not os.path.isfile(path):
        return
    try:
        img = Image(path, width=REPORT_IMAGE_WIDTH, height=REPORT_IMAGE_WIDTH * 0.6)
        block = KeepTogether(
            [
                Paragraph(caption, styles['Heading3']),
                img,
                Spacer(1, 0.5 * cm),
            ]
        )
        story.append(block)
    except Exception:
        pass


def _fmt_num(v: Any) -> str:
    """Format numbers as .3f, others as str."""
    if isinstance(v, (int, float)):
        return f"{v:.3f}"
    return str(v)


def _load_fits_from_yml(
    bodies_yml_path: Optional[str] = None,
    dammif_yml_path: Optional[str] = None,
) -> List[Tuple[str, str]]:
    """Build fits table rows (fit_kind, params_str) from bodies and dammif yml files. Numbers in .3f."""
    rows: List[Tuple[str, str]] = []
    if bodies_yml_path and os.path.isfile(bodies_yml_path):
        try:
            with open(bodies_yml_path, 'r') as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                for shape_name, params in data.items():
                    if isinstance(params, dict):
                        parts = [f"{k}: {_fmt_num(v)}" for k, v in params.items()]
                        rows.append((f"bodies-{shape_name}", ", ".join(parts)))
        except Exception:
            pass
    if dammif_yml_path and os.path.isfile(dammif_yml_path):
        try:
            with open(dammif_yml_path, 'r') as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                for rep_name, params in data.items():
                    if isinstance(params, dict):
                        parts = [f"{k}: {_fmt_num(v)}" for k, v in params.items()]
                        rows.append((rep_name, ", ".join(parts)))
        except Exception:
            pass
    return rows


def _table_from_csv_excerpt(csv_path: str, max_rows: int = 6, max_cols: int = 5) -> Optional[Table]:
    """Build a small ReportLab Table from the first rows of a CSV. Numbers formatted as .3f."""
    if not csv_path or not os.path.isfile(csv_path):
        return None
    try:
        import pandas as pd
        df = pd.read_csv(csv_path, nrows=max_rows)
        if df.empty:
            return None
        df = df.iloc[:, :max_cols]
        # Format numeric cells as .3f
        def _cell_str(x: Any) -> str:
            if isinstance(x, (int, float)) and not isinstance(x, bool):
                return f"{x:.3f}"
            return str(x)
        rows = [df.columns.tolist()] + [[_cell_str(x) for x in row] for row in df.values.tolist()]
        col_width = 2.8 * cm
        t = Table(rows, colWidths=[col_width] * len(rows[0]))
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ]))
        return t
    except Exception:
        return None


def _fig_exp_and_fits_from_csv(
    bodies_csv_path: Optional[str] = None,
    dammif_csv_path: Optional[str] = None,
) -> Optional[str]:
    """Create one figure: experimental curve + all fitted curves from bodies and/or dammif CSV. Returns temp PNG path or None."""
    if not _HAS_MPL:
        return None
    try:
        import pandas as pd
        dfs = []
        if bodies_csv_path and os.path.isfile(bodies_csv_path):
            dfs.append(pd.read_csv(bodies_csv_path))
        if dammif_csv_path and os.path.isfile(dammif_csv_path):
            dfs.append(pd.read_csv(dammif_csv_path))
        if not dfs:
            return None
        base = dfs[0]
        if 'q' not in base.columns or 'exp' not in base.columns:
            return None
        q = base['q'].values
        exp = base['exp'].values
        fig, ax = plt.subplots()
        ax.plot(q, exp, 'k-', lw=4, label='exp')
        for df in dfs:
            q_use = df['q'].values if 'q' in df.columns else q
            for c in df.columns:
                if c in ('q', 'exp'):
                    continue
                y = df[c].values
                if len(y) == len(q_use):
                    ax.plot(q_use, y, '-', lw=2, label=c)
        ax.set_xlabel('q (nm⁻¹)')
        ax.set_ylabel('I (a.u.)')
        ax.set_title('Experimental and fitted curves')
        ax.legend(loc='best', fontsize=12)
        ax.set_yscale('log')
        fig.tight_layout()
        fd, path = tempfile.mkstemp(suffix='.png')
        os.close(fd)
        fig.savefig(path, dpi=300, bbox_inches='tight')
        plt.close(fig)
        return path
    except Exception:
        return None


def build_report_pdf(report_data: Dict[str, Any], output_path: str) -> None:
    """
    Build a single PDF report from a report-data dictionary.
    Only sections for which report_data contains the corresponding key are included.
    Output is written to output_path (e.g. directory/reports/<basename>_report.pdf).

    Report_data keys (all optional):
        basename: str — used as title
        integrated_curve_path: str — path to 1D .dat; a figure is generated and embedded
        difference_plot_path: str — path to diff_<basename>.png
        subtracted_plot_path: str — path to sub_<basename>.png
        descriptors_table: list of (label, value) or dict — Rg, I(0), Quality
        plot_figures: dict with keys sub, guinier, kratky, loglog (paths) — one figure per plot kind
        fits_comparison_figure_path: str or list — path(s) to fits comparison PNG(s)
        fits_table: list of (fit_kind_str, params_str) — two columns (used if yml paths not given)
        bodies_fits_yml_path: str — path to bodies_fits.yml (BODIES step); used to build fits table
        dammif_fits_yml_path: str — path to dammif_fits.yml (DAMMIF step); used to build fits table
        bodies_fits_csv_path: str — path to bodies_fits.csv; optional excerpt table in Fits section
        dammif_fits_csv_path: str — path to dammif_fits.csv; optional excerpt table in Fits section
    """
    styles = getSampleStyleSheet()
    story: list = []
    temp_paths: List[str] = []

    basename = report_data.get('basename', 'Report')
    story.append(Paragraph(f"SAXS report: {basename}", styles['Title']))
    story.append(Spacer(1, 0.5 * cm))

    # (1) Integrated curve
    integrated_path = report_data.get('integrated_curve_path')
    if integrated_path and integrated_path.endswith('.dat'):
        fig_path = _fig_from_curve_dat(integrated_path)
        if fig_path:
            temp_paths.append(fig_path)
            _add_image_if_exists(story, fig_path, "Integrated curve", temp_paths, styles)
    elif integrated_path and os.path.isfile(integrated_path):
        _add_image_if_exists(story, integrated_path, "Integrated curve", temp_paths, styles)

    # (2) Difference plot
    _add_image_if_exists(
        story,
        report_data.get('difference_plot_path'),
        "Difference plot (sample vs scaled buffer)",
        temp_paths,
        styles,
    )

    # (3) Subtracted plot
    _add_image_if_exists(
        story,
        report_data.get('subtracted_plot_path'),
        "Subtracted curve",
        temp_paths,
        styles,
    )

    # (4) Descriptors table — keep heading + table together; numbers in .3f
    descriptors = report_data.get('descriptors_table')
    if descriptors is not None:
        if isinstance(descriptors, dict):
            rows = [["Parameter", "Value"]] + [[k, _fmt_num(v)] for k, v in descriptors.items()]
        elif isinstance(descriptors, (list, tuple)) and descriptors:
            if isinstance(descriptors[0], (list, tuple)):
                rows = [list(r) for r in descriptors]
            else:
                rows = [["Rg (nm)", "I(0)", "Quality"], [_fmt_num(x) for x in descriptors]]
        else:
            rows = []
        if rows:
            t = Table(rows, colWidths=[6 * cm, 6 * cm])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
                ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ]))
            story.append(KeepTogether([
                Paragraph("Descriptors", styles['Heading2']),
                t,
                Spacer(1, 0.5 * cm),
            ]))

    # (5) Plot figures — I vs q, Guinier, Kratky, log I vs log q
    plot_figures = report_data.get('plot_figures')
    if isinstance(plot_figures, dict):
        labels = {
            'sub': 'I vs q',
            'guinier': 'Guinier',
            'kratky': 'Kratky',
            'loglog': 'log I vs log q',
        }
        for key, path in plot_figures.items():
            if path and os.path.isfile(path):
                _add_image_if_exists(story, path, str(labels.get(key, key)), temp_paths, styles)
    elif isinstance(plot_figures, (list, tuple)):
        for i, path in enumerate(plot_figures):
            if path and os.path.isfile(path):
                _add_image_if_exists(story, path, f"Plot {i + 1}", temp_paths, styles)

    # (6) Experimental and fitted curves — one combined figure from bodies/dammif CSV (if available)
    bodies_csv = report_data.get('bodies_fits_csv_path')
    dammif_csv = report_data.get('dammif_fits_csv_path')
    exp_fits_fig_path = _fig_exp_and_fits_from_csv(bodies_csv, dammif_csv)
    if exp_fits_fig_path:
        temp_paths.append(exp_fits_fig_path)
        _add_image_if_exists(
            story, exp_fits_fig_path, "Experimental and fitted curves", temp_paths, styles
        )
    # Also embed any pre-generated fits comparison PNGs from pipeline (polydispfit, bodies, dammif)
    fits_fig = report_data.get('fits_comparison_figure_path')
    if isinstance(fits_fig, (list, tuple)):
        for j, path in enumerate(fits_fig):
            _add_image_if_exists(story, path, f"Fits comparison ({j + 1})", temp_paths, styles)
    elif fits_fig:
        _add_image_if_exists(story, fits_fig, "Fits comparison", temp_paths, styles)

    # (7) Fits table — from bodies/dammif yml when provided, else report_data['fits_table']; keep together
    fits_table = report_data.get('fits_table')
    yml_rows = _load_fits_from_yml(
        report_data.get('bodies_fits_yml_path'),
        report_data.get('dammif_fits_yml_path'),
    )
    if yml_rows:
        fits_table = yml_rows
    if fits_table and isinstance(fits_table, (list, tuple)):
        rows = [["Fit kind", "Fitted parameters"]]
        for row in fits_table:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                rows.append([str(row[0]), str(row[1])])
            elif isinstance(row, (list, tuple)) and len(row) == 1:
                rows.append([str(row[0]), ''])
        if len(rows) > 1:
            t = Table(rows, colWidths=[5 * cm, 9 * cm])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
                ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ]))
            story.append(KeepTogether([
                Paragraph("Fits", styles['Heading2']),
                t,
                Spacer(1, 0.5 * cm),
            ]))

    # Optional: fits curve data excerpt from bodies/dammif csv
    for label, csv_path in [
        ("Bodies fits curve (excerpt)", report_data.get('bodies_fits_csv_path')),
        ("DAMMIF fits curve (excerpt)", report_data.get('dammif_fits_csv_path')),
    ]:
        if not csv_path:
            continue
        tbl = _table_from_csv_excerpt(csv_path)
        if tbl is not None:
            story.append(KeepTogether([
                Paragraph(label, styles['Heading3']),
                tbl,
                Spacer(1, 0.5 * cm),
            ]))

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )
    doc.build(story)

    for p in temp_paths:
        try:
            os.unlink(p)
        except Exception:
            pass
