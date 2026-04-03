#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from datetime import datetime
from pathlib import Path


# DateTime in TIFF raw header: format YYYY:MM:DD HH:MM:SS
_TIFF_DATETIME_RE = re.compile(rb"(\d{4}:\d{2}:\d{2}\s+\d{2}:\d{2}:\d{2})")


def get_tiff_datetime(tif_path: Path) -> datetime:
    """
    Read acquisition time from TIFF.

    Same logic as in `repos/scripts/2026_Pt_NPs_kinetic_analysis.py`:
    - Try TIFF tag 306 (DateTime) via PIL.
    - Fallback: parse first 300 bytes for YYYY:MM:DD HH:MM:SS.
    """
    if not tif_path.is_file():
        raise FileNotFoundError(f"TIFF file not found: {tif_path}")

    datetime_str: str | None = None
    try:
        from PIL import Image
        from PIL.TiffTags import TAGS

        with Image.open(tif_path) as img:
            img.load()
            tag_v2 = getattr(img, "tag_v2", None) or {}
            for tag_id, value in tag_v2.items():
                if tag_id == 306 or TAGS.get(tag_id) == "DateTime":
                    raw = (
                        value
                        if isinstance(value, str)
                        else (value[0] if isinstance(value, (tuple, list)) else None)
                    )
                    if raw is None:
                        continue
                    if isinstance(raw, bytes):
                        raw = raw.decode("latin-1", errors="replace")
                    s = str(raw).strip().strip("\x00")[:19]
                    if s:
                        datetime_str = s
                        break
    except ImportError:
        pass

    if not datetime_str:
        head = tif_path.read_bytes()[:300]
        match = _TIFF_DATETIME_RE.search(head)
        if match:
            datetime_str = match.group(1).decode("ascii")
        if not datetime_str:
            raise ValueError(
                "DateTime not found in TIFF header (tag 306 or YYYY:MM:DD HH:MM:SS in first 300 bytes): "
                f"{tif_path}"
            )

    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(datetime_str, fmt)
        except ValueError:
            continue
    raise ValueError(
        f"DateTime in TIFF could not be parsed (value={datetime_str!r}); "
        f"expected YYYY:MM:DD HH:MM:SS or YYYY-MM-DD HH:MM:SS: {tif_path}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract acquisition datetimes from .tif files into a CSV."
    )
    parser.add_argument(
        "glob",
        help='Glob expression for TIFFs, e.g. "data/run/raw/*.tif" or "data/**/*.tif".',
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tif_datetimes.csv"),
        help="Output CSV path (default: tif_datetimes.csv).",
    )
    parser.add_argument(
        "--datetime-format",
        default="iso",
        choices=("iso", "tiff"),
        help='Datetime string format in CSV: "iso" -> YYYY-MM-DDTHH:MM:SS, "tiff" -> YYYY:MM:DD HH:MM:SS.',
    )
    args = parser.parse_args()

    paths = sorted(Path().glob(args.glob))
    tif_paths = [p for p in paths if p.is_file() and p.suffix.lower() in {".tif", ".tiff"}]
    if not tif_paths:
        raise SystemExit(f"No .tif/.tiff files matched: {args.glob!r}")

    rows: list[tuple[str, str]] = []
    failures: list[tuple[str, str]] = []
    for p in tif_paths:
        try:
            dt = get_tiff_datetime(p)
            dt_str = dt.isoformat(timespec="seconds") if args.datetime_format == "iso" else dt.strftime("%Y:%m:%d %H:%M:%S")
            rows.append((p.stem, dt_str))
        except Exception as e:
            failures.append((p.name, str(e)))

    rows.sort(key=lambda x: x[0])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["basename", "datetime"])
        w.writerows(rows)

    print(f"Wrote {len(rows)} rows to {args.output}")
    if failures:
        print(f"{len(failures)} files failed.")
        for name, err in failures[:10]:
            print(f"- {name}: {err}")
        if len(failures) > 10:
            print(f"... {len(failures) - 10} more failures not shown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

