from datetime import date

import pandas as pd
import pytest

from src.extract import extract_all
from src.transform import clean_transactions, fill_missing_attributes
from src.validate import count_reasons, evaluate_rules, split_valid_invalid
from tests.conftest import EXPECTED_TOTAL

TODAY = date(2026, 10, 31)


@pytest.fixture
def cleaned(raw_dir):
    raw, _ = extract_all(raw_dir)
    filled, _ = fill_missing_attributes(clean_transactions(raw))
    return filled


def test_every_input_row_ends_up_valid_or_invalid(cleaned):
    valid, invalid = split_valid_invalid(cleaned, TODAY)
    assert len(valid) + len(invalid) == len(cleaned) == EXPECTED_TOTAL
    assert set(valid.index).isdisjoint(invalid.index)


def test_each_invalid_record_gets_the_expected_reason(cleaned):
    _, invalid = split_valid_invalid(cleaned, TODAY)
    reasons = invalid["rejection_reason"].to_dict()
    assert reasons == {
        4: "INVALID_ORDER_DATE",
        5: "INVALID_QUANTITY",
        6: "INVALID_UNIT_PRICE",
        7: "MISSING_TRANSACTION_ID",
        8: "INVALID_TRANSACTION_ID",
        9: "ORDER_DATE_OUT_OF_RANGE",
        10: "MISSING_CUSTOMER_ID",
        11: "INVALID_QUANTITY",
    }


def test_valid_records_pass_all_rules(cleaned):
    valid, _ = split_valid_invalid(cleaned, TODAY)
    assert valid["transaction_id"].tolist() == ["TXN-000001", "TXN-000002", "TXN-000003", "TXN-000001"]
    assert not evaluate_rules(valid, TODAY).to_numpy().any()


def test_a_record_can_have_several_rejection_reasons():
    df = pd.DataFrame(
        {
            "transaction_id": pd.array(["BAD"], dtype="string"),
            "order_date": pd.to_datetime(["2024-01-01"]),
            "order_date_raw": pd.array(["2024-01-01"], dtype="string"),
            "customer_id": pd.array(["C1"], dtype="string"),
            "product_id": pd.array(["P1"], dtype="string"),
            "quantity": [0.0],
            "quantity_raw": pd.array(["0"], dtype="string"),
            "unit_price_minor": [float("nan")],  # "-5" is not a valid price
            "unit_price_raw": pd.array(["-5"], dtype="string"),
        }
    )
    _, invalid = split_valid_invalid(df, TODAY)
    reasons = invalid["rejection_reason"].iloc[0].split(";")
    assert set(reasons) == {"INVALID_TRANSACTION_ID", "INVALID_QUANTITY", "INVALID_UNIT_PRICE"}


@pytest.mark.parametrize(
    ("quantity", "price_minor", "expected"),
    [
        (1, 1, True),  # 0.01 is the minimum price
        (100, 1_000_000, True),  # 10,000.00 is the maximum price, 100 the maximum quantity
        (101, 500, False),  # above MAX_QUANTITY
        (1.5, 500, False),  # fractional quantity
        (2, 1_000_001, False),  # above the maximum price
        (2, 0, False),  # zero price
    ],
)
def test_numeric_limits(quantity, price_minor, expected):
    df = pd.DataFrame(
        {
            "transaction_id": pd.array(["TXN-000001"], dtype="string"),
            "order_date": pd.to_datetime(["2024-01-01"]),
            "order_date_raw": pd.array(["2024-01-01"], dtype="string"),
            "customer_id": pd.array(["C1"], dtype="string"),
            "product_id": pd.array(["P1"], dtype="string"),
            "quantity": [float(quantity)],
            "quantity_raw": pd.array([str(quantity)], dtype="string"),
            "unit_price_minor": [float(price_minor)],
            "unit_price_raw": pd.array([str(price_minor / 100)], dtype="string"),
        }
    )
    valid, _ = split_valid_invalid(df, TODAY)
    assert (len(valid) == 1) is expected


def _single_row(unit_price_text: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "transaction_id": ["TXN-000001"], "order_date": ["2024-01-01"], "customer_id": ["C1"],
            "region": ["Europe"], "product_id": ["P1"], "product_name": ["Mouse"],
            "category": ["Electronics"], "quantity": ["1"], "unit_price": [unit_price_text],
            "source_file": ["x.csv"], "source_row": [2],
        }
    )


@pytest.mark.parametrize("price_text", ["19.999", "-5", "0", "0.00", "free", "$", "1e3", "10000.01", "999999"])
def test_invalid_prices_are_rejected_with_the_price_reason(price_text):
    from src.transform import clean_transactions

    valid, invalid = split_valid_invalid(clean_transactions(_single_row(price_text)), TODAY)
    assert valid.empty
    assert invalid["rejection_reason"].iloc[0] == "INVALID_UNIT_PRICE"


@pytest.mark.parametrize("price_text", ["0.01", "19.99", "$1,299.99", "10,000.00", "25.5"])
def test_valid_price_formats_are_accepted(price_text):
    from src.transform import clean_transactions

    valid, invalid = split_valid_invalid(clean_transactions(_single_row(price_text)), TODAY)
    assert invalid.empty
    assert len(valid) == 1


@pytest.mark.parametrize("absurd_date", ["2999-01-01", "9999-12-31"])
def test_absurd_far_future_dates_are_rejected_instead_of_crashing(absurd_date):
    # Regression test: such dates used to overflow pandas' nanosecond timestamps.
    from src.transform import clean_transactions

    raw = pd.DataFrame(
        {
            "transaction_id": ["TXN-000001"], "order_date": [absurd_date], "customer_id": ["C1"],
            "region": ["Europe"], "product_id": ["P1"], "product_name": ["Mouse"],
            "category": ["Electronics"], "quantity": ["1"], "unit_price": ["10.00"],
            "source_file": ["x.csv"], "source_row": [2],
        }
    )
    valid, invalid = split_valid_invalid(clean_transactions(raw), TODAY)
    assert valid.empty
    assert invalid["rejection_reason"].iloc[0] in {"ORDER_DATE_OUT_OF_RANGE", "INVALID_ORDER_DATE"}


def test_count_reasons_counts_each_code_once_per_record():
    series = pd.Series(["A;B", "A", "C"])
    assert count_reasons(series) == {"A": 2, "B": 1, "C": 1}
    assert count_reasons(pd.Series([], dtype="object")) == {}
