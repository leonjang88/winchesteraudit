import pdfplumber
import csv
import yaml
import os
import re
from collections import Counter
from typing import Optional
from schema import get_db, init_db, ensure_town


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


def _col_map_match(cell_text: Optional[str], col_map: dict) -> Optional[tuple]:
    """Match a cell to a config column, handling merged/multi-line cells."""
    norm = _normalize_header(cell_text)
    if not norm:
        return None
    if norm in col_map:
        return col_map[norm]
    for col_name, meta in col_map.items():
        if col_name in norm:
            return meta
    return None


def _safe_extract_tables(page) -> list:
    """Extract tables from page, returning empty list if pdfplumber hits an edge case."""
    try:
        return page.extract_tables() or []
    except (KeyError, TypeError, ValueError):
        return []


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


_TYPE_LABELS = {"BUDGET", "ACTUAL", "REQUEST", "MANAGER", "FINCOM", "RECOMMENDED", "APPROP.", "APPROPRIATION", "ESTIMATED"}


def _build_col_map(config: dict) -> dict:
    """Build normalized column map from config: header_text → (fiscal_year, column_type)."""
    col_map = {}
    for header, meta in config.get("extraction", {}).get("columns", {}).items():
        col_map[_normalize_header(header)] = (meta["fiscal_year"], meta["type"])
    return col_map


def _validate_department_name(text: str) -> bool:
    """Return True if text looks like a legitimate department name."""
    if not text or len(text) < 3:
        return False
    # Starts with digit → likely account code or data row
    if text[0].isdigit():
        return False
    # Starts with lowercase → unlikely department header
    if text[0].islower():
        return False
    # Starts with bullet
    if text[0] in ("•", "-", "*", "·"):
        return False
    # Contains dollar amounts (digit groups with commas like 888,042)
    if re.search(r"\d{1,3}(,\d{3})+", text):
        return False
    # FY year markers → column header row, not a department
    if re.match(r"^FY\d{2,4}$", text, re.IGNORECASE):
        return False
    if len(re.findall(r"FY\d{2,4}", text, re.IGNORECASE)) >= 2:
        return False
    # Known non-department labels
    _NON_DEPT = {
        "EXPENSES", "CATEGORY", "STAFFING", "TOTAL", "SUBTOTAL",
        "PROGRAM COSTS", "PERSONNEL SERVICES", "REVENUE SOURCE",
    }
    if text.upper().strip() in _NON_DEPT:
        return False
    # Too many commas → likely data row
    if text.count(",") > 2:
        return False
    # Too long → likely narrative text, not a header
    if len(text) > 60:
        return False
    # Ends with period → likely narrative sentence
    if text.rstrip().endswith("."):
        return False
    return True


def _detect_department(page) -> Optional[str]:
    """Extract department name from large-font text at page top.
    Falls back to text-line detection when font-size heuristic fails."""
    chars = page.chars

    # --- Font-size heuristic ---
    if chars:
        sizes = [c.get("size", 0) for c in chars if c.get("size", 0) > 0]
        if sizes:
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

            if header_chars:
                result = "".join(c.get("text", "") for c in header_chars).strip()
                if result and _validate_department_name(result):
                    return result

    # --- Text fallback: word-position-based ---
    try:
        words = page.extract_words()
    except Exception:
        words = []

    if words:
        top_zone = page.height * 0.18
        top_words = [w for w in words if w.get("top", 9999) < top_zone]
        if top_words:
            from itertools import groupby

            top_words_sorted = sorted(
                top_words, key=lambda w: (round(w["top"]), w["x0"])
            )
            lines = []
            for _, group in groupby(
                top_words_sorted, key=lambda w: round(w["top"])
            ):
                line_words = list(group)
                line_text = " ".join(w["text"] for w in line_words).strip()
                lines.append(line_text)

            _COL_KEYWORDS = ["FY2", "BUDGET", "ACTUAL", "REQUEST", "FINCOM", "MANAGER"]
            for line in lines:
                if not line:
                    continue
                upper = line.upper()
                # Skip lines with 2+ column-type keywords (header rows)
                col_hits = sum(1 for tok in _COL_KEYWORDS if tok in upper)
                if col_hits >= 2:
                    continue
                if (
                    line.replace(",", "")
                    .replace(".", "")
                    .replace("-", "")
                    .replace(" ", "")
                    .isdigit()
                ):
                    continue
                if not _validate_department_name(line):
                    continue
                return line

    return None


def _page_has_text_headers(page, col_map: dict) -> bool:
    """Check if a page has column header keywords in its text (for borderless tables)."""
    try:
        words = page.extract_words()
    except Exception:
        return False
    word_texts = {w["text"].upper() for w in words}
    return len(word_texts.intersection(_TYPE_LABELS)) >= 3


def classify_page(page, config: dict) -> str:
    """Determine if a pdfplumber page is 'table', 'narrative', or 'mixed'.
    'table' = has extractable tables (bordered or text-layout) with column headers.
    'narrative' = text only, no tabular data.
    'mixed' = has both."""
    col_map = _build_col_map(config)

    # Check for bordered tables via pdfplumber
    tables = _safe_extract_tables(page)
    has_bordered_table = False
    if tables:
        for table in tables:
            if table and len(table) > 1:
                rows_to_check = [r for r in table[:2] if r is not None]
                for row in rows_to_check:
                    matches = sum(
                        1 for c in row if _col_map_match(c, col_map) is not None
                    )
                    if matches >= 1:
                        has_bordered_table = True
                        break
                if has_bordered_table:
                    break

    # Check for text-layout tables (borderless — column header keywords in text)
    has_text_table = not has_bordered_table and _page_has_text_headers(page, col_map)
    has_table = has_bordered_table or has_text_table

    text = (page.extract_text() or "").strip()
    has_narrative = bool(re.search(r"\.\s+[A-Z]", text)) and len(text) > 100

    if has_table and has_narrative:
        return "mixed"
    elif has_table:
        return "table"
    else:
        return "narrative"


def _extract_text_rows(page, config: dict) -> list[dict]:
    """Extract structured rows from a text-layout page (no table borders).
    Uses word bounding boxes to detect column positions and match amounts."""
    extraction = config.get("extraction", {})
    config_columns = extraction.get("columns", {})
    subtotal_patterns = [p.upper() for p in extraction.get("subtotal_patterns", [])]
    has_account_codes = extraction.get("has_account_codes", False)
    col_map = _build_col_map(config)

    try:
        words = page.extract_words()
    except Exception:
        return []
    if not words:
        return []

    # Sort words into lines by y-position
    words_sorted = sorted(words, key=lambda w: (round(w["top"]), w["x0"]))
    word_lines: list[list[dict]] = []
    current_line: list[dict] = []
    current_top: Optional[float] = None
    TOLERANCE = 3

    for w in words_sorted:
        if current_top is None or abs(w["top"] - current_top) <= TOLERANCE:
            current_line.append(w)
            if current_top is None:
                current_top = w["top"]
        else:
            if current_line:
                word_lines.append(current_line)
            current_line = [w]
            current_top = w["top"]
    if current_line:
        word_lines.append(current_line)

    # Find the type header line (contains BUDGET, ACTUAL, REQUEST, MANAGER, FINCOM)
    type_line_idx = None
    for i, line_words in enumerate(word_lines):
        line_text_set = {w["text"].upper() for w in line_words}
        if len(line_text_set.intersection(_TYPE_LABELS)) >= 3:
            type_line_idx = i
            break

    if type_line_idx is None:
        return []

    # Find year header line (should be just above the type line)
    year_line_idx = None
    for i in range(type_line_idx - 1, max(type_line_idx - 3, -1), -1):
        if i < 0:
            break
        year_count = sum(
            1 for w in word_lines[i] if re.match(r"^FY\d{2,4}$", w["text"].upper())
        )
        if year_count >= 3:
            year_line_idx = i
            break

    type_words = word_lines[type_line_idx]
    year_words = word_lines[year_line_idx] if year_line_idx is not None else []

    # Pair each type label with its nearest year label by x-center
    col_defs: list[tuple[float, tuple]] = []  # (x_center, (fiscal_year, column_type))
    year_positions = []
    for w in year_words:
        if re.match(r"^FY\d{2,4}$", w["text"].upper()):
            yr_str = w["text"][2:]
            yr = int(yr_str) if len(yr_str) > 2 else 2000 + int(yr_str)
            year_positions.append(((w["x0"] + w["x1"]) / 2, yr))

    for tw in type_words:
        type_name = tw["text"].upper()
        if type_name not in _TYPE_LABELS:
            continue
        tx_center = (tw["x0"] + tw["x1"]) / 2

        # Find nearest year
        nearest_year = None
        min_dist = float("inf")
        for yx, year in year_positions:
            dist = abs(yx - tx_center)
            if dist < min_dist:
                min_dist = dist
                nearest_year = year

        if nearest_year is None:
            continue

        col_key = f"FY{str(nearest_year)[-2:]} {type_name}"
        norm = _normalize_header(col_key)
        if norm in col_map:
            col_defs.append((tx_center, col_map[norm]))

    if not col_defs:
        return []

    col_defs.sort(key=lambda c: c[0])

    # Leftmost column boundary — words before this are description/account code
    leftmost_col_x = col_defs[0][0] - 40

    department = _detect_department(page)
    rows = []

    # Parse data lines after the header
    data_start = type_line_idx + 1
    for line_words in word_lines[data_start:]:
        if not line_words:
            continue

        line_sorted = sorted(line_words, key=lambda w: w["x0"])

        # Split into pre-column (description area) and column (amount area) words
        pre_words = [w for w in line_sorted if (w["x0"] + w["x1"]) / 2 < leftmost_col_x]
        col_words = [w for w in line_sorted if (w["x0"] + w["x1"]) / 2 >= leftmost_col_x]

        if not pre_words:
            continue

        # Parse account code
        account_code = None
        desc_word_list = pre_words

        if has_account_codes:
            acct_parts = []
            for w in pre_words:
                if re.match(r"^\d[\d,]*$", w["text"]):
                    acct_parts.append(w["text"].replace(",", ""))
                else:
                    break
            if acct_parts:
                account_code = " ".join(acct_parts)
                desc_word_list = pre_words[len(acct_parts):]

        description = " ".join(w["text"] for w in desc_word_list).strip()
        if not description:
            continue

        # Row type detection
        desc_upper = description.upper()
        if any(pat in desc_upper for pat in subtotal_patterns):
            row_type = "subtotal" if "SUB" in desc_upper else "total"
        else:
            row_type = "line_item"

        # Assign each col_word to its nearest column by x-center
        for col_x_center, (fiscal_year, column_type) in col_defs:
            best_word = None
            min_dist = float("inf")
            for w in col_words:
                wx_center = (w["x0"] + w["x1"]) / 2
                dist = abs(wx_center - col_x_center)
                if dist < min_dist:
                    min_dist = dist
                    best_word = w

            if best_word is None or min_dist > 50:
                continue

            amount = _parse_amount(best_word["text"])
            if amount is not None:
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
                # Remove used word to prevent double-assignment
                col_words = [w for w in col_words if w is not best_word]

    return rows


def extract_table_rows(page, config: dict) -> list[dict]:
    """Extract structured rows from a table page.
    Tries pdfplumber bordered-table extraction first, then falls back to
    text-layout extraction for borderless pages."""
    extraction = config.get("extraction", {})
    config_columns = extraction.get("columns", {})
    subtotal_patterns = [p.upper() for p in extraction.get("subtotal_patterns", [])]
    has_account_codes = extraction.get("has_account_codes", False)
    account_code_position = extraction.get("account_code_position", "first")
    col_map = _build_col_map(config)

    department = _detect_department(page)

    # --- Try bordered tables first ---
    rows = []
    tables = _safe_extract_tables(page)

    found_header = False
    for table in tables:
        if not table or len(table) < 2:
            continue

        header_row = None
        header_idx = 0

        for i, row in enumerate(table):
            if row is None:
                continue
            matches = sum(
                1 for cell in row if _col_map_match(cell, col_map) is not None
            )
            if matches >= 1:
                header_row = row
                header_idx = i
                found_header = True
                break
            if i + 1 < len(table) and table[i + 1] is not None:
                next_row = table[i + 1]
                combined = []
                for j in range(max(len(row), len(next_row))):
                    top = (row[j] if j < len(row) else None) or ""
                    bot = (next_row[j] if j < len(next_row) else None) or ""
                    combined.append(f"{top} {bot}".strip())
                matches = sum(
                    1 for cell in combined if _col_map_match(cell, col_map) is not None
                )
                if matches >= 1:
                    header_row = combined
                    header_idx = i + 1
                    found_header = True
                    break

        if header_row is None:
            continue

        col_idx_map: dict[int, tuple] = {}
        for idx, cell in enumerate(header_row):
            match = _col_map_match(cell, col_map)
            if match is not None:
                col_idx_map[idx] = match

        if not col_idx_map:
            continue

        amount_indices = set(col_idx_map.keys())
        non_amount = [i for i in range(len(header_row)) if i not in amount_indices]

        if (
            has_account_codes
            and account_code_position == "first"
            and len(non_amount) >= 2
        ):
            acct_col: Optional[int] = non_amount[0]
            desc_col: int = non_amount[1]
        elif non_amount:
            acct_col = None
            desc_col = non_amount[0]
        else:
            continue

        for row in table[header_idx + 1 :]:
            if row is None:
                continue

            def _cell(idx: Optional[int]) -> Optional[str]:
                if idx is None or idx >= len(row):
                    return None
                val = row[idx]
                return (
                    val.strip()
                    if isinstance(val, str)
                    else (str(val) if val is not None else None)
                )

            description = _cell(desc_col)
            if not description:
                continue

            account_code = _cell(acct_col) if acct_col is not None else None

            desc_upper = description.upper()
            if any(pat in desc_upper for pat in subtotal_patterns):
                row_type = "subtotal" if "SUB" in desc_upper else "total"
            else:
                row_type = "line_item"

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

    # --- Fallback: text-layout extraction if no bordered tables yielded data ---
    if not rows and not found_header:
        rows = _extract_text_rows(page, config)

    return rows


def extract_narrative(page) -> str:
    """Extract raw text content from a narrative/mixed page."""
    return (page.extract_text() or "").strip()


def run_extract(town: str, pdf_path: str) -> None:
    """Main extraction orchestrator."""
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
                tables = _safe_extract_tables(page)
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

            if page_type in ("narrative", "mixed"):
                text = extract_narrative(page)
                if text:
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

    # Wipe existing data for this town, then insert fresh
    conn = get_db()
    try:
        conn.execute("DELETE FROM line_items WHERE town_id = ?", (town_id,))
        conn.execute("DELETE FROM narratives WHERE town_id = ?", (town_id,))
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
                    item.get("account_code") or "",
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
