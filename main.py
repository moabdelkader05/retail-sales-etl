"""Command line entry point for the Retail Sales ETL pipeline.

Usage (PowerShell):
    python main.py                      # generate sample data if needed, then run the pipeline
    python main.py --analytics          # also print the SQL analytics results
    python main.py --add-sample-batch   # add a new CSV with new + already-loaded records, then run
    python main.py --reset-db           # delete the warehouse database first (full reload)
"""

from __future__ import annotations

import argparse
import logging
import sys
from contextlib import closing
from pathlib import Path

import pandas as pd

from src.analytics import run_analytics
from src.config import ANALYTICS_SQL_PATH, DATABASE_PATH, RAW_DATA_DIR, setup_logging
from src.extract import ExtractError, find_csv_files
from src.generate_data import write_incremental_batch, write_sample_dataset
from src.load import LoadError, connect
from src.pipeline import run_pipeline

logger = logging.getLogger("main")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retail Sales ETL Pipeline")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DATA_DIR, help="folder with raw CSV files")
    parser.add_argument("--db-path", type=Path, default=DATABASE_PATH, help="SQLite warehouse file")
    parser.add_argument("--analytics", action="store_true", help="print the SQL analytics results")
    parser.add_argument("--add-sample-batch", action="store_true", help="create sales_batch_2.csv before running")
    parser.add_argument("--reset-db", action="store_true", help="delete the warehouse database before running")
    return parser.parse_args(argv)


def print_summary(report: dict) -> None:
    missing = report["missing_values"]
    numeric = report["invalid_numeric_values"]
    print("\n=== Data Quality Report ===")
    print(f"Execution timestamp (UTC) : {report['execution_timestamp_utc']}")
    print(f"Source files              : {', '.join(report['source_files'])}")
    print(f"Total input records       : {report['total_input_records']}")
    print(f"Valid records             : {report['valid_records']}")
    print(f"Rejected records          : {report['rejected_records']}")
    print(f"  of which duplicates     : {report['duplicate_records']}")
    print(f"Missing values (total)    : {missing['total']}")
    print(f"Records w/ invalid numbers: {numeric['records_with_invalid_numeric_values']}")
    print(f"Loaded into warehouse     : {report['records_loaded']}")
    print(f"Skipped (already loaded)  : {report['records_skipped_already_loaded']}")
    print("Warehouse row counts      : " + ", ".join(f"{k}={v}" for k, v in report["warehouse_row_counts"].items()))
    print(f"Foreign key violations    : {report['foreign_key_violations']}")
    print(f"Report file               : reports/{report['output_files']['report']}")


def print_analytics(db_path: Path) -> None:
    with closing(connect(db_path)) as conn:
        results = run_analytics(conn, ANALYTICS_SQL_PATH)
    pd.set_option("display.width", 140)
    for name, frame in results.items():
        print(f"\n--- {name} ---")
        print(frame.to_string(index=False))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging()

    try:
        if args.reset_db and args.db_path.exists():
            args.db_path.unlink()
            logger.info("Deleted existing database %s", args.db_path)

        if not args.raw_dir.is_dir() or not find_csv_files(args.raw_dir):
            created = write_sample_dataset(args.raw_dir)
            logger.info("No raw data found. Generated sample files: %s", [p.name for p in created])
        if args.add_sample_batch:
            batch = write_incremental_batch(args.raw_dir)
            logger.info("Created sample batch file %s", batch.name)

        report = run_pipeline(raw_dir=args.raw_dir, db_path=args.db_path)
        print_summary(report)
        if args.analytics:
            print_analytics(args.db_path)
    except (ExtractError, LoadError) as exc:
        logger.error("Pipeline failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
