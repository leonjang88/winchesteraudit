import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from schema import get_db


def run_validate(town: str) -> int:
    """Run data quality validation checks for a town.

    Returns:
        0 - all checks pass
        1 - warnings detected
        2 - critical issues found
    """
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM towns WHERE name = ?", (town,)).fetchone()
        if row is None:
            print(f"CRITICAL: Town '{town}' not found in database. Run extract first.")
            return 2
        town_id = row[0]

        exit_code = 0

        total_rows = conn.execute(
            "SELECT COUNT(*) FROM line_items WHERE town_id = ?", (town_id,)
        ).fetchone()[0]

        if total_rows == 0:
            print(f"CRITICAL: No line items found for '{town}'. Run extract first.")
            return 2

        print(f"=== Data Quality Report: {town} ===")
        print(f"Total line items: {total_rows:,}")
        print()

        # ── AC2: Page coverage ──────────────────────────────────────────────
        print("── Page Coverage ──")
        distinct_data_pages = conn.execute(
            "SELECT COUNT(DISTINCT source_page) FROM line_items WHERE town_id = ?",
            (town_id,),
        ).fetchone()[0]
        narrative_pages = conn.execute(
            "SELECT COUNT(*) FROM narratives WHERE town_id = ?", (town_id,)
        ).fetchone()[0]
        total_known = distinct_data_pages + narrative_pages
        pct = distinct_data_pages / total_known * 100 if total_known > 0 else 0
        print(
            f"  {distinct_data_pages} pages with extracted data, "
            f"{narrative_pages} narrative-only pages "
            f"({pct:.1f}% extraction coverage)"
        )
        print()

        # ── AC2: Column count consistency ───────────────────────────────────
        print("── Column Types ──")
        col_rows = conn.execute(
            """SELECT column_type, COUNT(*) as cnt
               FROM line_items WHERE town_id = ?
               GROUP BY column_type ORDER BY cnt DESC""",
            (town_id,),
        ).fetchall()
        for col_type, cnt in col_rows:
            print(f"  {col_type}: {cnt:,} rows")
        print()

        # ── AC2: Zero amounts ───────────────────────────────────────────────
        zero_count = conn.execute(
            "SELECT COUNT(*) FROM line_items WHERE town_id = ? AND amount = 0",
            (town_id,),
        ).fetchone()[0]
        if zero_count > 0:
            pct_z = zero_count / total_rows * 100
            print(f"[WARN]  Zero amounts: {zero_count:,} rows ({pct_z:.1f}%) have amount = $0")
            exit_code = max(exit_code, 1)
        else:
            print("[OK]    Zero amounts: none found")

        # ── AC2: Negative amounts ───────────────────────────────────────────
        neg_count = conn.execute(
            """SELECT COUNT(*) FROM line_items
               WHERE town_id = ? AND amount < 0""",
            (town_id,),
        ).fetchone()[0]
        if neg_count > 0:
            print(f"[WARN]  Negative amounts: {neg_count:,} rows have amount < 0")
            examples = conn.execute(
                """SELECT description, amount, column_type, source_page
                   FROM line_items
                   WHERE town_id = ? AND amount < 0
                   LIMIT 3""",
                (town_id,),
            ).fetchall()
            for desc, amt, ct, pg in examples:
                print(f"          Page {pg}: {desc[:55]} ({ct}) = ${amt:,.0f}")
            exit_code = max(exit_code, 1)
        else:
            print("[OK]    Negative amounts: none found")

        # ── AC2: Duplicate detection ────────────────────────────────────────
        dup_count = conn.execute(
            """SELECT COUNT(*) FROM (
                   SELECT department, description, column_type, fiscal_year, account_code
                   FROM line_items WHERE town_id = ?
                   GROUP BY department, description, column_type, fiscal_year, account_code
                   HAVING COUNT(*) > 1
               )""",
            (town_id,),
        ).fetchone()[0]
        if dup_count > 0:
            print(
                f"[CRITICAL] Duplicates: {dup_count} (dept, description, column_type, fiscal_year, account_code) "
                "combos appear more than once"
            )
            exit_code = max(exit_code, 2)
        else:
            print("[OK]    Duplicates: none found")
        print()

        # ── AC3: Subtotal validation ────────────────────────────────────────
        print("── Subtotal Validation ──")
        subtotals = conn.execute(
            """SELECT department, fiscal_year, column_type, amount, source_page
               FROM line_items
               WHERE town_id = ? AND row_type IN ('subtotal', 'total')
               ORDER BY department, fiscal_year, column_type""",
            (town_id,),
        ).fetchall()

        checked = 0
        sub_warnings = 0
        sub_warning_lines: list[str] = []
        for dept, fy, ct, sub_amount, sub_page in subtotals:
            if sub_amount <= 0:
                continue
            # Scope to same page — avoids cross-table false positives
            line_sum = (
                conn.execute(
                    """SELECT COALESCE(SUM(amount), 0) FROM line_items
                       WHERE town_id = ? AND department = ? AND fiscal_year = ?
                       AND column_type = ? AND source_page = ? AND row_type = 'line_item'""",
                    (town_id, dept, fy, ct, sub_page),
                ).fetchone()[0]
            )
            if line_sum == 0:
                continue
            diff_pct = abs(line_sum - sub_amount) / sub_amount * 100
            checked += 1
            if diff_pct > 1.0:
                sub_warning_lines.append(
                    f"  [WARN]  {dept[:45]} FY{fy} {ct} (p{sub_page}): "
                    f"items=${line_sum:,.0f}  subtotal=${sub_amount:,.0f}  ({diff_pct:.1f}% diff)"
                )
                sub_warnings += 1
                exit_code = max(exit_code, 1)

        DISPLAY_LIMIT = 15
        for line in sub_warning_lines[:DISPLAY_LIMIT]:
            print(line)
        if len(sub_warning_lines) > DISPLAY_LIMIT:
            print(f"  ... and {len(sub_warning_lines) - DISPLAY_LIMIT} more subtotal warnings")

        if checked == 0:
            print("  No subtotals to validate")
        elif sub_warnings == 0:
            print(f"  [OK]  All {checked} checked subtotals within 1%")
        print()

        # ── AC4: Department completeness ────────────────────────────────────
        print("── Extracted Departments (eyeball against PDF table of contents) ──")
        depts = conn.execute(
            """SELECT DISTINCT department, COUNT(*) as n
               FROM line_items WHERE town_id = ?
               GROUP BY department ORDER BY n DESC""",
            (town_id,),
        ).fetchall()
        for dept, n in depts:
            print(f"  [{n:4d}]  {dept}")
        print()

        # ── AC5: Sampling QA ────────────────────────────────────────────────
        print("── Sampling QA — 3 random tables (spot-check against PDF) ──")
        samples = conn.execute(
            """SELECT DISTINCT department, fiscal_year, column_type, source_page
               FROM line_items WHERE town_id = ?
               ORDER BY RANDOM() LIMIT 3""",
            (town_id,),
        ).fetchall()

        for i, (dept, fy, ct, page) in enumerate(samples, 1):
            rows = conn.execute(
                """SELECT description, amount, row_type
                   FROM line_items
                   WHERE town_id = ? AND department = ? AND fiscal_year = ?
                   AND column_type = ? AND source_page = ?
                   ORDER BY amount DESC LIMIT 8""",
                (town_id, dept, fy, ct, page),
            ).fetchall()
            print(
                f"  Sample {i}  |  PDF page {page}  |  FY{fy} {ct}  |  {dept[:60]}"
            )
            for desc, amt, rt in rows:
                tag = " ◀ subtotal/total" if rt in ("subtotal", "total") else ""
                print(f"    ${amt:>14,.0f}  {desc[:60]}{tag}")
            print()

        # ── Summary ─────────────────────────────────────────────────────────
        label = {0: "PASS", 1: "WARNINGS", 2: "CRITICAL"}[exit_code]
        print(f"=== Result: {label} (exit {exit_code}) ===")
        return exit_code

    finally:
        conn.close()
