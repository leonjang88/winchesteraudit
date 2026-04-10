# Extraction Learnings — Winchester Budget Audit

Hard-won knowledge from building the extraction pipeline. Read this before
adding a new town or debugging extraction issues.

---

## 1. PDF Parsing Quirks

**Winchester's PDF stores budget data as text streams, not PDF table objects.**
pdfplumber's `extract_tables()` only finds real tables on ~16 of 143 pages
(the Education section). The other ~80 budget pages use whitespace-aligned
columns with no borders — pdfplumber sees nothing.

- **Always profile a PDF first** (`python src/load.py profile`) to understand
  whether data lives in tables or text streams.
- The extraction engine has two paths: bordered-table extraction via
  `extract_tables()`, and text-layout extraction via `extract_words()` with
  x-center column matching. The text path is the workhorse for Winchester.
- One page triggers a pdfplumber `KeyError` inside `snap_edges` during table
  detection. `_safe_extract_tables()` wraps the call in try/except to prevent
  a crash on that page.

## 2. Column Matching

**Merged header cells break exact matching.** Winchester's PDF has header cells
like `"FY24 FY25 FY26 FY26 FY26\nProgram Costs Actual Budget Request Manager FinCom"` —
one cell containing all column names. Exact matching against config column
names (e.g., "FY24 BUDGET") never matches.

Two solutions implemented:
- **Substring matching** (`_col_map_match`): check if any config column name
  appears as a substring of the cell text.
- **Multi-row header combining**: if the first row of a table has no matches,
  combine it with the second row (e.g., "FY24" + "BUDGET" → "FY24 BUDGET").

For text-layout pages, column positions are detected by finding the FY year
labels and column type labels (BUDGET, ACTUAL, etc.) as separate words, then
pairing them by x-center proximity. Data amounts are matched to the nearest
column by x-center distance (threshold: 50px).

## 3. Department Detection

**Font-size heuristics fail on Winchester.** The PDF uses uniform font sizes
throughout — `body_size * 1.3` never finds larger header text.

The working fallback:
1. Use `extract_words()` to get words with bounding boxes
2. Group words in the top 18% of the page into lines by y-position
3. Skip lines containing FY markers, column type labels, digits, or bullets
4. The first remaining line is the department candidate
5. `_validate_department_name()` rejects garbage: lines starting with
   digits/lowercase/bullets, ending with periods, containing too many commas,
   or exceeding 60 characters

Department carries forward across pages via `current_department` in
`run_extract()` until a new header is detected. This is correct because
Winchester's budget groups multiple pages under one department header.

## 4. Import Path Gotcha

Running `python src/load.py` from the project root sets `sys.path[0]` to
`src/`, not the project root. So `from src.extract import ...` fails — Python
looks for `src/src/extract.py`.

**Fix**: At the top of `load.py`:
```python
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
```
Then use `from extract import ...` and `from schema import ...` everywhere
inside `src/`.

## 5. Idempotency

The UNIQUE constraint on `line_items` is:
```sql
UNIQUE(town_id, fiscal_year, department, account_code, description, column_type)
```

**NULL breaks UNIQUE in SQLite.** SQLite treats `NULL != NULL`, so rows with
`account_code = NULL` are never considered duplicates — every re-run inserts
them again.

**Fix**: Coerce NULL to empty string before insert:
```python
item.get("account_code") or ""
```

After this fix, `INSERT OR IGNORE` works correctly and re-runs produce
identical row counts.

## 6. What Validators Caught

- **End-to-end testing revealed the text-stream issue.** Unit tests and code
  review couldn't detect that 127 of 143 pages had no PDF table objects. Only
  running the real extraction against the real Winchester PDF surfaced this.
- **Department names like "0112101 51101 PERMANENT 888,042..."** — data rows
  leaking into department detection. The font-size path returned raw text
  without any validation. Adding `_validate_department_name()` to both paths
  eliminated garbage.
- **Idempotency regression** — the NULL account_code issue only appeared when
  Zanoba's test ran extract twice in sequence. The first run always looked
  correct.

## 7. Patterns for Future Town Extractions

When adding a new town (e.g., Lexington, Belmont):

1. **Profile first**: `python src/load.py profile --town X --pdf budgets/X.pdf`
   to see whether data is in tables or text streams.
2. **Create town YAML**: `config/towns/{town}.yaml` with metrics, column
   mappings, account code settings, and subtotal patterns. Every town has
   different column headers (some use "APPROPRIATION" instead of "BUDGET").
3. **Expect config tuning**: the extraction engine is config-driven, but each
   town's PDF has quirks — merged headers, different column counts, different
   account code formats, different department header styles.
4. **Check summary vs. detail pages**: Winchester's PDF includes both summary
   tables (totals by category) and detail tables (line items by department).
   The extraction captures both. Phase 3 analysis should filter by department
   to exclude summary rows.
5. **Rescue for stragglers**: pages the extraction engine can't parse go into
   the validation report. `rescue` spawns Claude CLI subagents to extract them
   via LLM — good for the long tail of weird pages.
6. **Spot-check amounts**: always verify known values (e.g., Fire PERMANENT,
   Police PERMANENT) against the source PDF before trusting the full dataset.
