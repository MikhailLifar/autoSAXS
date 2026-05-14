"""
Per-profile PDF report builder for the SAXS pipeline.
Builds a single PDF from a report-data dictionary; only sections for which data is present are included.
See pipeline_interactive_spec.md §6 Report.
"""
import os
import re
import tempfile
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape as xml_escape

import yaml
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
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

from .utils import read_saxs, read_data

# Descriptor column order for summary table (§6.2) and per-profile descriptors table
SUMMARY_DESCRIPTOR_COLUMNS = [
    'Rg (nm)', 'Rg autorg (nm)', 'Guinier interval (final)', 'Guinier interval (autorg)',
    'I(0)', 'Quality', 'Dmax (nm)',
    'MW from Rg (kDa)', 'MW from DATMW (kDa)',
    'Classification',
]


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


def _fig_multi_curves_saxs(
    paths_with_labels: List[Tuple[str, str]],
    xlabel: str,
    ylabel: str,
    title: str,
    log_scale: bool = True,
) -> Optional[str]:
    """Create one figure with multiple SAXS curves (q, I) from .dat paths; return temp PNG path or None.
    log_scale: if True, y-axis is log (log I vs q); if False, linear (I vs q). No legend (avoids clutter for many samples)."""
    if not _HAS_MPL or not paths_with_labels:
        return None
    path_out = None
    try:
        fig, ax = plt.subplots()
        for dat_path, _label in paths_with_labels:
            if not dat_path or not os.path.isfile(dat_path):
                continue
            try:
                q, I, _, _ = read_saxs(dat_path)
                ax.plot(q, I)
            except Exception:
                continue
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        if log_scale:
            ax.set_yscale('log')
        fig.tight_layout()
        fd, path_out = tempfile.mkstemp(suffix='.png')
        os.close(fd)
        fig.savefig(path_out, dpi=150, bbox_inches='tight')
        plt.close(fig)
        return path_out
    except Exception:
        if path_out and os.path.isfile(path_out):
            try:
                os.unlink(path_out)
            except Exception:
                pass
        return None


def _fig_multi_derived_dat(
    paths_with_labels: List[Tuple[str, str]],
    xlabel: str,
    ylabel: str,
    title: str,
    x_col: str,
    y_col: str,
) -> Optional[str]:
    """Create one figure from multiple derived .dat files (e.g. guinier/kratky/loglog); columns by name or index. No legend."""
    if not _HAS_MPL or not paths_with_labels:
        return None
    path_out = None
    try:
        fig, ax = plt.subplots()
        for dat_path, _label in paths_with_labels:
            if not dat_path or not os.path.isfile(dat_path):
                continue
            try:
                df, _, _ = read_data(dat_path)
                if x_col in df.columns and y_col in df.columns:
                    x, y = df[x_col].to_numpy(), df[y_col].to_numpy()
                elif len(df.columns) >= 2:
                    x, y = df.iloc[:, 0].to_numpy(), df.iloc[:, 1].to_numpy()
                else:
                    continue
                ax.plot(x, y)
            except Exception:
                continue
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        fig.tight_layout()
        fd, path_out = tempfile.mkstemp(suffix='.png')
        os.close(fd)
        fig.savefig(path_out, dpi=150, bbox_inches='tight')
        plt.close(fig)
        return path_out
    except Exception:
        if path_out and os.path.isfile(path_out):
            try:
                os.unlink(path_out)
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


def _polydisp_row_from_dat(dat_path: Optional[str]) -> Optional[Tuple[str, str]]:
    """Build one fits table row (fit_kind, params_str) from polydisp fit .dat metadata. chi2, mean, deviation."""
    if not dat_path or not os.path.isfile(dat_path):
        return None
    try:
        _, _, metadata = read_data(dat_path)
        fp = metadata.get('fitted_parameters')
        if fp is None and 'distribution' in metadata:
            dist = metadata['distribution']
            fp = dist.get('params', {}) if isinstance(dist, dict) else {}
        fp = fp or {}
        fq = metadata.get('fit_quality') or {}
        chi2 = fq.get('chi2')
        mean = fp.get('mean') or fp.get('r_mean') or fp.get('mu')
        std = fp.get('std') or fp.get('sigma')
        parts = []
        if chi2 is not None:
            parts.append(f"chi2: {_fmt_num(chi2)}")
        if mean is not None:
            parts.append(f"mean: {_fmt_num(mean)}")
        if std is not None:
            parts.append(f"deviation: {_fmt_num(std)}")
        if not parts:
            return None
        return ("polydispfit", ", ".join(parts))
    except Exception:
        return None


def _load_fits_from_yml(
    bodies_yml_path: Optional[str] = None,
    dammif_yml_path: Optional[str] = None,
) -> List[Tuple[str, str]]:
    """Build fits table rows (fit_kind, params_str) from bodies and dammif yml. chi2 first; numbers in .3f."""
    rows: List[Tuple[str, str]] = []
    def _params_str(params: dict) -> str:
        ordered = ['chi2'] + [k for k in params if k != 'chi2']
        return ", ".join(f"{k}: {_fmt_num(params[k])}" for k in ordered if k in params)
    if bodies_yml_path and os.path.isfile(bodies_yml_path):
        try:
            with open(bodies_yml_path, 'r') as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                for shape_name, params in data.items():
                    if isinstance(params, dict):
                        rows.append((f"bodies-{shape_name}", _params_str(params)))
        except Exception:
            pass
    if dammif_yml_path and os.path.isfile(dammif_yml_path):
        try:
            with open(dammif_yml_path, 'r') as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                for rep_name, params in data.items():
                    if isinstance(params, dict):
                        rows.append((rep_name, _params_str(params)))
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
        descriptors_table: list of (label, value) or dict — Rg, I(0), Quality, Dmax (nm), MW from Rg (kDa), MW from DATMW (kDa)
        plot_figures: dict with keys guinier, kratky, loglog (paths) — plot figures only (no I vs q)
        mixture_best_label: str — best MIXTURE model label (lowest BIC_log)
        mixture_BIC_log: float — BIC on log(I) for best model
        mixture_comparison_figure_path: str — MIXTURE comparison plot (I vs q and log I vs q)
        mixture_distributions_figure_path: str — MIXTURE size distributions (R in nm)
        mixture_results_csv_path: str — MIXTURE results CSV (optional summary table)
        fits_comparison_figure_path: str or list of (path, label) — path(s) and optional label per figure (e.g. mixture, polydispfit, bodies, dammif)
        fits_table: list of (fit_kind_str, params_str) — two columns (used when no yml/polydisp paths)
        polydisp_fit_dat_path: str — path to polydisp fit .dat (metadata); polydisp row is prepended to fits table
        bodies_fits_yml_path: str — path to bodies_fits.yml (BODIES step); used to build fits table
        dammif_fits_yml_path: str — path to dammif_fits.yml (DAMMIF step); used to build fits table
        bodies_fits_csv_path: str — path to bodies_fits.csv (used for optional combined exp+fits figure only)
        dammif_fits_csv_path: str — path to dammif_fits.csv (used for optional combined exp+fits figure only)
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
            # Use spec order for known keys, then any extra keys
            order = [c for c in SUMMARY_DESCRIPTOR_COLUMNS if c in descriptors]
            order += [k for k in descriptors if k not in order]
            rows = [["Parameter", "Value"]] + [[k, _fmt_num(descriptors[k])] for k in order]
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

    # (5) Plot figures — Guinier, Kratky, log I vs log q
    plot_figures = report_data.get('plot_figures')
    if isinstance(plot_figures, dict):
        labels = {'guinier': 'Guinier', 'kratky': 'Kratky', 'loglog': 'log I vs log q'}
        for key in ('guinier', 'kratky', 'loglog'):
            path = plot_figures.get(key)
            if path and os.path.isfile(path):
                _add_image_if_exists(story, path, str(labels.get(key, key)), temp_paths, styles)
    elif isinstance(plot_figures, (list, tuple)):
        for i, path in enumerate(plot_figures):
            if path and os.path.isfile(path):
                _add_image_if_exists(story, path, f"Plot {i + 1}", temp_paths, styles)

    # (5b) Mixture — best model (lowest BIC_log), comparison plot, distribution plot, summary
    mixture_comp = report_data.get('mixture_comparison_figure_path')
    mixture_dist = report_data.get('mixture_distributions_figure_path')
    mixture_best = report_data.get('mixture_best_label')
    mixture_bic = report_data.get('mixture_BIC_log')
    if mixture_best is not None or mixture_bic is not None:
        try:
            if mixture_bic is None:
                bic_str = "—"
            else:
                v = float(mixture_bic)
                bic_str = f"{v:.3f}" if v == v else "—"  # v == v is False for nan
        except (TypeError, ValueError):
            bic_str = "—"
        story.append(Paragraph("Mixture (MIXTURE, spheres)", styles['Heading2']))
        story.append(Paragraph(f"Best model: {mixture_best or '—'}; BIC_log: {bic_str}", styles['Normal']))
        story.append(Spacer(1, 0.3 * cm))
    if mixture_comp and os.path.isfile(mixture_comp):
        _add_image_if_exists(story, mixture_comp, "Mixture: comparison (I vs q, log I vs q)", temp_paths, styles)
    if mixture_dist and os.path.isfile(mixture_dist):
        _add_image_if_exists(story, mixture_dist, "Mixture: size distributions (R in nm)", temp_paths, styles)

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
        for item in fits_fig:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                path, label = str(item[0]), item[1]
                caption = f"Fits comparison, {label}" if label else "Fits comparison"
            else:
                path = item if isinstance(item, str) else str(item)
                caption = "Fits comparison"
            _add_image_if_exists(story, path, caption, temp_paths, styles)
    elif fits_fig:
        _add_image_if_exists(story, str(fits_fig), "Fits comparison", temp_paths, styles)

    # (7) Fits table — polydisp first when present, then bodies/dammif yml; chi2 first; cells wrap to avoid overflow
    fits_table = report_data.get('fits_table')
    yml_rows = _load_fits_from_yml(
        report_data.get('bodies_fits_yml_path'),
        report_data.get('dammif_fits_yml_path'),
    )
    polydisp_row = _polydisp_row_from_dat(report_data.get('polydisp_fit_dat_path'))
    if polydisp_row is not None:
        all_fit_rows = [polydisp_row] + yml_rows
    elif yml_rows:
        all_fit_rows = yml_rows
    else:
        all_fit_rows = list(fits_table) if fits_table and isinstance(fits_table, (list, tuple)) else []
    if all_fit_rows:
        normal_style = styles['Normal']
        rows: List[List[Any]] = [["Fit kind", "Fitted parameters"]]
        for row in all_fit_rows:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                rows.append([Paragraph(str(row[0]), normal_style), Paragraph(str(row[1]), normal_style)])
            elif isinstance(row, (list, tuple)) and len(row) == 1:
                rows.append([Paragraph(str(row[0]), normal_style), Paragraph('', normal_style)])
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
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]))
            story.append(KeepTogether([
                Paragraph("Fits", styles['Heading2']),
                t,
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


def build_summary_report_pdf(summary_data: Dict[str, Any], output_path: str) -> None:
    """
    Build a single PDF summary report combining all sample curves (§6.2).
    Only sections for which data is present are included.

    summary_data keys (all optional):
        samples: list of dict. Each dict may contain:
            basename: str
            integrated_curve_path: str (optional)
            subtracted_curve_path: str (optional)
            descriptors: dict (optional) — same keys as per-profile (Rg (nm), I(0), etc.)
            guinier_path: str (optional) — path to guinier_<basename>.dat
            kratky_path: str (optional)
            loglog_path: str (optional)
    """
    styles = getSampleStyleSheet()
    story: list = []
    temp_paths: List[str] = []

    story.append(Paragraph("SAXS summary report (all curves)", styles['Title']))
    story.append(Spacer(1, 0.5 * cm))

    samples = summary_data.get('samples') or []
    if not samples:
        story.append(Paragraph("No sample data.", styles['Normal']))
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
        return

    # (1) All integrated curves, one axes — both I vs q and log(I) vs q
    integrated = [(s.get('integrated_curve_path'), s.get('basename', '')) for s in samples if s.get('integrated_curve_path')]
    if integrated:
        fig_lin = _fig_multi_curves_saxs(
            integrated,
            xlabel='q (nm⁻¹)',
            ylabel='I (a.u.)',
            title='All integrated curves (I vs q)',
            log_scale=False,
        )
        if fig_lin:
            temp_paths.append(fig_lin)
            _add_image_if_exists(story, fig_lin, "All integrated curves (I vs q)", temp_paths, styles)
        fig_log = _fig_multi_curves_saxs(
            integrated,
            xlabel='q (nm⁻¹)',
            ylabel='I (a.u.)',
            title='All integrated curves (log I vs q)',
            log_scale=True,
        )
        if fig_log:
            temp_paths.append(fig_log)
            _add_image_if_exists(story, fig_log, "All integrated curves (log I vs q)", temp_paths, styles)

    # (2) All subtracted curves, one axes — both I vs q and log(I) vs q
    subtracted = [(s.get('subtracted_curve_path'), s.get('basename', '')) for s in samples if s.get('subtracted_curve_path')]
    if subtracted:
        fig_lin = _fig_multi_curves_saxs(
            subtracted,
            xlabel='q (nm⁻¹)',
            ylabel='I (a.u.)',
            title='All subtracted curves (I vs q)',
            log_scale=False,
        )
        if fig_lin:
            temp_paths.append(fig_lin)
            _add_image_if_exists(story, fig_lin, "All subtracted curves (I vs q)", temp_paths, styles)
        fig_log = _fig_multi_curves_saxs(
            subtracted,
            xlabel='q (nm⁻¹)',
            ylabel='I (a.u.)',
            title='All subtracted curves (log I vs q)',
            log_scale=True,
        )
        if fig_log:
            temp_paths.append(fig_log)
            _add_image_if_exists(story, fig_log, "All subtracted curves (log I vs q)", temp_paths, styles)

    # (3) Descriptors table — rows = samples, columns = descriptor set
    desc_rows = []
    for s in samples:
        d = s.get('descriptors')
        if isinstance(d, dict) and d:
            desc_rows.append((s.get('basename', ''), d))
    if desc_rows:
        all_keys = []
        for _, d in desc_rows:
            for k in d:
                if k not in all_keys:
                    all_keys.append(k)
        # Prefer spec column order, then any extra keys
        col_order = [c for c in SUMMARY_DESCRIPTOR_COLUMNS if c in all_keys]
        col_order += [k for k in all_keys if k not in col_order]
        # Header and data as Paragraphs so all text wraps and doesn't overlap
        header_style = ParagraphStyle(
            name='SummaryTableHeader',
            parent=styles['Normal'],
            fontSize=8,
            fontName='Helvetica-Bold',
        )
        cell_style = ParagraphStyle(
            name='SummaryTableCell',
            parent=styles['Normal'],
            fontSize=8,
        )
        header = [Paragraph("Sample", header_style)] + [Paragraph(c, header_style) for c in col_order]
        rows = [header]
        for basename, d in desc_rows:
            row = [Paragraph(str(basename), cell_style)] + [
                Paragraph(str(d.get(k, '')), cell_style) for k in col_order
            ]
            rows.append(row)
        if len(rows) > 1:
            col_width = min(3.0 * cm, (14 * cm) / len(header))
            t = Table(rows, colWidths=[2.5 * cm] + [col_width] * (len(header) - 1))
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]))
            story.append(KeepTogether([
                Paragraph("Descriptors (all samples)", styles['Heading2']),
                t,
                Spacer(1, 0.5 * cm),
            ]))

    # (4) Guinier overplots
    guinier = [(s.get('guinier_path'), s.get('basename', '')) for s in samples if s.get('guinier_path')]
    if guinier:
        fig_path = _fig_multi_derived_dat(
            guinier,
            xlabel='q² (nm⁻²)',
            ylabel='log(I) (a.u.)',
            title='Guinier: all samples',
            x_col='q^2',
            y_col='log(I)',
        )
        if fig_path:
            temp_paths.append(fig_path)
            _add_image_if_exists(story, fig_path, "Guinier: all samples", temp_paths, styles)

    # (5) Kratky overplots
    kratky = [(s.get('kratky_path'), s.get('basename', '')) for s in samples if s.get('kratky_path')]
    if kratky:
        fig_path = _fig_multi_derived_dat(
            kratky,
            xlabel='q (nm⁻¹)',
            ylabel='I·q² (a.u.)',
            title='Kratky: all samples',
            x_col='q',
            y_col='I * q^2',
        )
        if fig_path:
            temp_paths.append(fig_path)
            _add_image_if_exists(story, fig_path, "Kratky: all samples", temp_paths, styles)

    # (6) Log-log overplots (columns may be log(q), log(I) or two unnamed; use iloc if needed)
    loglog = [(s.get('loglog_path'), s.get('basename', '')) for s in samples if s.get('loglog_path')]
    if loglog:
        fig_path = _fig_multi_derived_dat(
            loglog,
            xlabel='log(q)',
            ylabel='log(I)',
            title='Log-log: all samples',
            x_col='log(q)',
            y_col='log(I)',
        )
        if fig_path is None:
            fig_path = _fig_multi_derived_dat(
                loglog,
                xlabel='log(q)',
                ylabel='log(I)',
                title='Log-log: all samples',
                x_col='_iloc0',
                y_col='_iloc1',
            )
        if fig_path:
            temp_paths.append(fig_path)
            _add_image_if_exists(story, fig_path, "Log-log: all samples", temp_paths, styles)

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


_RE_MD_IMG = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _paragraph_from_plain(text: str, style: Any) -> Paragraph:
    t = xml_escape(text.strip())
    return Paragraph(t.replace("\n", "<br/>"), style)


def _is_md_table_separator_row(cells: List[str]) -> bool:
    if not cells:
        return False
    for c in cells:
        s = (c or "").strip().replace(" ", "")
        if not re.fullmatch(r":?-{3,}:?", s):
            return False
    return True


def build_pdf_from_assembled_markdown(
    md_text: str,
    output_path: str,
    *,
    markdown_base_dir: Optional[str] = None,
) -> None:
    """
    Render assembled Markdown (headings, paragraphs, ``![alt](path)`` images, pipe tables) to a PDF using ReportLab.

    Relative image paths are resolved against ``markdown_base_dir`` when provided (typically the directory
    containing the assembled ``.md`` file).
    """
    styles = getSampleStyleSheet()
    story: list = []
    body_style = styles["Normal"]
    h1_style = styles["Heading1"]
    h2_style = styles["Heading2"]
    h3_style = styles["Heading3"]
    small = ParagraphStyle(name="MdSmall", parent=body_style, fontSize=8, leading=10)
    lines = md_text.splitlines()
    i = 0
    buf: List[str] = []

    def flush_buf() -> None:
        nonlocal buf
        if not buf:
            return
        text = "\n".join(buf).strip()
        buf = []
        if not text:
            return
        story.append(_paragraph_from_plain(text, body_style))
        story.append(Spacer(1, 0.2 * cm))

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush_buf()
            i += 1
            continue
        if stripped == "---":
            flush_buf()
            story.append(Spacer(1, 0.3 * cm))
            i += 1
            continue
        if stripped.startswith("|") and "|" in stripped[1:]:
            flush_buf()
            table_lines: List[str] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            rows_raw = []
            for ln in table_lines:
                s = ln.strip()
                if s.startswith("|"):
                    s = s[1:]
                if s.endswith("|"):
                    s = s[:-1]
                rows_raw.append([c.strip() for c in s.split("|")])
            rows_data: List[List[str]] = []
            for row in rows_raw:
                if not row:
                    continue
                if _is_md_table_separator_row(row):
                    continue
                rows_data.append(row)
            if rows_data:
                rp_rows: List[List[Paragraph]] = []
                for row in rows_data:
                    rp_rows.append([Paragraph(xml_escape(c or ""), small) for c in row])
                ncols = max(len(r) for r in rows_data)
                col_w = (14 * cm) / max(ncols, 1)
                t = Table(rp_rows, colWidths=[col_w] * ncols)
                t.setStyle(
                    TableStyle(
                        [
                            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                        ]
                    )
                )
                story.append(t)
                story.append(Spacer(1, 0.3 * cm))
            continue
        if stripped.startswith("### "):
            flush_buf()
            story.append(Paragraph(xml_escape(stripped[4:].strip()), h3_style))
            story.append(Spacer(1, 0.2 * cm))
            i += 1
            continue
        if stripped.startswith("## "):
            flush_buf()
            story.append(Paragraph(xml_escape(stripped[3:].strip()), h2_style))
            story.append(Spacer(1, 0.25 * cm))
            i += 1
            continue
        if stripped.startswith("# "):
            flush_buf()
            story.append(Paragraph(xml_escape(stripped[2:].strip()), h1_style))
            story.append(Spacer(1, 0.3 * cm))
            i += 1
            continue
        if "![" in stripped:
            flush_buf()
            pos = 0
            for m in _RE_MD_IMG.finditer(stripped):
                before = stripped[pos : m.start()].strip()
                if before:
                    story.append(_paragraph_from_plain(before, body_style))
                img_path = m.group(2).strip().strip('"').strip("'")
                if img_path and not os.path.isabs(img_path) and markdown_base_dir:
                    img_path = os.path.normpath(os.path.join(markdown_base_dir, img_path))
                if img_path and os.path.isfile(img_path):
                    try:
                        img = Image(img_path, width=REPORT_IMAGE_WIDTH, height=REPORT_IMAGE_WIDTH * 0.6)
                        story.append(img)
                        story.append(Spacer(1, 0.3 * cm))
                    except Exception:
                        story.append(Paragraph("(image render error)", body_style))
                else:
                    story.append(Paragraph("(missing image)", body_style))
                pos = m.end()
            tail = stripped[pos:].strip()
            if tail:
                story.append(_paragraph_from_plain(tail, body_style))
            i += 1
            continue
        buf.append(line)
        i += 1
    flush_buf()
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )
    if not story:
        story.append(Paragraph("(empty report)", body_style))
    doc.build(story)
