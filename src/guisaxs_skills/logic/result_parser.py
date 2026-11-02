"""Parse autosaxs CLI machine results (framed JSON only)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from autosaxs.cli.request_codec import (
    envelope_to_outcome_fields,
    parse_result_frame,
)


def parse_skill_result_stdout(text: str) -> Tuple[Dict[str, Any], Optional[bool], Optional[bool]]:
    """
    Parse CLI stdout for the framed JSON result envelope.

    Human / free-text logs outside the frame are ignored. Returns
    ``(result_dict, process_ok, science_ok)``; oks are ``None`` when the frame
    is missing or omits those fields.
    """
    envelope = parse_result_frame(text)
    return envelope_to_outcome_fields(envelope)
