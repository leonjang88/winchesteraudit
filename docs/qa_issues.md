# QA Issues Log

Issues found during deep audits, logged for batch-fix by Cliff.

---

## Belmont FY2027 (commit e42b15c)

**Audit verdict:** FAIL (cycle 1) — posted to GitHub issue #12
**DB state:** 1,554 rows, 100 pages, validate exit 0

### 1. Massive FTE/Staffing Contamination (900 rows, 58% of total)

26 pages contain staffing headcount tables stored as financial data. **0 rows tagged as staffing** — Winchester's tagging heuristic doesn't fire for Belmont's format.

Key signals for detection: numeric descriptions (headcount values), position-title account codes ("Police Officer", "Total", "Subtotal"), amounts < 100 with no ORG+OBJ code.

Examples:
- p105: desc='24', amt=24.0, acct='Police Officer' (24 officers)
- p106: desc='72.38', amt=70.38, acct='Total' (total FTE)
- p117: desc='44', amt=26.0, acct='Total Enrollment' (student enrollment)
- p86: desc='8.0', amt=39.0, acct='Total' (Shared Services FTE)
- p39: desc='4', amt=4.0, acct='Total' (General Government headcount)

Affected pages: 39, 43, 48, 53, 59, 67, 72, 86, 97, 105, 106, 117, 123, 130, 133, 136, 141, 147, 151, 158, 165, 171, 181, 182, 191, 197.

### 2. Reversed Text in account_code (84 rows)

OCR or text-direction bug — "Compensation" extracted as "noitasnepmoC", "Expenses" as "sesnepxE", "Comp." as ".pmoC".

Examples:
- p109: desc='12131', amt=$3,128,809, acct='noitasnepmoC' (should be 'Compensation')
- p188: desc='16142', amt=$101,490, acct='sesnepxE' (should be 'Expenses')
- p108: desc='12111', amt=$447,246, acct='.pmoC' (should be 'Comp.')
- p110: desc='12132', amt=$31,385, acct='noitasnepmoC\nsesnepxE' (reversed multiline)

Affects ~84 rows across Public Safety, Human Services, and Public Services detail pages.

### 3. Descriptions Store OBJ Codes, Not Text Names (1,313 rows, 84%)

The description field contains bare ORG+OBJ account codes ('11411', '14271', '12131') instead of human-readable budget line names ("Assessor Compensation", "DPW Personal Services"). The ACCOUNT DESCRIPTION column text from the PDF is not captured.

Example: p40 desc='11411', amt=$299,787 — PDF shows "11411 Town Manager Compensation" but only the code is stored.

### 4. Garbage Department Names (5 entries, 104 rows)

Chart titles and section headers extracted as department names:

| Department Name | Rows | What It Is |
|---|---|---|
| Historical New Growth Levels by Property Type ($ millions) | 34 | Chart title |
| TABLE 6 | 29 | Table header |
| Debt Service - Principal and Interest | 25 | Section header |
| Program Summary | 11 | Section header |
| Revenue Summary | 5 | Section header |

### 5. Negative Amount Mismatches (pages 174-175)

9 negative rows on pages 174-175 (Public Services enterprise funds). Revenue offsets are structurally real (standard budget practice for departments listing generated revenue). However, **DB amounts don't match PDF values:**

- DB: -$337K to -$499K on pages 174-175
- PDF: shows -$25K and -$5K on those pages

The extraction may be aggregating multiple accounts, reading a different table section, or pulling from a different fiscal year column. Needs investigation.

Details:
- p174: acct='21586015', desc='438010', 4 rows from -$337K to -$382K
- p175: acct='21586025', desc='438010', 4 rows from -$428K to -$499K
- p128: acct='', desc='14111', amt=-$1,764 (isolated)

### 6. FTE Values Incorrect Even as Headcounts (addendum)

Subagent verified p106 (Police FTE table): PDF shows FY2027 Total = **68.38 FTE**, but DB stores **70.38**. Even the contaminated staffing data has wrong values — column misalignment on staffing tables.

### 7. Page-Department Cross-Reference Errors (addendum)

Subagent found p66 is Town Clerk narrative (General Government), not Fire Department. The audit universe has correct department assignment ("Program: General Government") but the page content is narrative goals/accomplishments with no financial data — the 30 rows extracted from this page may be from adjacent table bleed rather than actual p66 content.
