"""Data quality validation: decide which records are valid and why others are rejected."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from src.config import (
    MAX_QUANTITY,
    MAX_UNIT_PRICE_MINOR,
    MIN_ORDER_DATE,
    MIN_QUANTITY,
    MIN_UNIT_PRICE_MINOR,
    TRANSACTION_ID_PATTERN,
)

# Rejection reason codes
MISSING_TRANSACTION_ID = "MISSING_TRANSACTION_ID"
INVALID_TRANSACTION_ID = "INVALID_TRANSACTION_ID"
MISSING_ORDER_DATE = "MISSING_ORDER_DATE"
INVALID_ORDER_DATE = "INVALID_ORDER_DATE"
ORDER_DATE_OUT_OF_RANGE = "ORDER_DATE_OUT_OF_RANGE"
MISSING_CUSTOMER_ID = "MISSING_CUSTOMER_ID"
MISSING_PRODUCT_ID = "MISSING_PRODUCT_ID"
MISSING_QUANTITY = "MISSING_QUANTITY"
INVALID_QUANTITY = "INVALID_QUANTITY"
MISSING_UNIT_PRICE = "MISSING_UNIT_PRICE"
INVALID_UNIT_PRICE = "INVALID_UNIT_PRICE"

NUMERIC_REASONS = (INVALID_QUANTITY, INVALID_UNIT_PRICE)


def _as_bool(series: pd.Series) -> pd.Series:
    return series.fillna(False).astype(bool)


def evaluate_rules(df: pd.DataFrame, today: date | None = None) -> pd.DataFrame:
    """Return one boolean column per rejection reason (True = the rule is violated)."""
    today = today or date.today()
    order_date = df["order_date"]

    id_present = df["transaction_id"].notna()
    id_format_ok = _as_bool(df["transaction_id"].str.fullmatch(TRANSACTION_ID_PATTERN))

    date_present = df["order_date_raw"].notna()
    date_parsed = order_date.notna()
    date_in_range = (order_date >= pd.Timestamp(MIN_ORDER_DATE)) & (order_date <= pd.Timestamp(today))

    quantity = df["quantity"]
    quantity_ok = quantity.notna() & (np.mod(quantity, 1) == 0) & quantity.between(MIN_QUANTITY, MAX_QUANTITY)
    price_minor = df["unit_price_minor"]  # NaN when the text is not a valid price (see parse_price_minor)
    price_ok = price_minor.notna() & price_minor.between(MIN_UNIT_PRICE_MINOR, MAX_UNIT_PRICE_MINOR)

    flags = {
        MISSING_TRANSACTION_ID: ~id_present,
        INVALID_TRANSACTION_ID: id_present & ~id_format_ok,
        MISSING_ORDER_DATE: ~date_present,
        INVALID_ORDER_DATE: date_present & ~date_parsed,
        ORDER_DATE_OUT_OF_RANGE: date_parsed & ~date_in_range,
        MISSING_CUSTOMER_ID: df["customer_id"].isna(),
        MISSING_PRODUCT_ID: df["product_id"].isna(),
        MISSING_QUANTITY: df["quantity_raw"].isna(),
        INVALID_QUANTITY: df["quantity_raw"].notna() & ~quantity_ok,
        MISSING_UNIT_PRICE: df["unit_price_raw"].isna(),
        INVALID_UNIT_PRICE: df["unit_price_raw"].notna() & ~price_ok,
    }
    return pd.DataFrame({reason: mask.astype(bool) for reason, mask in flags.items()}, index=df.index)


def split_valid_invalid(df: pd.DataFrame, today: date | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separate valid records from invalid ones.

    Invalid records get a ``rejection_reason`` column listing every violated rule
    (separated by ``;``). Together the two outputs always contain every input row.
    """
    flags = evaluate_rules(df, today)
    is_invalid = flags.any(axis=1)

    reason_names = np.array(flags.columns)
    reasons = [";".join(reason_names[row]) for row in flags.to_numpy()]
    reason_series = pd.Series(reasons, index=df.index, dtype="object")

    valid = df.loc[~is_invalid].copy()
    invalid = df.loc[is_invalid].copy()
    invalid["rejection_reason"] = reason_series.loc[is_invalid]
    return valid, invalid


def count_reasons(rejection_reasons: pd.Series) -> dict[str, int]:
    """Count how many records were rejected for each reason code."""
    if rejection_reasons.empty:
        return {}
    counts = rejection_reasons.str.split(";").explode().value_counts()
    return {str(reason): int(count) for reason, count in counts.items()}
