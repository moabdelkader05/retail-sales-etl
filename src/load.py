"""Load step: create the SQLite star schema and incrementally load valid transactions."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.config import SCHEMA_PATH

logger = logging.getLogger(__name__)

TABLES = ("DimProduct", "DimCustomer", "DimDate", "FactSales")
_CHUNK_SIZE = 500  # keeps the number of SQL parameters per query small


class LoadError(Exception):
    """Raised when the database cannot be prepared or written."""


@dataclass(frozen=True)
class LoadResult:
    inserted: int
    skipped_existing: int


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the database with foreign key enforcement switched on."""
    try:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
    except (OSError, sqlite3.Error) as exc:
        raise LoadError(f"Could not open database {db_path}: {exc}") from exc
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        conn.close()
        raise LoadError("SQLite foreign key enforcement could not be enabled")
    return conn


def initialize_schema(conn: sqlite3.Connection, schema_path: Path = SCHEMA_PATH) -> None:
    """Create all warehouse tables from the SQL schema file (safe to run repeatedly)."""
    try:
        conn.executescript(Path(schema_path).read_text(encoding="utf-8"))
        # executescript can reset connection settings in some Python versions
        conn.execute("PRAGMA foreign_keys = ON")
        fact_columns = {row[1] for row in conn.execute("PRAGMA table_info(FactSales)")}
    except (OSError, sqlite3.Error) as exc:
        raise LoadError(f"Could not create schema from {schema_path}: {exc}") from exc

    if "UnitPriceMinor" not in fact_columns:
        raise LoadError(
            "The existing database uses the old schema (REAL UnitPrice/SalesAmount columns). "
            "Delete database/retail_dw.db or run `python main.py --reset-db` to rebuild it."
        )


def get_table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES}


def foreign_key_violations(conn: sqlite3.Connection) -> list[tuple]:
    """Return rows that break a foreign key (an empty list means the data is consistent)."""
    return conn.execute("PRAGMA foreign_key_check").fetchall()


def _existing_transaction_ids(conn: sqlite3.Connection, transaction_ids: list[str]) -> set[str]:
    existing: set[str] = set()
    for start in range(0, len(transaction_ids), _CHUNK_SIZE):
        chunk = transaction_ids[start : start + _CHUNK_SIZE]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT TransactionID FROM FactSales WHERE TransactionID IN ({placeholders})", chunk
        )
        existing.update(row[0] for row in rows)
    return existing


def _upsert_dimensions(conn: sqlite3.Connection, new_rows: pd.DataFrame) -> None:
    """Insert dimension members that do not exist yet (existing members are left unchanged)."""
    products = new_rows.drop_duplicates("product_id")
    conn.executemany(
        "INSERT INTO DimProduct (ProductID, ProductName, Category) VALUES (?, ?, ?) "
        "ON CONFLICT(ProductID) DO NOTHING",
        [(str(r.product_id), str(r.product_name), str(r.category)) for r in products.itertuples()],
    )

    customers = new_rows.drop_duplicates("customer_id")
    conn.executemany(
        "INSERT INTO DimCustomer (CustomerID, Region) VALUES (?, ?) ON CONFLICT(CustomerID) DO NOTHING",
        [(str(r.customer_id), str(r.region)) for r in customers.itertuples()],
    )

    dates = pd.Series(new_rows["order_date"].dropna().unique())
    date_rows = []
    for value in pd.to_datetime(dates):
        date_rows.append(
            (
                int(value.strftime("%Y%m%d")),
                value.strftime("%Y-%m-%d"),
                int(value.day),
                int(value.month),
                value.month_name(),
                int(value.quarter),
                int(value.year),
            )
        )
    conn.executemany(
        "INSERT INTO DimDate (DateKey, FullDate, Day, Month, MonthName, Quarter, Year) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(DateKey) DO NOTHING",
        date_rows,
    )


def load_transactions(conn: sqlite3.Connection, df: pd.DataFrame) -> LoadResult:
    """Insert only transactions whose TransactionID is not yet in FactSales.

    Everything happens inside one transaction: if any statement fails, nothing is saved.
    """
    if df.empty:
        return LoadResult(inserted=0, skipped_existing=0)

    transaction_ids = [str(value) for value in df["transaction_id"]]
    try:
        with conn:  # commits on success, rolls back on any exception
            existing = _existing_transaction_ids(conn, transaction_ids)
            new_rows = df.loc[~df["transaction_id"].isin(existing)]
            if new_rows.empty:
                return LoadResult(inserted=0, skipped_existing=len(df))

            _upsert_dimensions(conn, new_rows)
            product_keys = dict(conn.execute("SELECT ProductID, ProductKey FROM DimProduct"))
            customer_keys = dict(conn.execute("SELECT CustomerID, CustomerKey FROM DimCustomer"))

            fact_rows = [
                (
                    str(r.transaction_id),
                    product_keys[str(r.product_id)],
                    customer_keys[str(r.customer_id)],
                    int(r.order_date.strftime("%Y%m%d")),
                    int(r.quantity),
                    int(r.unit_price_minor),
                    int(r.sales_amount_minor),
                )
                for r in new_rows.itertuples()
            ]
            cursor = conn.executemany(
                "INSERT INTO FactSales "
                "(TransactionID, ProductKey, CustomerKey, DateKey, Quantity, UnitPriceMinor, SalesAmountMinor) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(TransactionID) DO NOTHING",
                fact_rows,
            )
            inserted = cursor.rowcount
    except sqlite3.Error as exc:
        logger.error("Database load failed and was rolled back: %s", exc)
        raise LoadError(f"Database load failed and was rolled back: {exc}") from exc

    return LoadResult(inserted=inserted, skipped_existing=len(df) - inserted)
