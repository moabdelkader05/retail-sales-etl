"""Run the SQL queries stored in sql/analytics_queries.sql."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pandas as pd

from src.config import ANALYTICS_SQL_PATH

_NAME_MARKER = re.compile(r"^-- name:\s*(.+)$", flags=re.MULTILINE)


def load_queries(sql_path: Path = ANALYTICS_SQL_PATH) -> list[tuple[str, str]]:
    """Split the SQL file into (query name, SQL text) pairs."""
    parts = _NAME_MARKER.split(Path(sql_path).read_text(encoding="utf-8"))
    # parts = [text before first marker, name1, sql1, name2, sql2, ...]
    return [(parts[i].strip(), parts[i + 1].strip()) for i in range(1, len(parts), 2)]


def run_analytics(conn: sqlite3.Connection, sql_path: Path = ANALYTICS_SQL_PATH) -> dict[str, pd.DataFrame]:
    """Execute every analytics query and return the results by query name."""
    return {name: pd.read_sql_query(sql, conn) for name, sql in load_queries(sql_path)}
