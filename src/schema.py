import sqlite3
import os
from typing import Optional

DB_PATH = os.path.join("output", "budgets.db")

CREATE_TOWNS = """
CREATE TABLE IF NOT EXISTS towns (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    area_sq_miles REAL,
    school_ranking TEXT
);
"""

CREATE_TOWN_METRICS = """
CREATE TABLE IF NOT EXISTS town_metrics (
    id INTEGER PRIMARY KEY,
    town_id INTEGER REFERENCES towns(id),
    fiscal_year INTEGER NOT NULL,
    population INTEGER,
    households INTEGER,
    median_household_income INTEGER,
    student_enrollment INTEGER,
    road_miles REAL,
    total_assessed_value REAL,
    tax_rate REAL,
    total_levy REAL,
    levy_limit REAL,
    new_growth REAL,
    UNIQUE(town_id, fiscal_year)
);
"""

CREATE_LINE_ITEMS = """
CREATE TABLE IF NOT EXISTS line_items (
    id INTEGER PRIMARY KEY,
    town_id INTEGER REFERENCES towns(id),
    fiscal_year INTEGER NOT NULL,
    department TEXT NOT NULL,
    account_code TEXT,
    description TEXT NOT NULL,
    amount REAL NOT NULL,
    column_type TEXT,
    row_type TEXT DEFAULT 'line_item',
    source_file TEXT,
    source_page INTEGER,
    category TEXT,
    subcategory TEXT,
    normalized_description TEXT,
    expense_type TEXT,
    UNIQUE(town_id, fiscal_year, department, account_code, description, column_type)
);
"""

CREATE_NARRATIVES = """
CREATE TABLE IF NOT EXISTS narratives (
    id INTEGER PRIMARY KEY,
    town_id INTEGER REFERENCES towns(id),
    fiscal_year INTEGER NOT NULL,
    department TEXT NOT NULL,
    page_number INTEGER,
    content TEXT NOT NULL,
    source_file TEXT
);
"""

CREATE_NORMALIZATION_CACHE = """
CREATE TABLE IF NOT EXISTS normalization_cache (
    id INTEGER PRIMARY KEY,
    original_text TEXT NOT NULL,
    field TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    UNIQUE(original_text, field)
);
"""


def get_db() -> sqlite3.Connection:
    """Return connection to output/budgets.db. Creates output/ dir if needed."""
    os.makedirs("output", exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    """CREATE TABLE IF NOT EXISTS for all 5 tables. Must be idempotent."""
    os.makedirs("output", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(CREATE_TOWNS)
        conn.execute(CREATE_TOWN_METRICS)
        conn.execute(CREATE_LINE_ITEMS)
        conn.execute(CREATE_NARRATIVES)
        conn.execute(CREATE_NORMALIZATION_CACHE)
        conn.commit()
    finally:
        conn.close()


def ensure_town(name: str, area_sq_miles: Optional[float] = None, school_ranking: Optional[str] = None) -> int:
    """INSERT OR IGNORE into towns, return the town's id."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO towns (name, area_sq_miles, school_ranking) VALUES (?, ?, ?)",
            (name, area_sq_miles, school_ranking),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM towns WHERE name = ?", (name,)).fetchone()
        return row[0]
    finally:
        conn.close()
