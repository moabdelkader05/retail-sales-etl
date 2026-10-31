"""Extract step: read raw CSV files without modifying them."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.config import LINEAGE_COLUMNS, SOURCE_COLUMNS

logger = logging.getLogger(__name__)


class ExtractError(Exception):
    """Raised when raw source files cannot be found or read."""


def find_csv_files(raw_dir: Path) -> list[Path]:
    """Return all CSV files in ``raw_dir`` sorted by name (stable processing order)."""
    if not raw_dir.is_dir():
        raise ExtractError(f"Raw data directory does not exist: {raw_dir}")
    return sorted(raw_dir.glob("*.csv"))


def read_csv_file(path: Path) -> pd.DataFrame:
    """Read one CSV file as text and add lineage columns.

    Every value is read as a string so that no information is lost or silently
    converted before the transform step. The file itself is only opened for reading.
    """
    try:
        df = pd.read_csv(
            path,
            dtype=str,
            keep_default_na=False,
            skipinitialspace=True,
            encoding="utf-8-sig",  # also handles the BOM that Excel adds
        )
    except (OSError, UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise ExtractError(f"Could not read {path.name}: {exc}") from exc

    df.columns = [str(column).strip().lower() for column in df.columns]
    missing_columns = [column for column in SOURCE_COLUMNS if column not in df.columns]
    if missing_columns:
        raise ExtractError(f"{path.name} is missing required columns: {missing_columns}")

    df = df[SOURCE_COLUMNS].copy()
    df["source_file"] = path.name
    df["source_row"] = range(2, len(df) + 2)  # line number in the file (line 1 is the header)
    return df


def extract_all(raw_dir: Path) -> tuple[pd.DataFrame, list[str]]:
    """Read every CSV in ``raw_dir`` and return one DataFrame plus the file names."""
    files = find_csv_files(raw_dir)
    if not files:
        raise ExtractError(f"No CSV files found in {raw_dir}")

    frames = []
    for path in files:
        frame = read_csv_file(path)
        logger.info("Extracted %d records from %s", len(frame), path.name)
        frames.append(frame)

    combined = pd.concat(frames, ignore_index=True)
    return combined[SOURCE_COLUMNS + LINEAGE_COLUMNS], [path.name for path in files]
