import pytest
import sqlite3
import os


# ---------------------------------------------------------------------------
# AC1: All required packages are importable after pip install -r requirements.txt
# ---------------------------------------------------------------------------

def test_pdfplumber_importable():
    import pdfplumber  # noqa: F401


def test_openpyxl_importable():
    import openpyxl  # noqa: F401


def test_pyyaml_importable():
    import yaml  # noqa: F401


# ---------------------------------------------------------------------------
# AC2: init_db() creates output/budgets.db with all 5 tables
# ---------------------------------------------------------------------------

EXPECTED_TABLES = frozenset(
    {"towns", "town_metrics", "line_items", "narratives", "normalization_cache"}
)


def test_init_db_creates_database(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from src.schema import init_db

    init_db()

    db_path = tmp_path / "output" / "budgets.db"
    assert db_path.exists(), "output/budgets.db was not created"

    con = sqlite3.connect(str(db_path))
    tables = {
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    con.close()

    missing = EXPECTED_TABLES - tables
    assert not missing, f"Missing tables: {missing}"


# ---------------------------------------------------------------------------
# AC3: init_db() is idempotent — calling twice must not raise
# ---------------------------------------------------------------------------

def test_init_db_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from src.schema import init_db

    init_db()
    init_db()  # second call must not raise "table already exists"


# ---------------------------------------------------------------------------
# AC4: ensure_town returns int id, same id on repeat, no duplicate rows
# ---------------------------------------------------------------------------

def test_ensure_town_returns_int(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from src.schema import init_db, ensure_town

    init_db()
    town_id = ensure_town("winchester")
    assert isinstance(town_id, int), f"Expected int, got {type(town_id)}"


def test_ensure_town_idempotent_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from src.schema import init_db, ensure_town

    init_db()
    id1 = ensure_town("winchester")
    id2 = ensure_town("winchester")
    assert id1 == id2, f"ensure_town returned different ids: {id1} vs {id2}"


def test_ensure_town_no_duplicate_rows(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from src.schema import init_db, ensure_town

    init_db()
    ensure_town("winchester")
    ensure_town("winchester")

    db_path = tmp_path / "output" / "budgets.db"
    con = sqlite3.connect(str(db_path))
    count = con.execute(
        "SELECT COUNT(*) FROM towns WHERE name='winchester'"
    ).fetchone()[0]
    con.close()

    assert count == 1, f"Expected 1 row in towns, got {count}"


# ---------------------------------------------------------------------------
# AC5: .gitignore lists output/ and *.db
# ---------------------------------------------------------------------------

def test_gitignore_output_dir():
    with open(".gitignore") as f:
        content = f.read()
    assert "output/" in content, ".gitignore must contain 'output/'"


def test_gitignore_db_pattern():
    with open(".gitignore") as f:
        content = f.read()
    assert "*.db" in content, ".gitignore must contain '*.db'"


# ---------------------------------------------------------------------------
# AC6: budgets/CLAUDE.md exists and documents schema + example query
# ---------------------------------------------------------------------------

def test_budgets_claude_md_exists():
    assert os.path.isfile("budgets/CLAUDE.md"), "budgets/CLAUDE.md does not exist"


def test_budgets_claude_md_documents_all_tables():
    with open("budgets/CLAUDE.md") as f:
        content = f.read()
    for table in ["towns", "town_metrics", "line_items", "narratives", "normalization_cache"]:
        assert table in content, f"budgets/CLAUDE.md is missing documentation for table: {table}"


def test_budgets_claude_md_has_example_query():
    with open("budgets/CLAUDE.md") as f:
        content = f.read()
    assert "SELECT" in content, "budgets/CLAUDE.md must contain at least one example SQL query"
