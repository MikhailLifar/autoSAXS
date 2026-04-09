from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .common import SingletonPathExpressionArg, coerce_singleton_path_expression


def report_individual(
    directory: SingletonPathExpressionArg,
    basename: str,
    output_dir: str = ".",
    *,
    output_path: Optional[str] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Build a per-sample PDF report from an existing pipeline directory.
    """
    from ..report import write_individual_report_pdf

    _ = use_cache  # report generation does not use caching; kept for CLI parity
    directory = coerce_singleton_path_expression(directory)
    directory_path = directory.unwrap()[0]
    if output_path is None:
        output_path = os.path.join(output_dir, f"{basename}_report.pdf")
    path = write_individual_report_pdf(directory_path, basename, output_path=output_path)
    return {"report_pdf_path": path}

