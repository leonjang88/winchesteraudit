import subprocess
import sys
import os
import pytest
import yaml

WORKTREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WINCHESTER_PDF = "budgets/winchester_fy2026_budget.pdf"
WINCHESTER_YAML = "config/towns/winchester.yaml"

REQUIRED_COLUMNS = [
    "FY24 BUDGET",
    "FY24 ACTUAL",
    "FY25 BUDGET",
    "FY26 REQUEST",
    "FY26 MANAGER",
    "FY26 FINCOM",
]

REQUIRED_METRICS = [
    "population",
    "households",
    "median_household_income",
    "student_enrollment",
    "road_miles",
    "total_assessed_value",
    "tax_rate",
    "total_levy",
    "levy_limit",
    "new_growth",
]


# ---------------------------------------------------------------------------
# AC1 + AC2: Profile command on real Winchester PDF
# Run once at module scope — pdfplumber on a real PDF is slow
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def profile_result():
    """Run profile command once; shared by all AC1/AC2 tests."""
    return subprocess.run(
        [
            sys.executable, "src/load.py", "profile",
            "--town", "winchester",
            "--pdf", WINCHESTER_PDF,
        ],
        capture_output=True,
        text=True,
        cwd=WORKTREE,
    )


def test_profile_exits_zero(profile_result):
    """AC1: Command exits 0 on a valid PDF."""
    assert profile_result.returncode == 0, (
        f"Profile command failed.\nstdout: {profile_result.stdout}\nstderr: {profile_result.stderr}"
    )


def test_profile_prints_page_numbers(profile_result):
    """AC1: Output references page numbers for each page."""
    import re
    stdout = profile_result.stdout
    assert stdout, "Profile command produced no output"
    assert re.search(r'\b\d+\b', stdout), "Output contains no page numbers"


def test_profile_shows_column_headers(profile_result):
    """AC1: Table pages include detected column headers (FY-prefixed expected for Winchester)."""
    stdout = profile_result.stdout
    assert "FY" in stdout, (
        "No FY-prefixed column headers found — table detection may be broken"
    )


def test_profile_includes_narrative_text(profile_result):
    """AC2: Output includes raw text lines for narrative (non-table) pages."""
    non_empty = [ln for ln in profile_result.stdout.splitlines() if ln.strip()]
    assert len(non_empty) > 5, (
        "Output has too few lines — narrative page raw text may be missing"
    )


# ---------------------------------------------------------------------------
# AC3: Non-existent PDF path → error message with path + non-zero exit
# ---------------------------------------------------------------------------

BAD_PDF = "/nonexistent/path/budget.pdf"


def test_nonexistent_pdf_exits_nonzero():
    """AC3: Exit code is non-zero when PDF file doesn't exist."""
    result = subprocess.run(
        [sys.executable, "src/load.py", "profile",
         "--town", "winchester", "--pdf", BAD_PDF],
        capture_output=True,
        text=True,
        cwd=WORKTREE,
    )
    assert result.returncode != 0, "Expected non-zero exit for missing PDF path"


def test_nonexistent_pdf_error_contains_path():
    """AC3: Error output contains the bad path so user can see what went wrong."""
    result = subprocess.run(
        [sys.executable, "src/load.py", "profile",
         "--town", "winchester", "--pdf", BAD_PDF],
        capture_output=True,
        text=True,
        cwd=WORKTREE,
    )
    combined = result.stdout + result.stderr
    assert BAD_PDF in combined, (
        f"Error message must contain the bad path '{BAD_PDF}'"
    )


# ---------------------------------------------------------------------------
# AC4: Missing required arguments → usage/help + non-zero exit
# ---------------------------------------------------------------------------

def test_no_subcommand_exits_nonzero():
    """AC4: Running load.py with no subcommand exits non-zero."""
    result = subprocess.run(
        [sys.executable, "src/load.py"],
        capture_output=True,
        text=True,
        cwd=WORKTREE,
    )
    assert result.returncode != 0, "Expected non-zero exit when no subcommand given"


def test_profile_missing_pdf_exits_nonzero():
    """AC4: Running profile without --pdf exits non-zero."""
    result = subprocess.run(
        [sys.executable, "src/load.py", "profile", "--town", "winchester"],
        capture_output=True,
        text=True,
        cwd=WORKTREE,
    )
    assert result.returncode != 0, "Expected non-zero exit when --pdf is omitted"


def test_profile_missing_town_exits_nonzero():
    """AC4: Running profile without --town exits non-zero."""
    result = subprocess.run(
        [sys.executable, "src/load.py", "profile", "--pdf", WINCHESTER_PDF],
        capture_output=True,
        text=True,
        cwd=WORKTREE,
    )
    assert result.returncode != 0, "Expected non-zero exit when --town is omitted"


# ---------------------------------------------------------------------------
# AC5: config/towns/winchester.yaml — structure and completeness
# ---------------------------------------------------------------------------

def test_winchester_yaml_exists():
    """AC5: Config file is present at config/towns/winchester.yaml."""
    path = os.path.join(WORKTREE, WINCHESTER_YAML)
    assert os.path.isfile(path), f"Config file not found: {WINCHESTER_YAML}"


@pytest.fixture(scope="module")
def winchester_config():
    path = os.path.join(WORKTREE, WINCHESTER_YAML)
    with open(path) as f:
        return yaml.safe_load(f)


def test_winchester_yaml_has_all_six_columns(winchester_config):
    """AC5: All 6 required extraction column names are present."""
    columns = winchester_config.get("extraction", {}).get("columns", {})
    for col in REQUIRED_COLUMNS:
        assert col in columns, f"Missing column in extraction.columns: '{col}'"


def test_winchester_yaml_metrics_2026_complete(winchester_config):
    """AC5: metrics.2026 block contains all 10 required fields."""
    metrics = winchester_config.get("metrics", {})
    # YAML keys may be int 2026 or string "2026" depending on serialization
    metrics_2026 = metrics.get(2026) or metrics.get("2026") or {}
    for field in REQUIRED_METRICS:
        assert field in metrics_2026, f"metrics.2026 missing field: '{field}'"
