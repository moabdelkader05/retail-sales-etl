"""Generate realistic synthetic retail CSV files with intentional data quality problems.

The generator is deterministic (fixed random seed) so results are reproducible.
It only creates new files; it never modifies existing raw files.
"""

from __future__ import annotations

import csv
import random
import re
from datetime import date, timedelta
from pathlib import Path

from src.config import SOURCE_COLUMNS

PRODUCTS = [
    ("PRD-101", "Wireless Mouse", "Electronics", 24.99),
    ("PRD-102", "Mechanical Keyboard", "Electronics", 79.99),
    ("PRD-103", "Noise Cancelling Headphones", "Electronics", 149.99),
    ("PRD-104", "27 Inch Monitor", "Electronics", 229.99),
    ("PRD-105", "Laptop Pro 15", "Electronics", 1299.99),
    ("PRD-201", "Non Stick Frying Pan", "Home & Kitchen", 34.50),
    ("PRD-202", "Electric Kettle", "Home & Kitchen", 39.90),
    ("PRD-203", "Air Fryer", "Home & Kitchen", 119.00),
    ("PRD-204", "Blender", "Home & Kitchen", 64.95),
    ("PRD-301", "Cotton T-Shirt", "Clothing", 15.99),
    ("PRD-302", "Denim Jeans", "Clothing", 49.90),
    ("PRD-303", "Running Shoes", "Clothing", 89.00),
    ("PRD-304", "Winter Jacket", "Clothing", 129.50),
    ("PRD-401", "Yoga Mat", "Sports & Outdoors", 22.00),
    ("PRD-402", "Camping Tent", "Sports & Outdoors", 159.99),
    ("PRD-403", "Dumbbell Set", "Sports & Outdoors", 74.25),
    ("PRD-501", "A4 Printer Paper", "Office Supplies", 6.99),
    ("PRD-502", "Ballpoint Pens Box", "Office Supplies", 4.50),
    ("PRD-503", "Ergonomic Office Chair", "Office Supplies", 189.00),
    ("PRD-601", "Face Moisturizer", "Beauty", 18.75),
    ("PRD-602", "Signature Perfume", "Beauty", 59.90),
]
REGIONS = ["North America", "Europe", "Middle East", "Asia Pacific", "Africa"]
MONTH_ABBREVIATIONS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Base number of intentional problems for a file of about 1,000 records.
ISSUE_COUNTS = {
    "blank_region": 15,
    "blank_category": 10,
    "blank_product_name": 10,
    "blank_customer_id": 8,
    "blank_order_date": 6,
    "blank_quantity": 6,
    "blank_unit_price": 6,
    "blank_transaction_id": 5,
    "invalid_quantity": 15,
    "invalid_unit_price": 12,
    "unparseable_date": 8,
    "future_date": 4,
    "ancient_date": 2,
    "invalid_transaction_id": 6,
    "duplicates": 25,
}

_CLEAN_ID = re.compile(r"TXN-\d{6}")


def _make_customers(rng: random.Random, count: int = 150) -> list[tuple[str, str]]:
    return [(f"CUST-{1001 + i}", rng.choice(REGIONS)) for i in range(count)]


def _format_date(day: date, style: str) -> str:
    if style == "dmy":
        return f"{day:%d/%m/%Y}"
    if style == "text":
        return f"{MONTH_ABBREVIATIONS[day.month - 1]} {day.day:02d}, {day.year}"
    return day.isoformat()


def _format_price(rng: random.Random, price: float) -> str:
    if rng.random() < 0.04:
        return f"${price:,.2f}"  # currency symbol and thousands separator
    return f"{price:.2f}"


def _case_noise(rng: random.Random, value: str) -> str:
    roll = rng.random()
    if roll < 0.04:
        return value.upper()
    if roll < 0.08:
        return value.lower()
    if roll < 0.11:
        return f"  {value} "
    return value


def build_base_rows(
    rng: random.Random, count: int, start_id: int, first_day: date, last_day: date, sources: dict[str, str]
) -> list[dict]:
    """Create plausible transactions. ``sources`` maps a source name to its date style."""
    customers = _make_customers(random.Random(7))  # same customer base for every file
    span = (last_day - first_day).days
    weights = [10, 8, 5, 3, 2, 6, 6, 3, 4, 9, 6, 4, 2, 5, 2, 3, 8, 8, 2, 6, 4]
    rows = []
    for offset in range(count):
        product_id, name, category, base_price = rng.choices(PRODUCTS, weights=weights)[0]
        customer_id, region = rng.choice(customers)
        source = rng.choice(list(sources))
        style = sources[source]
        if style == "dmy" and rng.random() < 0.1:
            style = "text"
        price = round(base_price * rng.choice([1.0, 1.0, 1.0, 0.9, 0.85]), 2)
        rows.append(
            {
                "transaction_id": f"TXN-{start_id + offset:06d}",
                "order_date": _format_date(first_day + timedelta(days=rng.randint(0, span)), style),
                "customer_id": customer_id.lower() if rng.random() < 0.03 else customer_id,
                "region": _case_noise(rng, region),
                "product_id": product_id,
                "product_name": _case_noise(rng, name),
                "category": _case_noise(rng, category),
                "quantity": str(rng.choices([1, 2, 3, 4, 5, 6, 8], weights=[40, 25, 15, 8, 6, 3, 3])[0]),
                "unit_price": _format_price(rng, price),
                "_source": source,
            }
        )
    return rows


def inject_quality_issues(rng: random.Random, rows: list[dict], scale: float) -> list[dict]:
    """Corrupt a random subset of rows and append duplicate rows. Returns the new row list."""
    counts = {key: max(1, round(value * scale)) for key, value in ISSUE_COUNTS.items()}
    pool = list(range(len(rows)))
    rng.shuffle(pool)

    def take(key: str) -> list[int]:
        return [pool.pop() for _ in range(min(counts[key], len(pool)))]

    for key, column in [
        ("blank_region", "region"),
        ("blank_category", "category"),
        ("blank_product_name", "product_name"),
        ("blank_customer_id", "customer_id"),
        ("blank_order_date", "order_date"),
        ("blank_quantity", "quantity"),
        ("blank_unit_price", "unit_price"),
        ("blank_transaction_id", "transaction_id"),
    ]:
        for index in take(key):
            rows[index][column] = rng.choice(["", "", "N/A"]) if column != "transaction_id" else ""

    for index in take("invalid_quantity"):
        rows[index]["quantity"] = rng.choice(["-1", "0", "abc", "2.5", "9999"])
    for index in take("invalid_unit_price"):
        rows[index]["unit_price"] = rng.choice(["-19.99", "0", "free", "$", "999999"])
    for index in take("unparseable_date"):
        rows[index]["order_date"] = rng.choice(["31/02/2024", "2024-13-45", "yesterday", "00/00/0000"])
    for index in take("future_date"):
        rows[index]["order_date"] = rng.choice(["2099-01-01", "01/01/2031"])
    for index in take("ancient_date"):
        rows[index]["order_date"] = "1999-12-31"
    for index in take("invalid_transaction_id"):
        rows[index]["transaction_id"] = rng.choice(["12345", "TXN-ABC", "TXN-12", "INV-000045"])

    # Duplicates are copies of otherwise healthy rows, sometimes with messy ID formatting.
    result = list(rows)
    for index in take("duplicates"):
        copy = dict(rows[index])
        if rng.random() < 0.3:
            copy["transaction_id"] = copy["transaction_id"].lower() + " "
        result.insert(rng.randint(0, len(result)), copy)
    return result


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_sample_dataset(raw_dir: Path, seed: int = 42, transactions: int = 975) -> list[Path]:
    """Create the initial raw dataset (about 1,000 rows including duplicates) as two CSV files."""
    rng = random.Random(seed)
    sources = {"online_store": "iso", "retail_stores": "dmy"}
    rows = build_base_rows(rng, transactions, 1, date(2023, 1, 1), date(2024, 12, 31), sources)
    rows = inject_quality_issues(rng, rows, scale=transactions / 975)

    paths = []
    for source, file_name in [("online_store", "sales_online_store.csv"), ("retail_stores", "sales_retail_stores.csv")]:
        path = raw_dir / file_name
        _write_csv(path, [row for row in rows if row["_source"] == source])
        paths.append(path)
    return paths


def _is_healthy(row: dict) -> bool:
    """True for rows that look clean enough to be re-sent as 'already loaded' records."""
    return (
        bool(_CLEAN_ID.fullmatch(row["transaction_id"]))
        and all(row[column].strip() for column in SOURCE_COLUMNS)
        and row["quantity"].strip().isdigit()
        and re.fullmatch(r"\d+\.\d{2}", row["unit_price"].strip()) is not None
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}", row["order_date"].strip()) is not None
    )


def write_incremental_batch(
    raw_dir: Path, new_transactions: int = 100, repeated_transactions: int = 20, seed: int = 43
) -> Path:
    """Create ``sales_batch_2.csv``: new 2025 transactions plus re-sent older ones.

    The re-sent rows are copies of healthy rows from the existing files. They let you
    verify that the pipeline does not load the same TransactionID twice.
    """
    rng = random.Random(seed)
    batch_path = raw_dir / "sales_batch_2.csv"

    new_rows = build_base_rows(
        rng, new_transactions, 1001, date(2025, 1, 1), date(2025, 3, 31), {"online_store": "iso"}
    )
    new_rows = inject_quality_issues(rng, new_rows, scale=new_transactions / 1000)

    healthy_existing: list[dict] = []
    for path in sorted(raw_dir.glob("*.csv")):
        if path == batch_path:
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            healthy_existing.extend(row for row in csv.DictReader(handle) if _is_healthy(row))
    repeated = rng.sample(healthy_existing, k=min(repeated_transactions, len(healthy_existing)))

    rows = new_rows + repeated
    rng.shuffle(rows)
    _write_csv(batch_path, rows)
    return batch_path
