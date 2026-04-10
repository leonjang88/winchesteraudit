# Winchester Budget Comparison Pipeline

## Context

Understand Winchester's ~$163M town budget at the line-item level: where the money goes, what's unusual vs comp towns, and surface specific items where spending diverges. Data lives in budget PDFs with different formats per town.

Winchester's format (from Fire Dept, p.69): 5-digit UMAS account codes, 6 FY columns (FY24 Budget/Actual, FY25 Budget, FY26 Request/Manager/FinCom), sections split into Personal Services (51xxx) and Expenses (52xxx-55xxx) with subtotals. Other towns will differ.

**All AI work done via Claude Code sessions** — no API calls, no `anthropic` package. **PDF extraction is the single source of truth** — no DLS state data import.

---

## Three Phases

```
Phase 1: EXTRACT          Phase 2: NORMALIZE           Phase 3: ANALYZE
─────────────────         ──────────────────           ─────────────────
Budget PDFs ──►           Raw data ──►                 Clean data ──►
  Tables + narrative        Comparable line items        Cross-town comparisons
  Every page extracted      (UMAS codes + Claude Code)   Normalized by right metric
  (per-town configs)                                     per category
```

**This plan covers all three phases.**

**Phase 3 note — normalization slices:**

Different categories need different denominators:
- **Per capita** (population) — General Government, Public Safety, Human Services, Debt Service
- **Per student** (enrollment) — Education
- **Per sq mile** (area) — DPW, Public Works, infrastructure
- **Per road mile** — road maintenance specifically
- **Per household** — property tax burden (more relevant than per-capita for tax analysis)
- **% of total budget** — reveals spending priorities (is education 55% or 48%?)
- **Personnel vs non-personnel** — staffing intensity by department
- **Year-over-year growth rate** — which departments are growing fastest
- **Tax rate / assessed value** — how hard is the town taxing relative to wealth

**Data sources for normalization metrics:**
- Population, households, median income → US Census / [DESE profiles](https://profiles.doe.mass.edu)
- Student enrollment → [DESE enrollment reports](https://profiles.doe.mass.edu/statereport/enrollmentbygrade.aspx)
- Area (sq miles) → Census (static)
- Road miles → MassDOT or town DPW reports (may need manual lookup)
- Assessed values, tax rates → available in budget PDFs (typically early pages) or town assessor websites

Town configs capture these metrics so they're ready when Phase 3 starts.

---

## Phase 1: Extraction

### What we extract from every page

Budget PDFs have two kinds of content, and we capture **both**:

- **Table pages** — line-item numbers (Fire Dept: PERMANENT $5.7M, OVERTIME $648K, etc.)
- **Narrative pages** — department descriptions, what changed and why, goals, staffing context
- **Mixed pages** — some text above/below a table

pdfplumber gives us both. The extractor classifies each page and stores everything. Every row records `source_page` and `source_file` so if something looks wrong, you can go straight back to the exact page to re-extract.

### Strategy: per-town configs

Each town's PDF has a different layout:

1. **Profile** — run pdfplumber on sample pages, dump raw output
2. **Configure** — YAML config per town describing its format
3. **Extract** — pdfplumber parses guided by the config, capturing tables + text
4. **Validate** — flag pages where extraction looks wrong
5. **Alert** — if format is too weird, alert you to decide: customize or skip

### Per-town config (`config/towns/winchester.yaml`)

```yaml
name: winchester
area_sq_miles: 6.1                    # static
school_ranking: "A+"
budget_url: "https://www.winchester.us/Archive.aspx?AMID=38"

# Metrics that change year over year (from budget docs, Census, DESE)
metrics:
  2026:
    population: 22970
    households: 8200
    median_household_income: 172000
    student_enrollment: 4600
    road_miles: 85
    total_assessed_value: 7_200_000_000
    tax_rate: 11.48                   # per $1000
    total_levy: 82_600_000
    levy_limit: 83_000_000
    new_growth: 1_200_000
  2025:
    population: 22800
    # ... (fill in as available)

extraction:
  has_account_codes: true
  account_code_position: "first"
  department_header: "page_top_large_font"
  
  columns:
    "FY24 BUDGET": { fiscal_year: 2024, type: budget }
    "FY24 ACTUAL": { fiscal_year: 2024, type: actual }
    "FY25 BUDGET": { fiscal_year: 2025, type: budget }
    "FY26 REQUEST": { fiscal_year: 2026, type: request }
    "FY26 MANAGER": { fiscal_year: 2026, type: manager }
    "FY26 FINCOM": { fiscal_year: 2026, type: fincom }
  
  subtotal_patterns: ["TOTAL", "SUB-TOTAL"]
```

Different format example:

```yaml
name: lexington
extraction:
  has_account_codes: false
  department_header: "bold_row"
  columns:
    "Expended FY24": { fiscal_year: 2024, type: actual }
    "Appropriated FY25": { fiscal_year: 2025, type: budget }
    "Requested FY26": { fiscal_year: 2026, type: request }
    "Recommended FY26": { fiscal_year: 2026, type: recommended }
```

### CLI workflow

```bash
# 1. Profile: inspect what pdfplumber sees
python src/load.py profile --town winchester --pdf pdfs/winchester/fy2026.pdf

# 2. Create/tweak config/towns/winchester.yaml

# 3. Extract: tables + narrative from every page
python src/load.py extract --town winchester --pdf pdfs/winchester/fy2026.pdf

# Output:
#   output/raw/winchester_fy2026.csv           (line items with source_page)
#   output/raw/winchester_fy2026_narrative.txt  (text content by department)
#   output/raw/winchester_fy2026_validation.txt (flagged pages by page number)
```

**Rescue for failed pages via subagent:** When extraction flags failed pages, `load.py rescue` spawns a **Sonnet subagent** per failed page. Sonnet is the right model — these are the visually messy pages that need image understanding, but not Opus-level reasoning. The subagent reads the specific PDF page, extracts the table data as structured rows, and writes them back into the database. Runs automatically after extract, no manual intervention needed.

```bash
# Automatically rescue failed pages after extraction
python src/load.py rescue --town winchester --pdf pdfs/winchester/fy2026.pdf
# → Spawns Sonnet subagent for each page listed in validation report
# → Subagent reads the PDF page, returns structured table data
# → Data inserted into line_items with source_page for traceability
```

---

## Phase 2: Normalization

### The problem

Same expense, different names across towns:

| Winchester | Lexington | Arlington |
|-----------|-----------|-----------|
| `52184 CLOTHING & UNIFORMS` $46K | `Uniform Allowance` $38K | `Protective Clothing` $42K |
| `53172 CONTRACTUAL SERVICE` $110K | `Professional Services` $95K | `Contracted Svcs` $88K |
| `51359 OVERTIME` $648K | `OT & Holiday` $580K | `Overtime Pay` $520K |

### Three normalization layers

**Layer 1 — UMAS account codes (automatic, free)**
MA towns use the [UMAS](https://www.mass.gov/doc/umas-manual/download) standard:
- `51xxx` = Personnel · `52xxx` = Supplies & services · `53xxx` = Other charges · `54xxx` = Capital · `55xxx` = Equipment

When two towns share a code → same expense type, auto-map.

**Layer 2 — Description clustering (Claude Code, one-time)**
`classify.py` dumps unmatched descriptions → `output/unclassified_descriptions.csv`. You ask Claude Code to map them. Results cached in `normalization_cache`.

**Layer 3 — Department mapping (Claude Code, one-time)**
`classify.py` dumps unmapped departments → `output/unclassified_departments.csv`. Claude Code maps them to: Education, Public Safety/Fire, Public Safety/Police, Public Works, General Government, Human Services, Culture & Recreation, Debt Service, Benefits/Insurance, Capital.

### CLI

```bash
python src/load.py classify

# "Auto-mapped 892 items from UMAS codes."
# "355 descriptions need classification → output/unclassified_descriptions.csv"
# "12 departments need classification → output/unclassified_departments.csv"
# → Open Claude Code to classify remaining items
# → Re-run classify to apply cached mappings
```

---

## Phase 3: Analysis & Comparison

### Smart normalization by category

Different categories need different denominators — per-capita is wrong for everything:

| Category | Normalize by | Why |
|----------|-------------|-----|
| Education | Per student (enrollment) | Spending tracks student count, not population |
| DPW / Public Works | Per sq mile (area) | Road maintenance, snow plowing, infrastructure scales with geography |
| Road maintenance | Per road mile | Most precise for DPW road spending |
| Public Safety | Per capita | Service demand correlates with population |
| General Government | Per capita | Administrative overhead scales with residents |
| Human Services | Per capita | |
| Debt Service | Per household | Debt burden is felt per taxpaying household |
| Benefits/Insurance | Per employee (if available) or per capita | |
| Tax burden | Per household + per $1000 assessed value | |

### No `compare.py` — Claude Code queries the database directly

With a well-structured database and good CLAUDE.md documentation, Claude Code writes SQL on the fly — more flexible than pre-built queries. Ask it anything:

- *"How does Winchester's fire overtime compare to comp towns?"*
- *"What does Winchester spend on that other towns don't?"*
- *"Show me education spending per student across all towns"*
- *"Which departments grew fastest year over year?"*
- *"How close is Winchester to its Prop 2½ levy ceiling vs comps?"*

The **CLAUDE.md** documents the schema, the normalization rules, and example queries so Claude Code always knows the right denominator to use.

### Questions this setup answers

1. **"Where does Winchester overspend?"** — per-student education cost, per-capita police cost, per-sqmile DPW cost vs comps
2. **"What are we spending on that others aren't?"** — line items unique to Winchester or at wildly different amounts
3. **"Why is Fire so expensive?"** — line-item breakdown, overtime vs comp average, staffing vs expense split
4. **"How fast are we growing?"** — YoY growth by department, departments growing faster than the levy
5. **"How much fiscal room do we have?"** — levy utilization, new growth trends, proximity to Prop 2½ ceiling
6. **"Is our DPW spend reasonable?"** — per-sq-mile strips out big-town-vs-small-town noise

---

## Project Structure

```
budgets/
├── src/
│   ├── __init__.py
│   ├── schema.py                 # SQLite schema + helpers
│   ├── extract.py                # Profile + extract tables + narrative
│   ├── classify.py               # UMAS auto-map + dump unclassified
│   ├── load.py                   # CLI entry point
│   └── (no compare.py — Claude Code queries DB directly)
├── pdfs/{town}/                  # Budget PDFs (gitignored)
├── output/
│   ├── budgets.db
│   ├── raw/                      # CSVs + narratives + validation reports
│   └── compared/                 # Phase 3 output
├── config/
│   └── towns/{town}.yaml         # Per-town configs
├── requirements.txt
└── CLAUDE.md
```

## SQLite Schema

```sql
CREATE TABLE towns (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    area_sq_miles REAL,              -- static, doesn't change
    school_ranking TEXT
);

-- Metrics that change year over year
CREATE TABLE town_metrics (
    id INTEGER PRIMARY KEY,
    town_id INTEGER REFERENCES towns(id),
    fiscal_year INTEGER NOT NULL,
    population INTEGER,
    households INTEGER,
    median_household_income INTEGER,
    student_enrollment INTEGER,
    road_miles REAL,
    total_assessed_value REAL,       -- changes as properties are reassessed
    tax_rate REAL,                   -- adjusts inversely with assessed value
    total_levy REAL,                 -- actual property tax revenue raised
    levy_limit REAL,                 -- Prop 2½ max allowed
    new_growth REAL,                 -- new construction adding to levy capacity
    UNIQUE(town_id, fiscal_year)
);

-- Line items from PDF extraction (source of truth)
CREATE TABLE line_items (
    id INTEGER PRIMARY KEY,
    town_id INTEGER REFERENCES towns(id),
    fiscal_year INTEGER NOT NULL,
    department TEXT NOT NULL,
    account_code TEXT,               -- 5-digit UMAS (null if town doesn't use them)
    description TEXT NOT NULL,
    amount REAL NOT NULL,
    column_type TEXT,                -- actual|budget|request|manager|fincom|recommended
    row_type TEXT DEFAULT 'line_item',  -- line_item|subtotal|total
    source_file TEXT,
    source_page INTEGER,             -- for easy re-extraction
    -- Normalization (Phase 2)
    category TEXT,                   -- Education, Public Safety, etc.
    subcategory TEXT,                -- Fire, Police, etc.
    normalized_description TEXT,     -- cross-town comparable name
    expense_type TEXT,               -- personnel|supplies|services|equipment|capital|other
    UNIQUE(town_id, fiscal_year, department, account_code, description, column_type)
);

-- Narrative text by department
CREATE TABLE narratives (
    id INTEGER PRIMARY KEY,
    town_id INTEGER REFERENCES towns(id),
    fiscal_year INTEGER NOT NULL,
    department TEXT NOT NULL,
    page_number INTEGER,
    content TEXT NOT NULL,
    source_file TEXT
);

-- Normalization cache
CREATE TABLE normalization_cache (
    id INTEGER PRIMARY KEY,
    original_text TEXT NOT NULL,
    field TEXT NOT NULL,             -- department|description|expense_type
    normalized_value TEXT NOT NULL,
    UNIQUE(original_text, field)
);
```

## Comparable Towns

| Town | Why |
|------|-----|
| **Winchester** | Target |
| **Lexington** | Similar wealth, top schools, nearby |
| **Arlington** | Adjacent, similar size, good schools |
| **Belmont** | Adjacent, comparable demographics |
| **Wellesley** | Top MA schools, similar income |
| **Needham** | Strong schools, comparable community |
| **Concord** | Top-tier schools, similar character |
| **Wayland** | Strong schools, comparable suburb |
| **Weston** | Affluent, top schools, nearby |
| **Bedford** | Nearby, good schools, similar character |

## Dependencies

```
pdfplumber>=0.11
openpyxl>=3.1
pyyaml>=6.0
```

## Files to Create

| # | File | Purpose |
|---|------|---------|
| 1 | `budgets/requirements.txt` | pdfplumber, openpyxl, pyyaml |
| 2 | `budgets/src/__init__.py` | Package marker |
| 3 | `budgets/src/schema.py` | SQLite schema, get_db(), init_db(), ensure_town() |
| 4 | `budgets/src/extract.py` | Profile + extract tables + narrative per page |
| 5 | `budgets/src/classify.py` | UMAS auto-map, dump unclassified for Claude Code |
| 6 | `budgets/src/load.py` | CLI: profile, extract, classify, rescue |
| 7 | `budgets/config/towns/winchester.yaml` | Winchester config |
| 8 | `budgets/CLAUDE.md` | Schema docs, normalization rules, example queries for Claude Code |
| 9 | Root `.gitignore` update | budgets/pdfs/, output/ |

## Verification

1. Profile shows detected columns matching Winchester's 6-column format
2. Extract produces CSV with Fire Dept PERMANENT ~$5.7M, OVERTIME ~$648K, each row tagged with source_page
3. Narrative file captures department descriptions from text-only pages
4. Validation report flags problem pages by page number for easy re-extraction
5. Classify auto-maps UMAS codes, dumps remainder for Claude Code

## Your Workflow

1. `pip install -r requirements.txt`
2. Drop Winchester PDFs into `pdfs/winchester/`
3. Profile → config → extract → eyeball CSV + narrative for Winchester
4. Repeat per comp town: profile, config, extract, validate
5. Towns with weird formats → decide: invest time or skip
6. Classify → Claude Code handles unclassified items
7. Run comparisons: Claude Code queries DB directly, drill into departments, surface unique items
