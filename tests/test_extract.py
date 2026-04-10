import csv
import os
import re
import sqlite3
import subprocess
import sys

import pytest

WORKTREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WINCHESTER_PDF = "budgets/winchester_fy2026_budget.pdf"
CSV_PATH = "output/raw/winchester_fy2026.csv"
NARRATIVE_PATH = "output/raw/winchester_fy2026_narrative.txt"
VALIDATION_PATH = "output/raw/winchester_fy2026_validation.txt"
DB_PATH = "output/budgets.db"


def _run_extract():
    return subprocess.run(
        [
            sys.executable, "src/load.py", "extract",
            "--town", "winchester",
            "--pdf", WINCHESTER_PDF,
        ],
        capture_output=True,
        text=True,
        cwd=WORKTREE,
    )


@pytest.fixture(scope="module")
def extract_once():
    """Run extract once. All AC1–AC5 tests share this single invocation."""
    return _run_extract()


def _read_csv():
    path = os.path.join(WORKTREE, CSV_PATH)
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _parse_amount(val: str) -> float:
    """Strip currency formatting and return float."""
    return float(val.replace(",", "").replace("$", "").strip())


# ---------------------------------------------------------------------------
# AC1: CSV produced with every row having a non-null source_page
# ---------------------------------------------------------------------------

def test_csv_exists(extract_once):
    """AC1: output/raw/winchester_fy2026.csv is created after extract."""
    path = os.path.join(WORKTREE, CSV_PATH)
    assert os.path.isfile(path), f"CSV not found: {CSV_PATH}"


def test_csv_all_rows_have_source_page(extract_once):
    """AC1: Every data row in the CSV has a non-empty source_page value."""
    rows = _read_csv()
    assert rows, "CSV is empty — no line items extracted"
    missing = [i + 2 for i, row in enumerate(rows) if not row.get("source_page", "").strip()]
    assert not missing, f"Rows missing source_page at CSV line numbers: {missing}"


# ---------------------------------------------------------------------------
# AC2: Fire Dept PERMANENT ~$5.7M and OVERTIME ~$648K
# ---------------------------------------------------------------------------

def _get_fire_rows():
    return [
        row for row in _read_csv()
        if "fire" in (row.get("department") or "").lower()
    ]


def test_fire_permanent_amount(extract_once):
    """AC2: Fire Dept PERMANENT line amount is within 10% of $5,700,000."""
    fire_rows = _get_fire_rows()
    assert fire_rows, "No rows found for Fire Department in CSV"
    permanent = [
        r for r in fire_rows
        if "permanent" in (r.get("description") or r.get("account_code") or "").lower()
    ]
    assert permanent, "No PERMANENT row found for Fire Department"
    target = 5_700_000
    amounts = [_parse_amount(r["amount"]) for r in permanent if r.get("amount", "").strip()]
    assert any(abs(amt - target) / target <= 0.10 for amt in amounts), (
        f"No PERMANENT amount within 10% of ${target:,}. Found: {amounts}"
    )


def test_fire_overtime_amount(extract_once):
    """AC2: Fire Dept OVERTIME line amount is within 10% of $648,000."""
    fire_rows = _get_fire_rows()
    assert fire_rows, "No rows found for Fire Department in CSV"
    overtime = [
        r for r in fire_rows
        if "overtime" in (r.get("description") or r.get("account_code") or "").lower()
    ]
    assert overtime, "No OVERTIME row found for Fire Department"
    target = 648_000
    amounts = [_parse_amount(r["amount"]) for r in overtime if r.get("amount", "").strip()]
    assert any(abs(amt - target) / target <= 0.10 for amt in amounts), (
        f"No OVERTIME amount within 10% of ${target:,}. Found: {amounts}"
    )


# ---------------------------------------------------------------------------
# AC3: Narrative file exists with non-empty department description text
# ---------------------------------------------------------------------------

def test_narrative_file_exists(extract_once):
    """AC3: output/raw/winchester_fy2026_narrative.txt is created after extract."""
    path = os.path.join(WORKTREE, NARRATIVE_PATH)
    assert os.path.isfile(path), f"Narrative file not found: {NARRATIVE_PATH}"


def test_narrative_has_content(extract_once):
    """AC3: Narrative file contains non-empty department description text."""
    path = os.path.join(WORKTREE, NARRATIVE_PATH)
    content = open(path).read().strip()
    assert content, "Narrative file is empty"
    assert len(content) > 50, (
        f"Narrative file has suspiciously little content ({len(content)} chars)"
    )


# ---------------------------------------------------------------------------
# AC4: Validation file exists; flagged lines match "Page N:" format
# ---------------------------------------------------------------------------

def test_validation_file_exists(extract_once):
    """AC4: output/raw/winchester_fy2026_validation.txt is created after extract."""
    path = os.path.join(WORKTREE, VALIDATION_PATH)
    assert os.path.isfile(path), f"Validation file not found: {VALIDATION_PATH}"


def test_validation_format(extract_once):
    """AC4: Each non-empty line in validation report matches 'Page N: ...' format."""
    path = os.path.join(WORKTREE, VALIDATION_PATH)
    lines = [ln.strip() for ln in open(path).read().splitlines() if ln.strip()]
    for line in lines:
        assert re.match(r"^Page \d+:", line), (
            f"Validation line does not match 'Page N: ...' format: {line!r}"
        )


# ---------------------------------------------------------------------------
# AC5: DB has non-zero line_items count for winchester
# ---------------------------------------------------------------------------

def test_db_line_items_nonzero(extract_once):
    """AC5: DB contains at least one line_item row for winchester after extract."""
    db_path = os.path.join(WORKTREE, DB_PATH)
    assert os.path.isfile(db_path), f"Database not found: {DB_PATH}"
    con = sqlite3.connect(db_path)
    count = con.execute(
        "SELECT COUNT(*) FROM line_items "
        "WHERE town_id = (SELECT id FROM towns WHERE name='winchester')"
    ).fetchone()[0]
    con.close()
    assert count > 0, "No line_items found for winchester in DB"


# ---------------------------------------------------------------------------
# AC6: Second extract run does not increase line_items count
# ---------------------------------------------------------------------------

def test_extract_idempotent_row_count(extract_once):
    """AC6: Running extract twice keeps line_items count unchanged (UNIQUE constraint)."""
    db_path = os.path.join(WORKTREE, DB_PATH)
    con = sqlite3.connect(db_path)
    count_before = con.execute(
        "SELECT COUNT(*) FROM line_items "
        "WHERE town_id = (SELECT id FROM towns WHERE name='winchester')"
    ).fetchone()[0]
    con.close()

    _run_extract()  # second extraction

    con = sqlite3.connect(db_path)
    count_after = con.execute(
        "SELECT COUNT(*) FROM line_items "
        "WHERE town_id = (SELECT id FROM towns WHERE name='winchester')"
    ).fetchone()[0]
    con.close()

    assert count_after == count_before, (
        f"Row count changed after second extract: {count_before} → {count_after}. "
        "UNIQUE constraint may not be enforced."
    )
