import hashlib
from pathlib import Path

import pandas as pd
import pytest

from src.config import SOURCE_COLUMNS
from src.extract import ExtractError, extract_all, find_csv_files, read_csv_file
from src.generate_data import write_incremental_batch, write_sample_dataset
from tests.conftest import EXPECTED_TOTAL, write_csv


def test_extract_reads_all_rows_as_text_with_lineage(raw_dir):
    df, files = extract_all(raw_dir)

    assert files == ["sales_sample.csv"]
    assert len(df) == EXPECTED_TOTAL
    assert list(df.columns) == SOURCE_COLUMNS + ["source_file", "source_row"]
    assert df["source_file"].eq("sales_sample.csv").all()
    assert df["source_row"].iloc[0] == 2  # line 1 is the header
    # values are untouched text: whitespace and symbols are preserved for the transform step
    assert df.loc[1, "transaction_id"] == "txn-000002 "
    assert df.loc[1, "unit_price"] == "$1,299.99"


def test_extract_combines_multiple_files_in_name_order(tmp_path):
    write_csv(tmp_path / "b.csv", [["TXN-000002", "2024-01-01", "C1", "R", "P1", "N", "C", "1", "1.00"]])
    write_csv(tmp_path / "a.csv", [["TXN-000001", "2024-01-01", "C1", "R", "P1", "N", "C", "1", "1.00"]])
    (tmp_path / "notes.txt").write_text("not a csv")

    df, files = extract_all(tmp_path)

    assert files == ["a.csv", "b.csv"]
    assert df["transaction_id"].tolist() == ["TXN-000001", "TXN-000002"]


def test_new_csv_file_is_picked_up(raw_dir):
    assert len(find_csv_files(raw_dir)) == 1
    write_csv(raw_dir / "sales_new.csv", [["TXN-000100", "2024-05-01", "C1", "R", "P1", "N", "C", "1", "1.00"]])
    df, files = extract_all(raw_dir)
    assert files == ["sales_new.csv", "sales_sample.csv"]
    assert len(df) == EXPECTED_TOTAL + 1


def test_missing_required_column_raises(tmp_path):
    write_csv(tmp_path / "bad.csv", [["TXN-000001", "2024-01-01"]], columns=["transaction_id", "order_date"])
    with pytest.raises(ExtractError, match="missing required columns"):
        read_csv_file(tmp_path / "bad.csv")


def test_empty_folder_and_missing_folder_raise(tmp_path):
    with pytest.raises(ExtractError, match="No CSV files"):
        extract_all(tmp_path)
    with pytest.raises(ExtractError, match="does not exist"):
        extract_all(tmp_path / "nope")


def test_extract_does_not_modify_raw_files(raw_dir):
    path = raw_dir / "sales_sample.csv"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    extract_all(raw_dir)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_generated_sample_dataset_has_about_1000_rows(tmp_path):
    paths = write_sample_dataset(tmp_path)
    total = sum(len(pd.read_csv(path, dtype=str, keep_default_na=False)) for path in paths)
    assert total == 1000


def test_generated_batch_adds_new_and_repeated_rows(tmp_path):
    write_sample_dataset(tmp_path)
    batch = write_incremental_batch(tmp_path)
    assert isinstance(batch, Path) and batch.name == "sales_batch_2.csv"
    df = pd.read_csv(batch, dtype=str, keep_default_na=False)
    assert len(df) > 100
