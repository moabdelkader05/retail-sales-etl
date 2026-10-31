"""Transform step: standardize, type-convert, fill gaps and derive columns with Pandas.

Nothing is dropped here. Records that fail business rules are handled by
``src/validate.py``; duplicates are separated by ``separate_duplicates`` below.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.config import (
    DATE_FORMATS,
    DEFAULT_CATEGORY,
    DEFAULT_PRODUCT_NAME,
    DEFAULT_REGION,
    MINOR_UNITS_PER_UNIT,
    MISSING_TOKENS,
    UNIT_PRICE_PATTERN,
)

DUPLICATE_REASON = "DUPLICATE_TRANSACTION_ID"


def standardize_text(series: pd.Series) -> pd.Series:
    """Trim, collapse repeated spaces and turn blank/placeholder values into missing."""
    text = series.astype("string").str.strip().str.replace(r"\s+", " ", regex=True)
    is_placeholder = text.str.upper().isin(MISSING_TOKENS).fillna(False).astype(bool)
    return text.mask(is_placeholder)


def parse_dates(series: pd.Series) -> pd.Series:
    """Parse dates written in several formats into one datetime column (NaT if invalid)."""
    text = series.astype("string")
    pending = text.notna()
    pieces = []
    for date_format in DATE_FORMATS:
        if not pending.any():
            break
        parsed = pd.to_datetime(text[pending], format=date_format, errors="coerce")
        parsed = parsed[parsed.notna()]
        pieces.append(parsed)
        pending = pending & ~text.index.isin(parsed.index)

    if not pieces:
        return pd.Series(pd.NaT, index=text.index, dtype="datetime64[ns]")
    # No forced nanosecond cast: absurd dates (e.g. year 2999) must reach validation
    # and be rejected instead of crashing the pipeline with an overflow error.
    return pd.concat(pieces).reindex(text.index)


def parse_numeric(series: pd.Series) -> pd.Series:
    """Convert text to float. Unparseable values become NaN (they are rejected later)."""
    numbers = pd.to_numeric(series.astype("string"), errors="coerce")
    return pd.Series(numbers.to_numpy(dtype="float64", na_value=np.nan), index=series.index)


def parse_price_minor(series: pd.Series) -> pd.Series:
    """Convert price text to integer minor units (cents), e.g. ``"$1,299.99"`` -> ``129999``.

    Currency symbols, spaces and thousands commas are removed first. Only plain decimals
    with at most 2 decimal places are accepted; anything else (``19.999``, ``-5``, ``free``)
    becomes NaN so that validation rejects it instead of silently rounding the price.
    The result is a float column holding whole numbers (NaN where invalid).
    """
    text = series.astype("string").str.replace(r"[$€£,\s]", "", regex=True)
    has_valid_format = text.str.fullmatch(UNIT_PRICE_PATTERN).fillna(False).astype(bool)
    numbers = pd.to_numeric(text.where(has_valid_format), errors="coerce")
    values = numbers.to_numpy(dtype="float64", na_value=np.nan)
    return pd.Series(np.rint(values * MINOR_UNITS_PER_UNIT), index=series.index)


def format_minor_units(minor: pd.Series) -> pd.Series:
    """Format integer minor units as a 2-decimal string, e.g. ``129999`` -> ``"1299.99"``."""
    whole, fraction = np.divmod(minor.to_numpy(dtype="int64"), MINOR_UNITS_PER_UNIT)
    return pd.Series([f"{w}.{f:02d}" for w, f in zip(whole, fraction)], index=minor.index, dtype="object")


def clean_transactions(raw: pd.DataFrame) -> pd.DataFrame:
    """Standardize text, normalize dates and convert numeric columns.

    The original (trimmed) text of the date, quantity and price columns is kept in
    ``*_raw`` columns so that validation can tell "missing" apart from "invalid".
    """
    df = raw.copy()
    for column in raw.columns:
        if column not in ("source_row",):
            df[column] = standardize_text(df[column])

    for column in ("transaction_id", "customer_id", "product_id"):
        df[column] = df[column].str.upper()
    for column in ("product_name", "category", "region"):
        df[column] = df[column].str.title()

    df["order_date_raw"] = df["order_date"]
    df["quantity_raw"] = df["quantity"]
    df["unit_price_raw"] = df["unit_price"]

    df["order_date"] = parse_dates(df["order_date_raw"])
    df["quantity"] = parse_numeric(df["quantity_raw"])
    df["unit_price_minor"] = parse_price_minor(df["unit_price_raw"])
    return df.drop(columns=["unit_price"])  # the original text lives on in unit_price_raw


def _fill_from_known_values(
    df: pd.DataFrame, key: str, column: str, default: str
) -> tuple[pd.Series, dict[str, int]]:
    """Fill a missing attribute with the most common known value for the same key."""
    known = df.dropna(subset=[key, column])
    if known.empty:
        most_common = pd.Series(dtype="string")
    else:
        most_common = known.groupby(key)[column].agg(lambda values: values.mode().iloc[0])

    was_missing = df[column].isna()
    inferred_values = df[key].map(most_common).astype("string")
    filled = df[column].fillna(inferred_values)
    inferred = int((was_missing & filled.notna()).sum())
    defaulted = int(filled.isna().sum())
    return filled.fillna(default), {"inferred_from_known_value": inferred, "default_applied": defaulted}


def fill_missing_attributes(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, dict[str, int]]]:
    """Apply the documented missing-value rules for descriptive attributes.

    * ``product_name`` and ``category``: taken from other rows with the same ``product_id``,
      otherwise ``Unknown Product`` / ``Uncategorized``.
    * ``region``: taken from other rows with the same ``customer_id``, otherwise ``Unknown``.

    Identifiers, dates, quantities and prices are never guessed; those records are rejected.
    """
    df = df.copy()
    summary: dict[str, dict[str, int]] = {}
    rules = [
        ("product_name", "product_id", DEFAULT_PRODUCT_NAME),
        ("category", "product_id", DEFAULT_CATEGORY),
        ("region", "customer_id", DEFAULT_REGION),
    ]
    for column, key, default in rules:
        df[column], summary[column] = _fill_from_known_values(df, key, column, default)
    return df, summary


def separate_duplicates(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split records into unique transactions and duplicates of an earlier TransactionID.

    The first occurrence (file order, then row order) is kept. Duplicates are returned
    with a ``rejection_reason`` so they are preserved, never silently dropped.
    """
    is_duplicate = df.duplicated(subset=["transaction_id"], keep="first")
    unique = df.loc[~is_duplicate].copy()
    duplicates = df.loc[is_duplicate].copy()
    duplicates["rejection_reason"] = DUPLICATE_REASON
    return unique, duplicates


def add_sales_amount(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate ``sales_amount_minor = quantity * unit_price_minor`` with exact integer math."""
    df = df.copy()
    df["quantity"] = df["quantity"].astype("int64")
    df["unit_price_minor"] = df["unit_price_minor"].astype("int64")
    df["sales_amount_minor"] = df["quantity"] * df["unit_price_minor"]
    return df
