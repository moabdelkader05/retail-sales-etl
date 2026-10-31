"""Pipeline orchestration: extract -> transform -> validate -> load -> report."""

from __future__ import annotations

import logging
import time
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from src.config import (
    DATABASE_PATH,
    PROCESSED_DATA_DIR,
    RAW_DATA_DIR,
    REJECTED_DATA_DIR,
    REPORTS_DIR,
    SCHEMA_PATH,
    SOURCE_COLUMNS,
)
from src.extract import extract_all
from src.load import connect, foreign_key_violations, get_table_counts, initialize_schema, load_transactions
from src.report import count_invalid_numeric, count_missing_values, save_report
from src.transform import (
    add_sales_amount,
    clean_transactions,
    fill_missing_attributes,
    format_minor_units,
    separate_duplicates,
)
from src.validate import count_reasons, split_valid_invalid

logger = logging.getLogger(__name__)

PROCESSED_COLUMNS = [
    "transaction_id",
    "order_date",
    "customer_id",
    "region",
    "product_id",
    "product_name",
    "category",
    "quantity",
    "unit_price",  # decimal text derived from the minor units, for readability
    "unit_price_minor",  # source of truth: integer cents
    "sales_amount",  # decimal text derived from the minor units, for readability
    "sales_amount_minor",  # source of truth: integer cents
    "source_file",
    "source_row",
]


def _build_rejected_output(raw: pd.DataFrame, rejected_reasons: pd.Series) -> pd.DataFrame:
    """Rejected records are written with their ORIGINAL values plus the rejection reasons."""
    columns = SOURCE_COLUMNS + ["source_file", "source_row"]
    rejected = raw.loc[rejected_reasons.index, columns].copy()
    rejected["rejection_reason"] = rejected_reasons
    return rejected.sort_values(["source_file", "source_row"])


def run_pipeline(
    raw_dir: Path = RAW_DATA_DIR,
    db_path: Path = DATABASE_PATH,
    processed_dir: Path = PROCESSED_DATA_DIR,
    rejected_dir: Path = REJECTED_DATA_DIR,
    reports_dir: Path = REPORTS_DIR,
    schema_path: Path = SCHEMA_PATH,
    today: date | None = None,
) -> dict:
    """Run the full batch pipeline once and return the data quality report."""
    started_at = datetime.now(timezone.utc)
    timer = time.perf_counter()
    logger.info("Pipeline started (raw dir: %s)", raw_dir)

    # 1. Extract
    raw, source_files = extract_all(raw_dir)
    total_input = len(raw)

    # 2. Transform and clean
    cleaned = clean_transactions(raw)
    missing_values = count_missing_values(cleaned)
    cleaned, imputed_values = fill_missing_attributes(cleaned)

    # 3. Validate, then separate in-batch duplicates among the valid records
    valid, invalid = split_valid_invalid(cleaned, today)
    unique_valid, duplicates = separate_duplicates(valid)

    rejected_reasons = pd.concat([invalid["rejection_reason"], duplicates["rejection_reason"]])
    reason_counts = count_reasons(rejected_reasons)
    invalid_numeric = count_invalid_numeric(reason_counts, invalid["rejection_reason"])

    final_valid = add_sales_amount(unique_valid)
    rejected_output = _build_rejected_output(raw, rejected_reasons)
    logger.info(
        "Validation finished: %d valid, %d rejected (%d duplicates)",
        len(final_valid),
        len(rejected_output),
        len(duplicates),
    )

    # 4. Save processed and rejected records
    stamp = f"{started_at:%Y%m%d_%H%M%S_%f}"
    processed_dir.mkdir(parents=True, exist_ok=True)
    rejected_dir.mkdir(parents=True, exist_ok=True)
    processed_path = processed_dir / f"valid_transactions_{stamp}.csv"
    rejected_path = rejected_dir / f"rejected_transactions_{stamp}.csv"
    processed_output = final_valid.copy()
    processed_output["unit_price"] = format_minor_units(processed_output["unit_price_minor"])
    processed_output["sales_amount"] = format_minor_units(processed_output["sales_amount_minor"])
    processed_output[PROCESSED_COLUMNS].to_csv(processed_path, index=False, date_format="%Y-%m-%d")
    rejected_output.to_csv(rejected_path, index=False)

    # 5. Load into the warehouse (incremental, idempotent)
    with closing(connect(db_path)) as conn:
        initialize_schema(conn, schema_path)
        load_result = load_transactions(conn, final_valid)
        table_counts = get_table_counts(conn)
        fk_violations = len(foreign_key_violations(conn))
    logger.info(
        "Load finished: %d inserted, %d already in the warehouse",
        load_result.inserted,
        load_result.skipped_existing,
    )

    # 6. Data quality report
    report = {
        "execution_timestamp_utc": started_at.isoformat(timespec="seconds"),
        "duration_seconds": round(time.perf_counter() - timer, 3),
        "source_files": source_files,
        "total_input_records": total_input,
        "valid_records": len(final_valid),
        "rejected_records": len(rejected_output),
        "duplicate_records": len(duplicates),
        "missing_values": missing_values,
        "invalid_numeric_values": invalid_numeric,
        "rejection_reasons": reason_counts,
        "imputed_values": imputed_values,
        "records_loaded": load_result.inserted,
        "records_skipped_already_loaded": load_result.skipped_existing,
        "warehouse_row_counts": table_counts,
        "foreign_key_violations": fk_violations,
        "output_files": {
            "processed": processed_path.name,
            "rejected": rejected_path.name,
        },
    }
    report_path = save_report(report, reports_dir, started_at)
    report["output_files"]["report"] = report_path.name
    logger.info("Pipeline finished in %.2f s. Report: %s", report["duration_seconds"], report_path.name)
    return report
