"""Data quality report: numbers are computed from the actual pipeline results."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.config import SOURCE_COLUMNS
from src.validate import NUMERIC_REASONS

# For these columns the trimmed original text is kept in "<column>_raw".
_RAW_COLUMNS = {"order_date", "quantity", "unit_price"}


def count_missing_values(cleaned: pd.DataFrame) -> dict:
    """Count missing values per source column (before any gap is filled)."""
    by_column = {}
    for column in SOURCE_COLUMNS:
        source_column = f"{column}_raw" if column in _RAW_COLUMNS else column
        by_column[column] = int(cleaned[source_column].isna().sum())
    return {"total": sum(by_column.values()), "by_column": by_column}


def count_invalid_numeric(reason_counts: dict[str, int], rejected_reasons: pd.Series) -> dict:
    """Count rows with a present-but-invalid quantity or unit price."""
    has_numeric_problem = (
        rejected_reasons.apply(lambda text: any(reason in text.split(";") for reason in NUMERIC_REASONS))
        if not rejected_reasons.empty
        else pd.Series(dtype=bool)
    )
    return {
        "records_with_invalid_numeric_values": int(has_numeric_problem.sum()),
        "invalid_quantity": reason_counts.get("INVALID_QUANTITY", 0),
        "invalid_unit_price": reason_counts.get("INVALID_UNIT_PRICE", 0),
    }


def save_report(report: dict, reports_dir: Path, run_stamp: datetime) -> Path:
    """Write the report as JSON, named after the execution time."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"data_quality_report_{run_stamp:%Y%m%d_%H%M%S_%f}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
