"""Shared test fixtures: a small, hand-made raw dataset with known data quality problems."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.config import SOURCE_COLUMNS

# Expected outcome of SAMPLE_ROWS (used by several test modules):
#   3 valid, 1 duplicate, 8 invalid  ->  12 input records
SAMPLE_ROWS = [
    # valid rows
    ["TXN-000001", "2024-01-15", "CUST-1001", "north america", "PRD-101", "wireless mouse", "electronics", "2", "25.50"],
    ["txn-000002 ", "15/02/2024", "CUST-1002", "Europe", "PRD-102", "Desk Lamp", "Home & Kitchen", "1", "$1,299.99"],
    ["TXN-000003", "Mar 05, 2024", "CUST-1001", "", "PRD-101", "", "", "3", "25.50"],
    # duplicate of the first row
    ["TXN-000001", "2024-01-15", "CUST-1001", "north america", "PRD-101", "wireless mouse", "electronics", "2", "25.50"],
    # invalid rows
    ["TXN-000004", "2024-02-30", "CUST-1003", "Africa", "PRD-103", "Notebook", "Office Supplies", "1", "4.00"],
    ["TXN-000005", "2024-03-01", "CUST-1003", "Africa", "PRD-103", "Notebook", "Office Supplies", "-2", "4.00"],
    ["TXN-000006", "2024-03-02", "CUST-1003", "Africa", "PRD-103", "Notebook", "Office Supplies", "2", "abc"],
    ["", "2024-03-03", "CUST-1003", "Africa", "PRD-103", "Notebook", "Office Supplies", "2", "4.00"],
    ["12345", "2024-03-03", "CUST-1003", "Africa", "PRD-103", "Notebook", "Office Supplies", "2", "4.00"],
    ["TXN-000007", "2099-01-01", "CUST-1003", "Africa", "PRD-103", "Notebook", "Office Supplies", "2", "4.00"],
    ["TXN-000008", "2024-03-04", "", "Africa", "PRD-103", "Notebook", "Office Supplies", "2", "4.00"],
    ["TXN-000009", "2024-03-05", "CUST-1003", "Africa", "PRD-103", "Notebook", "Office Supplies", "2.5", "4.00"],
]
EXPECTED_TOTAL = 12
EXPECTED_VALID = 3
EXPECTED_DUPLICATES = 1
EXPECTED_REJECTED = 9  # 8 invalid + 1 duplicate
EXPECTED_REVENUE = 1427.49  # 2*25.50 + 1299.99 + 3*25.50
EXPECTED_REVENUE_MINOR = 142749  # the same revenue as integer cents


def write_csv(path: Path, rows: list[list[str]], columns: list[str] | None = None) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns or SOURCE_COLUMNS)
        writer.writerows(rows)
    return path


@pytest.fixture
def raw_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "raw"
    directory.mkdir()
    write_csv(directory / "sales_sample.csv", SAMPLE_ROWS)
    return directory


@pytest.fixture
def pipeline_paths(tmp_path: Path, raw_dir: Path) -> dict[str, Path]:
    """Keyword arguments for run_pipeline() that keep every output inside tmp_path."""
    return {
        "raw_dir": raw_dir,
        "db_path": tmp_path / "warehouse.db",
        "processed_dir": tmp_path / "processed",
        "rejected_dir": tmp_path / "rejected",
        "reports_dir": tmp_path / "reports",
    }
