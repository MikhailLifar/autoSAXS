from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .common import SingletonPathExpressionArg, coerce_singleton_path_expression


def report_summary(
    directory: SingletonPathExpressionArg,
    output_dir: str = ".",
    *,
    output_path: Optional[str] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Build a summary PDF report for all samples found inside an existing pipeline directory. The skill discovers samples and combines plots/tables where data exists.

    ### Arguments

    - `directory` (str): Path to the existing pipeline output directory.
    - `output_dir` (str, default `.`): Directory where the summary PDF is written.
    - `output_path` (str | None, default `None`): Optional explicit output PDF path. If not provided, defaults to `<output_dir>/summary_report.pdf`.
    - `use_cache` (bool, default `True`): Present for CLI parity; report generation does not use caching.

    ### Returns

    `dict[str, Any]` with:

    - `report_pdf_path`: Path to the generated summary PDF.

    ### Python usage

    ```python
    from autosaxs.skill import report_summary

    out = report_summary(
        directory="pipeline_out",
        output_dir="reports",
    )

    print(out["report_pdf_path"])
    ```

    ### CLI usage

    ```bash
    autosaxs report_summary pipeline_out --output-dir reports
    ```
    """
    from ..report import write_summary_report_pdf

    _ = use_cache  # report generation does not use caching; kept for CLI parity
    directory = coerce_singleton_path_expression(directory)
    directory_path = directory.unwrap()[0]
    if output_path is None:
        output_path = os.path.join(output_dir, "summary_report.pdf")
    path = write_summary_report_pdf(directory_path, output_path=output_path)
    return {"report_pdf_path": path}

