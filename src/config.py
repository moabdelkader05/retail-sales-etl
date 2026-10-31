"""Central configuration: paths, column names, validation limits and logging setup."""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

# --------------------------------------------------------------------------
# Paths (always relative to the project root, never hard-coded absolute paths)
# --------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent

RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed"
REJECTED_DATA_DIR = PROJECT_ROOT / "data" / "rejected"
REPORTS_DIR = PROJECT_ROOT / "reports"
LOGS_DIR = PROJECT_ROOT / "logs"

DATABASE_PATH = PROJECT_ROOT / "database" / "retail_dw.db"
SCHEMA_PATH = PROJECT_ROOT / "database" / "schema.sql"
ANALYTICS_SQL_PATH = PROJECT_ROOT / "sql" / "analytics_queries.sql"

# --------------------------------------------------------------------------
# Source file contract
# --------------------------------------------------------------------------
SOURCE_COLUMNS: list[str] = [
    "transaction_id",
    "order_date",
    "customer_id",
    "region",
    "product_id",
    "product_name",
    "category",
    "quantity",
    "unit_price",
]
# Columns added by the extract step so every record can be traced to its file.
LINEAGE_COLUMNS: list[str] = ["source_file", "source_row"]

# --------------------------------------------------------------------------
# Cleaning rules
# --------------------------------------------------------------------------
# Text values that are treated as "missing" after trimming (compared in upper case).
MISSING_TOKENS: frozenset[str] = frozenset({"", "NA", "N/A", "NULL", "NONE", "NAN", "-", "?"})

# Date formats accepted from the different sources, tried in this order.
# Ambiguous values such as 03/04/2024 are interpreted as day/month/year.
DATE_FORMATS: list[str] = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%b %d, %Y", "%Y/%m/%d"]

# Defaults used only when a missing text attribute cannot be inferred.
DEFAULT_REGION = "Unknown"
DEFAULT_CATEGORY = "Uncategorized"
DEFAULT_PRODUCT_NAME = "Unknown Product"

# --------------------------------------------------------------------------
# Validation rules
# --------------------------------------------------------------------------
TRANSACTION_ID_PATTERN = r"TXN-\d{6}"
MIN_ORDER_DATE = date(2020, 1, 1)  # earlier dates are considered data errors
MIN_QUANTITY = 1
MAX_QUANTITY = 100

# Money is handled as INTEGER minor units (cents) everywhere after parsing,
# so no floating point rounding can ever change an amount: 19.99 is stored as 1999.
MINOR_UNITS_PER_UNIT = 100
MIN_UNIT_PRICE_MINOR = 1  # 0.01
MAX_UNIT_PRICE_MINOR = 1_000_000  # 10,000.00
# A valid price is a plain decimal number with at most 2 decimal places (after removing
# currency symbols, spaces and thousands commas). "19.999", "-5", "1e3" and "free" are invalid.
UNIT_PRICE_PATTERN = r"\d+(?:\.\d{1,2})?"

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def setup_logging(log_dir: Path = LOGS_DIR, level: int = logging.INFO) -> None:
    """Log to the console and to ``logs/pipeline.log``."""
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):  # avoid duplicate handlers on repeated calls
        root.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    file_handler = logging.FileHandler(log_dir / "pipeline.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(console)
    root.addHandler(file_handler)
