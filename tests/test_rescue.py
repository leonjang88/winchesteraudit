import os
import sqlite3
import subprocess
import sys

import pytest

WORKTREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WINCHESTER_PDF = "budgets/winchester_fy2026_budget.pdf"
VALIDATION_PATH = os.path.join(WORKTREE, "output", "raw", "winchester_fy2026_validation.txt")
DB_PATH = os.path.join(WORKTREE, "output", "budgets.db")

# Valid JSON matching the line_items schema from the contract
GOOD_JSON = (
    '[{"department":"Rescued Dept","account_code":"999",'
    '"description":"Rescued item","amount":"5000",'
    '"fiscal_year":"FY26","column_type":"BUDGET","row_type":"line_item"}]'
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_rescue(env=None):
    return subprocess.run(
        [
            sys.executable, "src/load.py", "rescue",
            "--town", "winchester",
            "--pdf", WINCHESTER_PDF,
        ],
        capture_output=True,
        text=True,
        cwd=WORKTREE,
        env=env,
        timeout=300,
    )


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
        timeout=300,
    )


def _write_validation(content):
    os.makedirs(os.path.dirname(VALIDATION_PATH), exist_ok=True)
    with open(VALIDATION_PATH, "w") as f:
        f.write(content)


def _make_fake_claude(tmp_path, script_body):
    """Create a fake 'claude' CLI script and return an env dict with it first in PATH."""
    script = tmp_path / "claude"
    script.write_text(script_body)
    script.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(tmp_path) + ":" + env.get("PATH", "")
    return env


def _winchester_line_items_count(source_page=None):
    con = sqlite3.connect(DB_PATH)
    if source_page is not None:
        count = con.execute(
            "SELECT COUNT(*) FROM line_items "
            "WHERE source_page = ? "
            "AND town_id = (SELECT id FROM towns WHERE name='winchester')",
            (source_page,),
        ).fetchone()[0]
    else:
        count = con.execute(
            "SELECT COUNT(*) FROM line_items "
            "WHERE town_id = (SELECT id FROM towns WHERE name='winchester')",
        ).fetchone()[0]
    con.close()
    return count


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def db_ready():
    """Run extract once to set up DB, validation report, and output files."""
    result = _run_extract()
    assert result.returncode == 0, (
        f"Extract failed — rescue tests cannot proceed.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


@pytest.fixture(autouse=False)
def validation_backup():
    """Save and restore the validation report around tests that modify it."""
    original = None
    if os.path.exists(VALIDATION_PATH):
        with open(VALIDATION_PATH) as f:
            original = f.read()
    yield
    # Restore
    if original is not None:
        with open(VALIDATION_PATH, "w") as f:
            f.write(original)
    elif os.path.exists(VALIDATION_PATH):
        os.remove(VALIDATION_PATH)


@pytest.fixture
def good_claude(tmp_path):
    """Fake claude CLI that returns valid JSON line-item data."""
    return _make_fake_claude(tmp_path, f'#!/bin/bash\necho \'{GOOD_JSON}\'\n')


@pytest.fixture
def bad_claude(tmp_path):
    """Fake claude CLI that exits non-zero."""
    return _make_fake_claude(tmp_path, '#!/bin/bash\necho "Subagent error" >&2\nexit 1\n')


@pytest.fixture
def garbage_claude(tmp_path):
    """Fake claude CLI that returns unparseable output."""
    return _make_fake_claude(
        tmp_path, '#!/bin/bash\necho "This is definitely not JSON"\n'
    )


# ---------------------------------------------------------------------------
# AC1: Rescue prints each page number as it processes
# ---------------------------------------------------------------------------

def test_rescue_prints_page_numbers(db_ready, validation_backup, good_claude):
    """AC1: Stdout mentions each page number from the validation report."""
    _write_validation(
        "Page 5: expected 6 columns, found 3\n"
        "Page 10: expected 6 columns, found 2\n"
    )
    result = _run_rescue(env=good_claude)
    stdout = result.stdout
    assert "5" in stdout, f"Output should mention page 5.\nstdout: {stdout}"
    assert "10" in stdout, f"Output should mention page 10.\nstdout: {stdout}"
    assert result.returncode == 0, (
        f"Rescue should exit 0 on success.\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# AC2: Rescued pages have rows in line_items after completion
# ---------------------------------------------------------------------------

def test_rescued_pages_have_db_rows(db_ready, validation_backup, good_claude):
    """AC2: After rescue, previously-failed pages have at least one line_items row."""
    _write_validation("Page 97: expected 6 columns, found 3\n")
    _run_rescue(env=good_claude)
    count = _winchester_line_items_count(source_page=97)
    assert count > 0, "No line_items rows found for rescued page 97"


# ---------------------------------------------------------------------------
# AC3: No failed pages → "No pages to rescue" + exit 0
# ---------------------------------------------------------------------------

def test_no_pages_to_rescue(db_ready, validation_backup):
    """AC3: Clean validation report produces 'No pages to rescue' and exit 0."""
    _write_validation("No validation issues detected.\n")
    result = _run_rescue()
    assert result.returncode == 0, (
        f"Rescue should exit 0 when no pages need rescue.\nstderr: {result.stderr}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "no pages to rescue" in combined, (
        f"Expected 'No pages to rescue' message.\nstdout: {result.stdout}"
    )


# ---------------------------------------------------------------------------
# AC4: Bad subagent output handled gracefully — no crash
# ---------------------------------------------------------------------------

def test_rescue_handles_nonzero_exit(db_ready, validation_backup, bad_claude):
    """AC4: Subagent exiting non-zero is logged and skipped; rescue doesn't crash."""
    _write_validation(
        "Page 42: expected 6 columns, found 3\n"
        "Page 43: expected 6 columns, found 2\n"
    )
    result = _run_rescue(env=bad_claude)
    assert result.returncode == 0, (
        f"Rescue must exit 0 even when all subagents fail.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_rescue_handles_unparseable_output(db_ready, validation_backup, garbage_claude):
    """AC4: Subagent returning non-JSON is logged and skipped; rescue doesn't crash."""
    _write_validation("Page 44: expected 6 columns, found 3\n")
    result = _run_rescue(env=garbage_claude)
    assert result.returncode == 0, (
        f"Rescue must exit 0 even with unparseable subagent output.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# AC5: Second rescue run does not increase row count (no duplicates)
# ---------------------------------------------------------------------------

def test_rescue_no_duplicate_inserts(db_ready, validation_backup, good_claude):
    """AC5: Running rescue twice for the same page keeps row count unchanged."""
    _write_validation("Page 88: expected 6 columns, found 3\n")

    _run_rescue(env=good_claude)
    count_before = _winchester_line_items_count(source_page=88)
    assert count_before > 0, "First rescue should have inserted rows for page 88"

    _run_rescue(env=good_claude)
    count_after = _winchester_line_items_count(source_page=88)

    assert count_after == count_before, (
        f"Row count changed after second rescue: {count_before} → {count_after}. "
        "UNIQUE constraint may not be enforced."
    )
