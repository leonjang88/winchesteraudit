import pdfplumber
import csv
import yaml
import os
import re
from collections import Counter
from typing import Optional
from src.schema import get_db, init_db, ensure_town


def load_town_config(town: str) -> dict:
    """Load and return config/towns/{town}.yaml. Raise FileNotFoundError if missing."""
    path = os.path.join("config", "towns", f"{town}.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Town config not found: {path}")
    with open(path) as f:
        return yaml.safe_load(f)


def _normalize_header(text: Optional[str]) -> str:
    if text is None:
        return ""
    return " ".join(text.upper().split())


def _parse_amount(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    text = str(text).strip().replace(",", "").replace("$", "").replace(" ", "")
    if not text or text == "-":
        return None
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except (ValueError, TypeError):
        return None


def _detect_department(page) -> Optional[str]:
    """Extract department name from large-font text at page top."""
    chars = page.chars
    if not chars:
        return None

    sizes = [c.get("size", 0) for c in chars if c.get("size", 0) > 0]
    if not sizes:
        return None

    size_counts = Counter(round(s, 1) for s in sizes)
    body_size = size_counts.most_common(1)[0][0]
    header_min = body_size * 1.3
    top_zone = page.height * 0.20

    header_chars = sorted(
        [
            c
            for c in chars
            if c.get("size", 0) >= header_min and c.get("top", 9999) < top_zone
        ],
        key=lambda c: (round(c.get("top", 0), 1), c.get("x0", 0)),
    )

    if not header_chars:
        return None

    return "".join(c.get("text", "") for c in header_chars).strip() or None


def classify_page(page, config: dict) -> str:
    """Determine if a pdfplumber page is 'table', 'narrative', or 'mixed'.
    'table' = has extractable tables with column count matching config.
    'narrative' = text only, no tables.
    'mixed' = has both."""
    tables = page.extract_tables()
    config_col_count = len(config.get("extraction", {}).get("columns", {}))

    has_table = False
    if tables:
        for table in tables:
            if table and len(table) > 1:
                non_empty = sum(
                    1 for c in (table[0] or []) if c is not None and str(c).strip()
                )
                if non_empty >= max(2, config_col_count // 2):
                    has_table = True
                    break

    text = (page.extract_text() or "").strip()
    # Narrative text = multi-sentence prose (has sentence-ending punctuation + caps)
    has_narrative = bool(re.search(r"\.\s+[A-Z]", text)) and len(text) > 100

    if has_table and has_narrative:
        return "mixed"
    elif has_table:
        return "table"
    else:
        return "narrative"


def extract_table_rows(page, config: dict) -> list[dict]:
    """Extract structured rows from a table page.
    Each row dict has: department, account_code, description, amount, column_type, fiscal_year, row_type.
    Uses config['extraction']['columns'] to map header text → (fiscal_year, column_type).
    Detects subtotals via config['extraction']['subtotal_patterns']."""
    extraction = config.get("extraction", {})
    config_columns = extraction.get("columns", {})
    subtotal_patterns = [p.upper() for p in extraction.get("subtotal_patterns", [])]
    has_account_codes = extraction.get("has_account_codes", False)
    account_code_position = extraction.get("account_code_position", "first")

    # Normalized map: header text → (fiscal_year, column_type)
    col_map = {}
    for header, meta in config_columns.items():
        col_map[_normalize_header(header)] = (meta["fiscal_year"], meta["type"])

    department = _detect_department(page)

    rows = []
    tables = page.extract_tables()
    if not tables:
        return rows

    for table in tables:
        if not table or len(table) < 2:
            continue

        # Find the header row (first row with at least one matching config column)
        header_row = None
        header_idx = 0
        for i, row in enumerate(table):
            if row is None:
                continue
            matches = sum(1 for cell in row if _normalize_header(cell) in col_map)
            if matches >= 1:
                header_row = row
                header_idx = i
                break

        if header_row is None:
            continue

        # Map column index → (fiscal_year, column_type)
        col_idx_map: dict[int, tuple] = {}
        for idx, cell in enumerate(header_row):
            norm = _normalize_header(cell)
            if norm in col_map:
                col_idx_map[idx] = col_map[norm]

        if not col_idx_map:
            continue

        # Identify description and account_code column positions
        amount_indices = set(col_idx_map.keys())
        non_amount = [i for i in range(len(header_row)) if i not in amount_indices]

        if has_account_codes and account_code_position == "first" and len(non_amount) >= 2:
            acct_col: Optional[int] = non_amount[0]
            desc_col: int = non_amount[1]
        elif non_amount:
            acct_col = None
            desc_col = non_amount[0]
        else:
            continue

        # Process each data row
        for row in table[header_idx + 1 :]:
            if row is None:
                continue

            def _cell(idx: Optional[int]) -> Optional[str]:
                if idx is None or idx >= len(row):
                    return None
                val = row[idx]
                return val.strip() if isinstance(val, str) else (str(val) if val is not None else None)

            description = _cell(desc_col)
            if not description:
                continue

            account_code = _cell(acct_col) if acct_col is not None else None

            desc_upper = description.upper()
            if any(pat in desc_upper for pat in subtotal_patterns):
                row_type = "subtotal" if "SUB" in desc_upper else "total"
            else:
                row_type = "line_item"

            # One record per amount column
            for col_idx, (fiscal_year, column_type) in col_idx_map.items():
                amount = _parse_amount(_cell(col_idx))
                if amount is None:
                    continue
                rows.append(
                    {
                        "department": department,
                        "account_code": account_code,
                        "description": description,
                        "amount": amount,
                        "column_type": column_type,
                        "fiscal_year": fiscal_year,
                        "row_type": row_type,
                    }
                )

    return rows


def extract_narrative(page) -> str:
    """Extract raw text content from a narrative/mixed page."""
    return (page.extract_text() or "").strip()


def run_extract(town: str, pdf_path: str) -> None:
    """Main extraction orchestrator:
    1. load_town_config(town)
    2. init_db(), ensure_town(town, ...) using config values
    3. Open PDF, iterate every page
    4. Per page: classify → extract table rows OR narrative OR both
    5. Write line_items to DB with UNIQUE constraint (no duplicates on re-run)
    6. Write narratives to DB
    7. Write output/raw/{town}_fy2026.csv (all line items)
    8. Write output/raw/{town}_fy2026_narrative.txt (all narrative text)
    9. Write output/raw/{town}_fy2026_validation.txt (pages where detected columns < config columns)
    Creates output/raw/ directory if needed."""
    config = load_town_config(town)
    init_db()

    area_sq_miles = config.get("area_sq_miles")
    school_ranking = config.get("school_ranking")
    town_id = ensure_town(town, area_sq_miles=area_sq_miles, school_ranking=school_ranking)

    # Seed town_metrics from config
    metrics_by_year = config.get("metrics", {})
    conn = get_db()
    try:
        for fiscal_year, metrics in metrics_by_year.items():
            conn.execute(
                """INSERT OR IGNORE INTO town_metrics
                   (town_id, fiscal_year, population, households, median_household_income,
                    student_enrollment, road_miles, total_assessed_value, tax_rate,
                    total_levy, levy_limit, new_growth)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    town_id,
                    int(fiscal_year),
                    metrics.get("population"),
                    metrics.get("households"),
                    metrics.get("median_household_income"),
                    metrics.get("student_enrollment"),
                    metrics.get("road_miles"),
                    metrics.get("total_assessed_value"),
                    metrics.get("tax_rate"),
                    metrics.get("total_levy"),
                    metrics.get("levy_limit"),
                    metrics.get("new_growth"),
                ),
            )
        conn.commit()
    finally:
        conn.close()

    os.makedirs("output/raw", exist_ok=True)
    source_file = os.path.basename(pdf_path)
    config_col_count = len(config.get("extraction", {}).get("columns", {}))

    all_line_items: list[dict] = []
    all_narratives: list[dict] = []
    validation_lines: list[str] = []
    current_department = "Unknown"

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_num = page.page_number
            page_type = classify_page(page, config)

            if page_type in ("table", "mixed"):
                page_rows = extract_table_rows(page, config)

                # Update carry-forward department if page has a header
                for row in page_rows:
                    if row.get("department"):
                        current_department = row["department"]
                        break

                for row in page_rows:
                    row["department"] = current_department
                    row["source_file"] = source_file
                    row["source_page"] = page_num
                    all_line_items.append(row)

                # Validation: check column count vs config
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        if table and table[0]:
                            found_cols = sum(
                                1
                                for c in table[0]
                                if c is not None and str(c).strip()
                            )
                            if found_cols < config_col_count:
                                validation_lines.append(
                                    f"Page {page_num}: expected {config_col_count} columns, found {found_cols}"
                                )
                else:
                    validation_lines.append(
                        f"Page {page_num}: expected {config_col_count} columns, found 0 (narrative only)"
                    )

            if page_type in ("narrative", "mixed"):
                text = extract_narrative(page)
                if text:
                    # Attempt department detection for narrative pages too
                    dept = _detect_department(page) or current_department
                    all_narratives.append(
                        {
                            "town_id": town_id,
                            "fiscal_year": 2026,
                            "department": dept,
                            "page_number": page_num,
                            "content": text,
                            "source_file": source_file,
                        }
                    )

    # Write line_items + narratives to DB
    conn = get_db()
    try:
        for item in all_line_items:
            conn.execute(
                """INSERT OR IGNORE INTO line_items
                   (town_id, fiscal_year, department, account_code, description,
                    amount, column_type, row_type, source_file, source_page)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    town_id,
                    item["fiscal_year"],
                    item["department"],
                    item.get("account_code"),
                    item["description"],
                    item["amount"],
                    item["column_type"],
                    item["row_type"],
                    item["source_file"],
                    item["source_page"],
                ),
            )
        for narr in all_narratives:
            conn.execute(
                """INSERT OR IGNORE INTO narratives
                   (town_id, fiscal_year, department, page_number, content, source_file)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    narr["town_id"],
                    narr["fiscal_year"],
                    narr["department"],
                    narr["page_number"],
                    narr["content"],
                    narr["source_file"],
                ),
            )
        conn.commit()
    finally:
        conn.close()

    # Write CSV
    csv_path = os.path.join("output", "raw", f"{town}_fy2026.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "department",
                "account_code",
                "description",
                "amount",
                "fiscal_year",
                "column_type",
                "row_type",
                "source_file",
                "source_page",
            ],
        )
        writer.writeheader()
        for item in all_line_items:
            writer.writerow({k: item.get(k) for k in writer.fieldnames})

    # Write narrative txt
    narr_path = os.path.join("output", "raw", f"{town}_fy2026_narrative.txt")
    with open(narr_path, "w") as f:
        for narr in all_narratives:
            f.write(f"=== Page {narr['page_number']} | {narr['department']} ===\n")
            f.write(narr["content"])
            f.write("\n\n")

    # Write validation report
    val_path = os.path.join("output", "raw", f"{town}_fy2026_validation.txt")
    with open(val_path, "w") as f:
        if validation_lines:
            f.write("\n".join(validation_lines) + "\n")
        else:
            f.write("No validation issues detected.\n")

    print(
        f"Extracted {len(all_line_items)} line items, {len(all_narratives)} narrative pages"
    )
    print(f"Output: {csv_path}, {narr_path}, {val_path}")
