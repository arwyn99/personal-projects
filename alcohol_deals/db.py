import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

DB_PATH = Path(__file__).parent / "deals.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS deals (
    url TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    sale_price REAL,
    regular_price REAL,
    percent_off REAL,
    image_url TEXT,
    scraped_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_percent_off ON deals(percent_off DESC);
CREATE INDEX IF NOT EXISTS idx_category ON deals(category);
"""


@contextmanager
def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init():
    with connect() as con:
        con.executescript(SCHEMA)


def replace_all(deals: Iterable[dict]) -> int:
    rows = [
        (
            d["url"],
            d["name"],
            d.get("category"),
            d.get("sale_price"),
            d.get("regular_price"),
            d.get("percent_off"),
            d.get("image_url"),
            d["scraped_at"],
        )
        for d in deals
    ]
    with connect() as con:
        con.execute("DELETE FROM deals")
        con.executemany(
            """INSERT INTO deals
               (url, name, category, sale_price, regular_price, percent_off, image_url, scraped_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    return len(rows)


def list_deals(
    category: str | None = None,
    min_percent_off: float | None = None,
    max_price: float | None = None,
    sort: str = "percent_off",
) -> list[dict]:
    allowed_sort = {"percent_off", "sale_price", "name"}
    if sort not in allowed_sort:
        sort = "percent_off"
    direction = "DESC" if sort == "percent_off" else "ASC"

    sql = "SELECT * FROM deals WHERE 1=1"
    params: list = []
    if category:
        sql += " AND category = ?"
        params.append(category)
    if min_percent_off is not None:
        sql += " AND percent_off >= ?"
        params.append(min_percent_off)
    if max_price is not None:
        sql += " AND sale_price <= ?"
        params.append(max_price)
    sql += f" ORDER BY {sort} {direction} NULLS LAST"

    with connect() as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def list_categories() -> list[str]:
    with connect() as con:
        rows = con.execute(
            "SELECT DISTINCT category FROM deals WHERE category IS NOT NULL ORDER BY category"
        ).fetchall()
    return [r["category"] for r in rows]


def last_scraped_at() -> str | None:
    with connect() as con:
        row = con.execute("SELECT MAX(scraped_at) AS t FROM deals").fetchone()
    return row["t"] if row else None
