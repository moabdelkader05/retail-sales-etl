import pandas as pd
import pytest

from src.extract import extract_all
from src.transform import (
    add_sales_amount,
    clean_transactions,
    fill_missing_attributes,
    format_minor_units,
    parse_dates,
    parse_numeric,
    parse_price_minor,
    separate_duplicates,
    standardize_text,
)


def test_standardize_text_trims_collapses_spaces_and_flags_placeholders():
    result = standardize_text(pd.Series(["  hello   world ", "", "N/A", "null", "-", "ok"]))
    assert result.iloc[0] == "hello world"
    assert result.iloc[1:5].isna().all()
    assert result.iloc[5] == "ok"


def test_parse_dates_handles_all_supported_formats_and_invalid_values():
    dates = pd.Series(["2024-03-15", "15/03/2024", "15-03-2024", "Mar 15, 2024", "2024/03/15", "31/02/2024", "x", None])
    parsed = parse_dates(dates)
    assert parsed.iloc[:5].eq(pd.Timestamp("2024-03-15")).all()
    assert parsed.iloc[5:].isna().all()


def test_parse_numeric_converts_numbers_and_coerces_bad_values():
    numbers = parse_numeric(pd.Series(["3", "2.5", "abc", None]))
    assert numbers.iloc[0] == 3.0
    assert numbers.iloc[1] == 2.5
    assert numbers.iloc[2:].isna().all()


@pytest.mark.parametrize(
    ("text", "expected_minor"),
    [
        ("$1,299.99", 129999),  # currency symbol and thousands separator
        ("25.50", 2550),
        ("25.5", 2550),
        ("5", 500),
        ("0.10", 10),
        ("19.99", 1999),  # 19.99 * 100 is 1998.9999999999998 in floating point; must still be 1999
        ("1.15", 115),
        ("4.35", 435),
        ("€ 7.00", 700),
    ],
)
def test_parse_price_minor_gives_exact_integer_cents(text, expected_minor):
    result = parse_price_minor(pd.Series([text])).iloc[0]
    assert result == expected_minor
    assert float(result).is_integer()


@pytest.mark.parametrize("text", ["19.999", "-5", "-19.99", "free", "$", "1e3", "25.", ".5", "1.2.3", "", None])
def test_parse_price_minor_rejects_anything_that_is_not_a_plain_price(text):
    # Prices with more than 2 decimals are rejected, never silently rounded.
    assert parse_price_minor(pd.Series([text])).isna().all()


def test_format_minor_units_round_trips_to_two_decimals():
    minor = pd.Series([129999, 5, 100, 1999, 0])
    assert format_minor_units(minor).tolist() == ["1299.99", "0.05", "1.00", "19.99", "0.00"]


def test_clean_transactions_standardizes_text_and_types(raw_dir):
    raw, _ = extract_all(raw_dir)
    cleaned = clean_transactions(raw)

    assert cleaned.loc[1, "transaction_id"] == "TXN-000002"  # trimmed and upper-cased
    assert cleaned.loc[0, "region"] == "North America"  # title-cased
    assert cleaned.loc[0, "product_name"] == "Wireless Mouse"
    assert cleaned.loc[1, "order_date"] == pd.Timestamp("2024-02-15")  # dd/mm/yyyy normalized
    assert cleaned.loc[2, "order_date"] == pd.Timestamp("2024-03-05")  # "Mar 05, 2024" normalized
    assert cleaned.loc[1, "unit_price_minor"] == 129999  # "$1,299.99" -> integer cents
    assert cleaned.loc[1, "unit_price_raw"] == "$1,299.99"  # original text is kept
    assert "unit_price" not in cleaned.columns  # no decimal money column is left in the pipeline
    assert pd.isna(cleaned.loc[4, "order_date"])  # 2024-02-30 does not exist
    assert cleaned.loc[4, "order_date_raw"] == "2024-02-30"  # original text is kept
    assert pd.isna(cleaned.loc[6, "unit_price_minor"])  # "abc"


def test_missing_attributes_are_inferred_then_defaulted(raw_dir):
    raw, _ = extract_all(raw_dir)
    cleaned = clean_transactions(raw)
    filled, summary = fill_missing_attributes(cleaned)

    # row 2: same customer and product as row 0, so values are inferred
    assert filled.loc[2, "region"] == "North America"
    assert filled.loc[2, "product_name"] == "Wireless Mouse"
    assert filled.loc[2, "category"] == "Electronics"
    assert summary["region"]["inferred_from_known_value"] == 1
    assert summary["region"]["default_applied"] == 0


def test_missing_attributes_fall_back_to_documented_defaults():
    df = pd.DataFrame(
        {
            "customer_id": pd.array(["C1"], dtype="string"),
            "product_id": pd.array(["P1"], dtype="string"),
            "region": pd.array([pd.NA], dtype="string"),
            "product_name": pd.array([pd.NA], dtype="string"),
            "category": pd.array([pd.NA], dtype="string"),
        }
    )
    filled, summary = fill_missing_attributes(df)
    assert filled.loc[0, "region"] == "Unknown"
    assert filled.loc[0, "product_name"] == "Unknown Product"
    assert filled.loc[0, "category"] == "Uncategorized"
    assert summary["category"]["default_applied"] == 1


def test_duplicates_are_separated_not_dropped(raw_dir):
    raw, _ = extract_all(raw_dir)
    cleaned = clean_transactions(raw)
    unique, duplicates = separate_duplicates(cleaned)

    assert len(unique) + len(duplicates) == len(cleaned)
    assert duplicates["transaction_id"].tolist() == ["TXN-000001"]
    assert duplicates["rejection_reason"].eq("DUPLICATE_TRANSACTION_ID").all()
    assert unique["transaction_id"].is_unique
    # the FIRST occurrence is kept
    assert unique.loc[0, "transaction_id"] == "TXN-000001"


def test_duplicates_with_different_formatting_are_detected():
    df = pd.DataFrame({"transaction_id": ["TXN-000001", "TXN-000001", "TXN-000002"]})
    unique, duplicates = separate_duplicates(df)
    assert unique["transaction_id"].tolist() == ["TXN-000001", "TXN-000002"]
    assert len(duplicates) == 1


def test_sales_amount_is_quantity_times_price_in_exact_integer_cents():
    df = pd.DataFrame({"quantity": [3.0, 1.0, 2.0], "unit_price_minor": [1999.0, 129999.0, 10.0]})
    result = add_sales_amount(df)
    assert result["sales_amount_minor"].tolist() == [5997, 129999, 20]
    assert str(result["quantity"].dtype) == "int64"
    assert str(result["unit_price_minor"].dtype) == "int64"
    assert str(result["sales_amount_minor"].dtype) == "int64"


def test_integer_cents_have_no_floating_point_drift():
    # With floats, 3 * 0.10 is 0.30000000000000004 and summing 0.10 a thousand times is not 100.0.
    df = pd.DataFrame({"quantity": [3.0] + [1.0] * 1000, "unit_price_minor": [10.0] * 1001})
    result = add_sales_amount(df)
    assert result.loc[0, "sales_amount_minor"] == 30
    assert result["sales_amount_minor"].sum() == 10030  # 30 + 1000 * 10, exactly
    assert format_minor_units(pd.Series([int(result["sales_amount_minor"].sum())])).iloc[0] == "100.30"
