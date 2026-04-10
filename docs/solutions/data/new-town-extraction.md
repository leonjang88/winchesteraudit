---
title: "Adding a new town extraction (Lexington pattern)"
category: "data"
tags: [extraction, town, yaml, config, pdf, pdfplumber, budget]
story: "#10"
project: "winchesteraudit"
date: "2026-04-10"
cycles: 1
---

## What Was Built

A YAML config (`config/towns/lexington.yaml`) that drives the extraction engine for Lexington's FY2026 budget PDF. Lexington has no account codes and uses seven column variants across two fiscal years (FY2025 + FY2026), named differently from Winchester. The extraction produced 3016 line items across 274 narrative pages with exact-match totals for Education, DPW, and Public Safety.

## Key Patterns

### Towns without account codes
```yaml
extraction:
  has_account_codes: false
  # no account_code_position needed
```
Winchester uses `has_account_codes: true` with `account_code_position: "first"`. Lexington has none — omit both fields when the town's PDF has no account codes.

### Column naming varies by town
Winchester uses `FY24 BUDGET` / `FY24 ACTUAL` style. Lexington uses full year + verbose type:
```yaml
columns:
  "FY2023 ACTUAL":        { fiscal_year: 2023, type: actual }
  "FY2024 ACTUAL":        { fiscal_year: 2024, type: actual }
  "FY2025 APPROPRIATION": { fiscal_year: 2025, type: appropriation }
  "FY2025 REVISED":       { fiscal_year: 2025, type: revised }
  "FY2025 ESTIMATE":      { fiscal_year: 2025, type: estimate }
  "FY2026 RECOMMENDED":   { fiscal_year: 2026, type: recommended }
  "FY2026 PROJECTED":     { fiscal_year: 2026, type: projected }
```
Always run `profile` first to discover the actual column header text before writing the YAML.

### Subtotal pattern casing matters
Winchester: `["TOTAL", "SUB-TOTAL"]` (all caps). Lexington: `["Total", "Subtotal"]` (title case). Match the PDF's exact casing.

### Profile-first workflow
```bash
python src/load.py profile --town lexington --pdf budgets/lexington_fy2026_budget.pdf
```
Profile reveals: text-stream vs table pages, column header text, FY label format. Write the YAML *after* seeing profile output — never guess column names.

## Test Patterns

No dedicated test file for this story (lean team, no Zanoba). Validation was:
1. `extract` runs without error
2. CSV has non-zero rows, all with `source_page`
3. Spot-check 3 key departments vs PDF (Education, DPW, Public Safety)
4. Run extract twice → same row count (idempotency)

## Reuse Notes

- `config/towns/winchester.yaml` is the canonical template. Copy it, then adjust column names and `has_account_codes`.
- The extraction engine (`src/extract.py`) is config-driven — no code changes needed for a new town unless the PDF has a fundamentally different layout.
- `_safe_extract_tables()` wraps pdfplumber's KeyError — already in place, no action needed.

## Gotchas

- **Cliff committed to local `main` instead of the story branch.** The file was created in the worktree directory but committed from the main project root. Rudy caught it, cherry-picked (blocked by untracked file), then committed directly from the worktree. Future: Cliff must `cd` into the worktree path before any `git add/commit`.
- **`output/` is gitignored.** CSV files aren't committed — Roxy must re-run extraction to validate AC4/AC5. This is by design (large files), but validators need to know they run `extract` themselves.
