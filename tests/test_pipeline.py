import hashlib
import json
from contextlib import closing

import pandas as pd

from src.analytics import load_queries, run_analytics
from src.config import ANALYTICS_SQL_PATH
from src.generate_data import write_incremental_batch, write_sample_dataset
from src.load import connect, foreign_key_violations, get_table_counts
from src.pipeline import run_pipeline
from tests.conftest import (
    EXPECTED_DUPLICATES,
    EXPECTED_REJECTED,
    EXPECTED_REVENUE,
    EXPECTED_REVENUE_MINOR,
    EXPECTED_TOTAL,
    EXPECTED_VALID,
    write_csv,
)


def table_counts(db_path):
    with closing(connect(db_path)) as conn:
        return get_table_counts(conn)


def test_pipeline_report_matches_actual_results(pipeline_paths):
    report = run_pipeline(**pipeline_paths)

    assert report["total_input_records"] == EXPECTED_TOTAL
    assert report["valid_records"] == EXPECTED_VALID
    assert report["rejected_records"] == EXPECTED_REJECTED
    assert report["duplicate_records"] == EXPECTED_DUPLICATES
    assert report["valid_records"] + report["rejected_records"] == report["total_input_records"]
    assert report["records_loaded"] == EXPECTED_VALID
    assert report["invalid_numeric_values"] == {
        "records_with_invalid_numeric_values": 3,
        "invalid_quantity": 2,
        "invalid_unit_price": 1,
    }
    assert report["missing_values"]["by_column"]["transaction_id"] == 1
    assert report["missing_values"]["by_column"]["customer_id"] == 1
    assert report["foreign_key_violations"] == 0
    assert table_counts(pipeline_paths["db_path"]) == {
        "DimProduct": 2, "DimCustomer": 2, "DimDate": 3, "FactSales": EXPECTED_VALID,
    }


def test_report_is_saved_as_json_with_a_timestamp(pipeline_paths):
    report = run_pipeline(**pipeline_paths)

    saved = list(pipeline_paths["reports_dir"].glob("data_quality_report_*.json"))
    assert [path.name for path in saved] == [report["output_files"]["report"]]
    content = json.loads(saved[0].read_text(encoding="utf-8"))
    assert content["total_input_records"] == EXPECTED_TOTAL
    assert content["execution_timestamp_utc"]
    assert content["output_files"] == report["output_files"]
    assert content["output_files"]["report"] == saved[0].name

def test_rejected_records_are_preserved_with_reasons(pipeline_paths):
    run_pipeline(**pipeline_paths)

    rejected = pd.read_csv(next(pipeline_paths["rejected_dir"].glob("*.csv")), dtype=str, keep_default_na=False)
    assert len(rejected) == EXPECTED_REJECTED
    assert (rejected["rejection_reason"] != "").all()
    assert "DUPLICATE_TRANSACTION_ID" in set(rejected["rejection_reason"])
    # original values are kept so the problem can be traced back to the source file
    assert "12345" in set(rejected["transaction_id"])
    assert {"source_file", "source_row"} <= set(rejected.columns)


def test_processed_file_contains_only_valid_records_with_sales_amount(pipeline_paths):
    run_pipeline(**pipeline_paths)

    processed = pd.read_csv(next(pipeline_paths["processed_dir"].glob("*.csv")), dtype={"unit_price": str, "sales_amount": str})
    assert len(processed) == EXPECTED_VALID
    # integer minor units are the source of truth ...
    assert processed["sales_amount_minor"].sum() == EXPECTED_REVENUE_MINOR
    assert (processed["sales_amount_minor"] == processed["quantity"] * processed["unit_price_minor"]).all()
    # ... and the decimal columns are just a readable rendering of them
    assert sorted(processed["sales_amount"]) == ["1299.99", "51.00", "76.50"]
    assert sorted(processed["unit_price"]) == ["1299.99", "25.50", "25.50"]


def test_warehouse_revenue_matches_expected(pipeline_paths):
    run_pipeline(**pipeline_paths)
    with closing(connect(pipeline_paths["db_path"])) as conn:
        revenue_minor = conn.execute("SELECT SUM(SalesAmountMinor) FROM FactSales").fetchone()[0]
    assert revenue_minor == EXPECTED_REVENUE_MINOR  # exact integer arithmetic, no rounding involved
    assert revenue_minor / 100 == EXPECTED_REVENUE


def test_repeated_runs_do_not_create_duplicates(pipeline_paths):
    first = run_pipeline(**pipeline_paths)
    second = run_pipeline(**pipeline_paths)
    third = run_pipeline(**pipeline_paths)

    assert first["records_loaded"] == EXPECTED_VALID
    for later in (second, third):
        assert later["records_loaded"] == 0
        assert later["records_skipped_already_loaded"] == EXPECTED_VALID
        assert later["valid_records"] == EXPECTED_VALID
    assert table_counts(pipeline_paths["db_path"])["FactSales"] == EXPECTED_VALID


def test_incremental_load_picks_up_new_records_from_a_new_file(pipeline_paths):
    run_pipeline(**pipeline_paths)
    write_csv(
        pipeline_paths["raw_dir"] / "sales_new.csv",
        [
            # already loaded -> skipped
            ["TXN-000001", "2024-01-15", "CUST-1001", "North America", "PRD-101", "Wireless Mouse", "Electronics", "2", "25.50"],
            # new -> inserted (new customer, new product, new date)
            ["TXN-000100", "2025-02-01", "CUST-2001", "Africa", "PRD-900", "Tent", "Outdoors", "1", "100.00"],
            # new but invalid -> rejected
            ["TXN-000101", "2025-02-01", "CUST-2001", "Africa", "PRD-900", "Tent", "Outdoors", "0", "100.00"],
        ],
    )

    report = run_pipeline(**pipeline_paths)

    assert report["records_loaded"] == 1
    assert report["records_skipped_already_loaded"] == EXPECTED_VALID  # the 3 old valid rows are re-read
    counts = table_counts(pipeline_paths["db_path"])
    assert counts == {"DimProduct": 3, "DimCustomer": 3, "DimDate": 4, "FactSales": EXPECTED_VALID + 1}


def test_raw_files_are_never_modified(pipeline_paths):
    path = pipeline_paths["raw_dir"] / "sales_sample.csv"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    run_pipeline(**pipeline_paths)
    run_pipeline(**pipeline_paths)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_full_workflow_on_generated_dataset(tmp_path):
    raw_dir = tmp_path / "raw"
    paths = {
        "raw_dir": raw_dir,
        "db_path": tmp_path / "dw.db",
        "processed_dir": tmp_path / "processed",
        "rejected_dir": tmp_path / "rejected",
        "reports_dir": tmp_path / "reports",
    }
    write_sample_dataset(raw_dir)

    first = run_pipeline(**paths)
    assert first["total_input_records"] == 1000
    assert first["valid_records"] + first["rejected_records"] == 1000
    assert first["valid_records"] > 800
    assert first["records_loaded"] == first["valid_records"]
    assert first["foreign_key_violations"] == 0

    again = run_pipeline(**paths)
    assert again["records_loaded"] == 0
    assert table_counts(paths["db_path"])["FactSales"] == first["records_loaded"]

    write_incremental_batch(raw_dir)
    third = run_pipeline(**paths)
    assert third["records_loaded"] > 0
    assert third["records_skipped_already_loaded"] >= first["valid_records"]
    assert table_counts(paths["db_path"])["FactSales"] == first["records_loaded"] + third["records_loaded"]

    with closing(connect(paths["db_path"])) as conn:
        assert foreign_key_violations(conn) == []
        distinct = conn.execute("SELECT COUNT(DISTINCT TransactionID), COUNT(*) FROM FactSales").fetchone()
        assert distinct[0] == distinct[1]
        inconsistent = conn.execute(
            "SELECT COUNT(*) FROM FactSales WHERE SalesAmountMinor != Quantity * UnitPriceMinor"
        ).fetchone()[0]
        assert inconsistent == 0
        assert conn.execute("SELECT DISTINCT typeof(UnitPriceMinor), typeof(SalesAmountMinor) FROM FactSales").fetchall() == [
            ("integer", "integer")
        ]


def test_all_analytics_queries_run_against_the_schema(pipeline_paths):
    run_pipeline(**pipeline_paths)
    with closing(connect(pipeline_paths["db_path"])) as conn:
        results = run_analytics(conn)

    assert len(results) == len(load_queries()) == 9
    assert all(not frame.empty for frame in results.values())
    assert results["1. Total revenue"].iloc[0, 0] == EXPECTED_REVENUE
    assert results["2. Total number of transactions"].iloc[0, 0] == EXPECTED_VALID
    assert results["9. Yearly sales summary"]["Transactions"].sum() == EXPECTED_VALID


def test_average_is_named_transaction_amount_not_order_value(pipeline_paths):
    run_pipeline(**pipeline_paths)
    with closing(connect(pipeline_paths["db_path"])) as conn:
        results = run_analytics(conn)

    average = results["3. Average transaction amount"]
    assert list(average.columns) == ["AverageTransactionAmount"]
    assert average.iloc[0, 0] == round(EXPECTED_REVENUE / EXPECTED_VALID, 2)
    assert "AverageTransactionAmount" in results["9. Yearly sales summary"].columns
    assert not any("order value" in name.lower() for name in results)
    # the SQL comments may explain the rename, but no executable SQL may use the old name
    code = "\n".join(
        line for line in ANALYTICS_SQL_PATH.read_text(encoding="utf-8").lower().splitlines()
        if not line.strip().startswith("--")
    )
    assert "averageordervalue" not in code
    assert "order value" not in code


def test_analytics_revenue_is_consistent_across_queries(pipeline_paths):
    run_pipeline(**pipeline_paths)
    with closing(connect(pipeline_paths["db_path"])) as conn:
        results = run_analytics(conn)

    total = results["1. Total revenue"].iloc[0, 0]
    assert round(results["4. Monthly revenue"]["Revenue"].sum(), 2) == total
    assert round(results["6. Sales by product category"]["Revenue"].sum(), 2) == total
    assert round(results["7. Sales by region"]["Revenue"].sum(), 2) == total
    assert round(results["9. Yearly sales summary"]["Revenue"].sum(), 2) == total
    assert round(results["6. Sales by product category"]["RevenueSharePct"].sum()) == 100


def test_dimension_attributes_are_insert_only_even_for_default_values(pipeline_paths):
    """Documents the ACTUAL dimension behaviour end to end (first loaded value wins).

    CUST-7 first arrives without a region, so it is loaded as "Unknown". A later file brings the
    real region, but the existing DimCustomer row is not updated (ON CONFLICT DO NOTHING).
    """
    raw_dir = pipeline_paths["raw_dir"]
    for path in raw_dir.glob("*.csv"):
        path.unlink()
    write_csv(raw_dir / "a_first.csv", [
        ["TXN-000001", "2024-01-15", "CUST-7", "", "PRD-7", "Lamp", "Home", "1", "10.00"],
    ])
    run_pipeline(**pipeline_paths)

    write_csv(raw_dir / "b_second.csv", [
        ["TXN-000002", "2024-01-16", "CUST-7", "Europe", "PRD-7", "Lamp", "Home", "1", "10.00"],
    ])
    report = run_pipeline(**pipeline_paths)

    assert report["records_loaded"] == 1  # the new transaction is loaded and linked to CUST-7
    with closing(connect(pipeline_paths["db_path"])) as conn:
        regions = conn.execute("SELECT Region FROM DimCustomer WHERE CustomerID = 'CUST-7'").fetchall()
        facts = conn.execute("SELECT COUNT(*) FROM FactSales").fetchone()[0]
    assert regions == [("Unknown",)]  # not corrected to "Europe"
    assert facts == 2
