import sqlite3

import pandas as pd
import pytest

from src.load import (
    LoadError,
    connect,
    foreign_key_violations,
    get_table_counts,
    initialize_schema,
    load_transactions,
)


def make_frame(rows: list[tuple]) -> pd.DataFrame:
    """rows: (transaction_id, date, customer_id, region, product_id, name, category, qty, price_minor)

    Prices are integer minor units (cents), exactly like the real pipeline produces.
    """
    df = pd.DataFrame(
        rows,
        columns=[
            "transaction_id", "order_date", "customer_id", "region", "product_id",
            "product_name", "category", "quantity", "unit_price_minor",
        ],
    )
    df["order_date"] = pd.to_datetime(df["order_date"])
    df["sales_amount_minor"] = df["quantity"] * df["unit_price_minor"]
    return df


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "test.db")
    initialize_schema(connection)
    yield connection
    connection.close()


FIRST_BATCH = [
    ("TXN-000001", "2024-01-15", "CUST-1", "Europe", "PRD-1", "Mouse", "Electronics", 2, 2550),
    ("TXN-000002", "2024-01-15", "CUST-2", "Africa", "PRD-2", "Chair", "Office Supplies", 1, 9900),
]


def test_schema_creates_all_four_tables(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"FactSales", "DimProduct", "DimCustomer", "DimDate"} <= tables


def test_schema_can_be_applied_twice(conn):
    initialize_schema(conn)  # must not raise or wipe data
    assert get_table_counts(conn)["FactSales"] == 0


def test_money_columns_are_declared_and_stored_as_integers(conn):
    columns = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(FactSales)")}
    assert columns["UnitPriceMinor"] == "INTEGER"
    assert columns["SalesAmountMinor"] == "INTEGER"
    assert "UnitPrice" not in columns and "SalesAmount" not in columns  # no REAL money columns

    load_transactions(conn, make_frame(FIRST_BATCH))
    types = conn.execute("SELECT DISTINCT typeof(UnitPriceMinor), typeof(SalesAmountMinor) FROM FactSales").fetchall()
    assert types == [("integer", "integer")]


def test_foreign_keys_are_enforced_on_the_connection(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO FactSales (TransactionID, ProductKey, CustomerKey, DateKey, Quantity, UnitPriceMinor, SalesAmountMinor) "
            "VALUES ('TXN-999999', 1, 1, 20240101, 1, 100, 100)"
        )


def test_database_rejects_a_sales_amount_that_does_not_match_quantity_times_price(conn):
    load_transactions(conn, make_frame(FIRST_BATCH))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO FactSales (TransactionID, ProductKey, CustomerKey, DateKey, Quantity, UnitPriceMinor, SalesAmountMinor) "
            "VALUES ('TXN-000003', 1, 1, 20240115, 2, 2550, 5000)"  # 2 * 2550 is 5100, not 5000
        )


def test_load_populates_fact_and_dimensions(conn):
    result = load_transactions(conn, make_frame(FIRST_BATCH))

    assert (result.inserted, result.skipped_existing) == (2, 0)
    assert get_table_counts(conn) == {"DimProduct": 2, "DimCustomer": 2, "DimDate": 1, "FactSales": 2}
    row = conn.execute(
        "SELECT Quantity, UnitPriceMinor, SalesAmountMinor, DateKey FROM FactSales WHERE TransactionID='TXN-000001'"
    ).fetchone()
    assert row == (2, 2550, 5100, 20240115)
    assert conn.execute("SELECT FullDate, Day, Month, MonthName, Quarter, Year FROM DimDate").fetchone() == (
        "2024-01-15", 15, 1, "January", 1, 2024,
    )


def test_foreign_key_integrity_after_load(conn):
    load_transactions(conn, make_frame(FIRST_BATCH))
    assert foreign_key_violations(conn) == []
    orphans = conn.execute(
        "SELECT COUNT(*) FROM FactSales f LEFT JOIN DimProduct p ON p.ProductKey = f.ProductKey WHERE p.ProductKey IS NULL"
    ).fetchone()[0]
    assert orphans == 0


def test_loading_same_records_twice_does_not_duplicate(conn):
    load_transactions(conn, make_frame(FIRST_BATCH))
    second = load_transactions(conn, make_frame(FIRST_BATCH))

    assert (second.inserted, second.skipped_existing) == (0, 2)
    assert get_table_counts(conn)["FactSales"] == 2


def test_incremental_load_inserts_only_new_transactions(conn):
    load_transactions(conn, make_frame(FIRST_BATCH))
    mixed = FIRST_BATCH + [("TXN-000003", "2024-02-01", "CUST-1", "Europe", "PRD-2", "Chair", "Office Supplies", 4, 9900)]

    result = load_transactions(conn, make_frame(mixed))

    assert (result.inserted, result.skipped_existing) == (1, 2)
    counts = get_table_counts(conn)
    assert counts["FactSales"] == 3
    assert counts["DimCustomer"] == 2  # CUST-1 reused, not duplicated
    assert counts["DimDate"] == 2


def test_existing_dimension_members_keep_their_first_seen_attributes(conn):
    """Documents the ACTUAL behaviour: dimension rows are insert-only (first seen wins).

    The loader uses ``ON CONFLICT DO NOTHING``. Later transactions that carry a different
    region, product name or category do NOT update the dimension row (this is neither
    SCD Type 1 overwrite nor SCD Type 2 history).
    """
    load_transactions(conn, make_frame(FIRST_BATCH))
    changed = [
        ("TXN-000010", "2024-03-01", "CUST-1", "Asia Pacific", "PRD-1", "Renamed Mouse", "Gadgets", 1, 1000),
    ]

    result = load_transactions(conn, make_frame(changed))

    assert result.inserted == 1  # the new fact row is loaded and linked to the existing members
    assert get_table_counts(conn)["DimCustomer"] == 2
    assert get_table_counts(conn)["DimProduct"] == 2
    assert conn.execute("SELECT Region FROM DimCustomer WHERE CustomerID='CUST-1'").fetchone() == ("Europe",)
    assert conn.execute(
        "SELECT ProductName, Category FROM DimProduct WHERE ProductID='PRD-1'"
    ).fetchone() == ("Mouse", "Electronics")
    assert foreign_key_violations(conn) == []


def test_first_row_wins_when_a_new_batch_has_conflicting_attributes_for_one_member(conn):
    batch = [
        ("TXN-000020", "2024-03-01", "CUST-9", "Europe", "PRD-9", "Lamp", "Home", 1, 1000),
        ("TXN-000021", "2024-03-02", "CUST-9", "Africa", "PRD-9", "Desk Lamp", "Furniture", 1, 1000),
    ]
    load_transactions(conn, make_frame(batch))
    assert conn.execute("SELECT Region FROM DimCustomer WHERE CustomerID='CUST-9'").fetchall() == [("Europe",)]
    assert conn.execute("SELECT ProductName, Category FROM DimProduct WHERE ProductID='PRD-9'").fetchall() == [
        ("Lamp", "Home")
    ]


def test_failed_load_is_rolled_back_completely(conn):
    bad = make_frame(
        FIRST_BATCH + [("TXN-000003", "2024-01-16", "CUST-3", "Africa", "PRD-3", "Lamp", "Home", 1, 1000)]
    )
    bad.loc[2, "sales_amount_minor"] = -1  # violates the CHECK constraints

    with pytest.raises(LoadError):
        load_transactions(conn, bad)

    assert get_table_counts(conn) == {"DimProduct": 0, "DimCustomer": 0, "DimDate": 0, "FactSales": 0}


def test_empty_frame_is_a_no_op(conn):
    result = load_transactions(conn, make_frame(FIRST_BATCH).iloc[0:0])
    assert (result.inserted, result.skipped_existing) == (0, 0)


def test_database_created_with_the_old_real_schema_is_detected(tmp_path):
    legacy_path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(legacy_path)
    legacy.execute(
        "CREATE TABLE FactSales (TransactionID TEXT PRIMARY KEY, ProductKey INTEGER, CustomerKey INTEGER, "
        "DateKey INTEGER, Quantity INTEGER, UnitPrice REAL, SalesAmount REAL)"
    )
    legacy.commit()
    legacy.close()

    connection = connect(legacy_path)
    try:
        with pytest.raises(LoadError, match="old schema"):
            initialize_schema(connection)
    finally:
        connection.close()
