"""Tests for decentralized report fragment assembly."""
from __future__ import annotations

import csv
import os
import tempfile
import unittest

from autosaxs.core.report_fragments import (
    _sanitize_individual_fragment_body,
    assemble_individual_markdown,
    assemble_summary_markdown,
    discover_individual_fragment_paths,
    embed_individual_report_images,
    resolve_reference_value,
    write_skill_report_fragments,
)
from autosaxs.core.utils import write_saxs


class ReportFragmentsTest(unittest.TestCase):
    def test_individual_discover_and_assemble_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d1 = os.path.join(tmp, "subtracted")
            d2 = os.path.join(tmp, "plots")
            os.makedirs(d1)
            os.makedirs(d2)
            write_skill_report_fragments(
                d1,
                "mysample",
                "subtract",
                "Body subtract\n",
                summary_references=[{"role": "x", "path": "f.dat", "format": "dat"}],
                order=30,
            )
            write_skill_report_fragments(
                d2,
                "mysample",
                "plot",
                "Body plot\n",
                summary_references=[{"role": "y", "path": "g.dat", "format": "dat"}],
                order=40,
            )
            paths = discover_individual_fragment_paths(tmp, "mysample")
            self.assertEqual(len(paths), 2)
            md = assemble_individual_markdown(paths)
            self.assertIn("Body subtract", md)
            self.assertIn("Body plot", md)
            self.assertNotIn("_Section order", md)
            self.assertNotIn("report_individual.md", md)
            # subtract order 30 before plot 40
            self.assertLess(md.index("Body subtract"), md.index("Body plot"))

    def test_csv_cell_and_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = os.path.join(tmp, "t.csv")
            with open(csv_path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["a", "b"])
                w.writerow(["1", "2"])
                w.writerow(["3", "4"])
            ref_cell = {"role": "r", "path": "t.csv", "format": "csv", "cell": {"row": 1, "column": "b"}}
            self.assertEqual(resolve_reference_value(tmp, ref_cell), "4")
            ref_cols = {"role": "r", "path": "t.csv", "format": "csv", "row": 0, "columns": ["a", "b"]}
            v = resolve_reference_value(tmp, ref_cols)
            self.assertIn("a=1", v)
            self.assertIn("b=2", v)

    def test_summary_assemble_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sdir = os.path.join(tmp, "sub", "mysample_out")
            os.makedirs(sdir)
            write_skill_report_fragments(
                sdir,
                "mysample",
                "subtract",
                "ignored for summary md\n",
                summary_references=[{"role": "sub", "path": "x.dat", "format": "dat"}],
            )
            md = assemble_summary_markdown(tmp)
            self.assertIn("mysample", md)
            self.assertIn("# SAXS pipeline summary", md)
            self.assertIn("## Overview", md)
            self.assertIn("subtract", md)
            self.assertNotIn("YAML:", md)

    def test_individual_sanitizer_strips_legacy_lines(self) -> None:
        body = (
            "### Azimuthal integration\n"
            "Source image: `x.tif`\n"
            "Integrated curve: `int_x.dat` (npt=1000).\n"
            "### GNOM\n"
            "Summary: `best.yml`; scores: `fit_sizes_fits.csv`.\n"
            "![D(R)](dr.png)\n"
        )
        cleaned = _sanitize_individual_fragment_body(body)
        self.assertNotIn("`", cleaned)
        self.assertNotIn("Source image", cleaned)
        self.assertNotIn("Summary:", cleaned)
        self.assertIn("![D(R)](dr.png)", cleaned)

    def test_individual_embed_dat_curve(self) -> None:
        import numpy as np

        with tempfile.TemporaryDirectory() as tmp:
            q = np.linspace(0.1, 2.0, 20)
            I = np.exp(-(q ** 2))
            dat_path = os.path.join(tmp, "int_sample.dat")
            write_saxs(dat_path, q, I, None, {"type": "integrated"})
            md_dir = os.path.join(tmp, "reports")
            os.makedirs(md_dir)
            body = f"![Integrated curve]({os.path.basename(dat_path)})\n"
            dedup: dict = {}
            result = embed_individual_report_images(body, tmp, md_dir, dedup)
            self.assertIn("_rptimg_", result)
            self.assertTrue(any(fn.endswith(".png") for fn in os.listdir(md_dir)))

    def test_build_pdf_from_markdown(self) -> None:
        from autosaxs.core.report import build_pdf_from_assembled_markdown

        with tempfile.TemporaryDirectory() as tmp:
            pdf = os.path.join(tmp, "out.pdf")
            build_pdf_from_assembled_markdown("# Title\n\nHello world.\n", pdf)
            self.assertTrue(os.path.isfile(pdf))
            self.assertGreater(os.path.getsize(pdf), 100)


if __name__ == "__main__":
    unittest.main()
