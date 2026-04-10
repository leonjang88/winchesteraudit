import subprocess
import json
import re
import os
from schema import get_db, init_db, ensure_town
from extract import load_town_config


def parse_validation_report(town: str) -> list[int]:
    """Read output/raw/{town}_fy2026_validation.txt, return list of failed page numbers.
    Parse lines like 'Page 42: expected 6 columns, found 3' → [42].
    Return empty list if file doesn't exist or has no failed pages."""
    path = os.path.join("output", "raw", f"{town}_fy2026_validation.txt")
    if not os.path.exists(path):
        return []
    pages = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line == "No validation issues detected.":
                return []
            match = re.match(r"Page (\d+):", line)
            if match:
                pages.append(int(match.group(1)))
    return pages


def build_prompt(town: str, pdf_path: str, page_num: int, config: dict) -> str:
    """Build prompt for Claude CLI subagent to extract table data from a specific page."""
    columns = config.get("extraction", {}).get("columns", {})
    col_desc = "\n".join(
        f'  - "{name}" → fiscal_year={meta["fiscal_year"]}, column_type="{meta["type"]}"'
        for name, meta in columns.items()
    )
    subtotal_patterns = config.get("extraction", {}).get("subtotal_patterns", [])

    return f"""Extract budget table data from page {page_num} of the PDF file "{pdf_path}".

Read ONLY page {page_num}. Extract every row of budget data as a JSON array.

Column mappings (map header text to these values):
{col_desc}

Each row in the JSON array must have these fields:
- "department": string — the department name from the page header
- "account_code": string or null — account/line code if present
- "description": string — the line item description
- "amount": number — the dollar amount
- "fiscal_year": integer — from the column mapping above
- "column_type": string — from the column mapping above
- "row_type": string — "line_item", "subtotal" if description contains {subtotal_patterns}, or "total" if description contains "TOTAL"

Emit ONE row per (line item × column). A single line with 6 amount columns produces 6 rows.

Return ONLY a valid JSON array. No markdown, no explanation, no extra text.
Example: [{{"department":"Fire","description":"PERMANENT","amount":5735607,"fiscal_year":2026,"column_type":"request","row_type":"line_item","account_code":"51101"}}]"""


def spawn_subagent(prompt: str) -> tuple[int, str]:
    """Run claude CLI as subprocess. Returns (returncode, stdout)."""
    result = subprocess.run(
        ["claude", "--model", "sonnet", "-p", prompt],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return result.returncode, result.stdout


def parse_subagent_output(output: str) -> list[dict]:
    """Parse JSON array from subagent stdout. Handle markdown fences, extra text.
    Raise ValueError if not valid JSON array."""
    text = output.strip()

    # Strip markdown code fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # Try to find a JSON array in the text
    bracket_match = re.search(r"\[.*\]", text, re.DOTALL)
    if bracket_match:
        text = bracket_match.group(0)

    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array, got {type(data).__name__}")
    return data


def insert_rescued_rows(
    rows: list[dict], town_id: int, source_file: str, page_num: int
) -> int:
    """Insert rescued rows into line_items. INSERT OR IGNORE for no duplicates.
    Returns count inserted."""
    conn = get_db()
    inserted = 0
    try:
        for row in rows:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO line_items
                   (town_id, fiscal_year, department, account_code, description,
                    amount, column_type, row_type, source_file, source_page)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    town_id,
                    row.get("fiscal_year"),
                    row.get("department", "Unknown"),
                    row.get("account_code") or "",
                    row.get("description", ""),
                    row.get("amount"),
                    row.get("column_type"),
                    row.get("row_type", "line_item"),
                    source_file,
                    page_num,
                ),
            )
            inserted += cursor.rowcount
        conn.commit()
    finally:
        conn.close()
    return inserted


def run_rescue(town: str, pdf_path: str) -> None:
    """Main rescue orchestrator."""
    pages = parse_validation_report(town)
    if not pages:
        print("No pages to rescue.")
        return

    config = load_town_config(town)
    init_db()

    area_sq_miles = config.get("area_sq_miles")
    school_ranking = config.get("school_ranking")
    town_id = ensure_town(town, area_sq_miles=area_sq_miles, school_ranking=school_ranking)
    source_file = os.path.basename(pdf_path)

    print(f"Rescuing {len(pages)} pages: {pages}")

    rescued = 0
    failed = 0

    for page_num in pages:
        try:
            print(f"  Page {page_num}...", end=" ", flush=True)
            prompt = build_prompt(town, pdf_path, page_num, config)
            returncode, stdout = spawn_subagent(prompt)

            if returncode != 0:
                print(f"FAILED (exit code {returncode})")
                failed += 1
                continue

            rows = parse_subagent_output(stdout)
            count = insert_rescued_rows(rows, town_id, source_file, page_num)
            print(f"OK ({len(rows)} rows parsed, {count} new)")
            rescued += 1

        except subprocess.TimeoutExpired:
            print("TIMEOUT (120s)")
            failed += 1
        except (json.JSONDecodeError, ValueError) as e:
            print(f"PARSE ERROR ({e})")
            failed += 1
        except Exception as e:
            print(f"ERROR ({e})")
            failed += 1

    print(f"\nRescue complete: {rescued} pages rescued, {failed} failed")
