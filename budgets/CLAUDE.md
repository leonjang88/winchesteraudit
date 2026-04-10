# budgets/ — Source Files and Schema Reference

Raw PDF budget documents live here. This file documents the SQLite schema in
`output/budgets.db` and provides example queries for Phase 3 analysis.

---

## Schema

### towns

Static reference data. One row per town.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | Auto |
| name | TEXT UNIQUE | e.g. "Winchester" |
| area_sq_miles | REAL | Used to normalize DPW/road spending |
| school_ranking | TEXT | e.g. "Top 10%" |

### town_metrics

Year-over-year metrics. One row per town per fiscal year.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | Auto |
| town_id | INTEGER | FK → towns.id |
| fiscal_year | INTEGER | e.g. 2026 |
| population | INTEGER | Per capita denominator for public safety, general gov |
| households | INTEGER | Per household denominator for debt service, tax burden |
| median_household_income | INTEGER | Context for tax burden analysis |
| student_enrollment | INTEGER | Per-student denominator for education spending |
| road_miles | REAL | Per-road-mile denominator for road maintenance |
| total_assessed_value | REAL | Total property value; denominator for tax rate analysis |
| tax_rate | REAL | Per $1,000 assessed value |
| total_levy | REAL | Actual property tax revenue raised |
| levy_limit | REAL | Prop 2½ maximum allowed levy |
| new_growth | REAL | New construction adding to levy capacity |

UNIQUE on (town_id, fiscal_year).

### line_items

Raw budget line items extracted from PDFs. Source of truth for all spending data.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | Auto |
| town_id | INTEGER | FK → towns.id |
| fiscal_year | INTEGER | e.g. 2026 |
| department | TEXT | e.g. "Police Department" |
| account_code | TEXT | 5-digit UMAS code; NULL if town doesn't use them |
| description | TEXT | Line item label from PDF |
| amount | REAL | Dollar amount |
| column_type | TEXT | actual\|budget\|request\|manager\|fincom\|recommended |
| row_type | TEXT | line_item\|subtotal\|total (default: line_item) |
| source_file | TEXT | PDF filename for re-extraction |
| source_page | INTEGER | Page number in source PDF |
| category | TEXT | Phase 2: Education, Public Safety, DPW, etc. |
| subcategory | TEXT | Phase 2: Fire, Police, Roads, etc. |
| normalized_description | TEXT | Phase 2: cross-town comparable name |
| expense_type | TEXT | Phase 2: personnel\|supplies\|services\|equipment\|capital\|other |

UNIQUE on (town_id, fiscal_year, department, account_code, description, column_type).

### narratives

Narrative text extracted from department budget sections.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | Auto |
| town_id | INTEGER | FK → towns.id |
| fiscal_year | INTEGER | e.g. 2026 |
| department | TEXT | Department name |
| page_number | INTEGER | Source page |
| content | TEXT | Full text of narrative |
| source_file | TEXT | PDF filename |

### normalization_cache

Caches LLM normalization results to avoid re-calling the API.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | Auto |
| original_text | TEXT | Raw text from PDF |
| field | TEXT | department\|description\|expense_type |
| normalized_value | TEXT | Normalized output |

UNIQUE on (original_text, field).

---

## Normalization Denominators

When comparing spending across towns, normalize by the appropriate denominator:

| Category | Normalize by | Why |
|----------|-------------|-----|
| Education | Per student (enrollment) | Spending tracks student count |
| DPW / Public Works | Per sq mile (area) | Infrastructure scales with geography |
| Road maintenance | Per road mile | Most precise for DPW road spending |
| Public Safety | Per capita | Service demand correlates with population |
| General Government | Per capita | Administrative overhead scales with residents |
| Debt Service | Per household | Debt burden is felt per taxpaying household |
| Tax burden | Per household + per $1,000 assessed value | |

---

## Example Queries

### Total FY2026 spending by department for Winchester

```sql
SELECT department, SUM(amount) AS total
FROM line_items
JOIN towns ON towns.id = line_items.town_id
WHERE towns.name = 'Winchester'
  AND fiscal_year = 2026
  AND column_type = 'budget'
  AND row_type = 'line_item'
GROUP BY department
ORDER BY total DESC;
```

### Per-student education spending across all towns

```sql
SELECT towns.name,
       SUM(li.amount) AS education_total,
       tm.student_enrollment,
       ROUND(SUM(li.amount) / tm.student_enrollment, 2) AS per_student
FROM line_items li
JOIN towns ON towns.id = li.town_id
JOIN town_metrics tm ON tm.town_id = li.town_id AND tm.fiscal_year = li.fiscal_year
WHERE li.category = 'Education'
  AND li.fiscal_year = 2026
  AND li.column_type = 'budget'
  AND li.row_type = 'line_item'
GROUP BY towns.name
ORDER BY per_student DESC;
```

### Per-capita public safety spending comparison

```sql
SELECT towns.name,
       SUM(li.amount) AS safety_total,
       tm.population,
       ROUND(SUM(li.amount) / tm.population, 2) AS per_capita
FROM line_items li
JOIN towns ON towns.id = li.town_id
JOIN town_metrics tm ON tm.town_id = li.town_id AND tm.fiscal_year = li.fiscal_year
WHERE li.category = 'Public Safety'
  AND li.fiscal_year = 2026
  AND li.column_type = 'budget'
  AND li.row_type = 'line_item'
GROUP BY towns.name
ORDER BY per_capita DESC;
```

### Tax burden: levy per household

```sql
SELECT towns.name,
       tm.total_levy,
       tm.households,
       ROUND(tm.total_levy / tm.households, 2) AS levy_per_household
FROM town_metrics tm
JOIN towns ON towns.id = tm.town_id
WHERE tm.fiscal_year = 2026
ORDER BY levy_per_household DESC;
```

### Debt service per household

```sql
SELECT towns.name,
       SUM(li.amount) AS debt_total,
       tm.households,
       ROUND(SUM(li.amount) / tm.households, 2) AS per_household
FROM line_items li
JOIN towns ON towns.id = li.town_id
JOIN town_metrics tm ON tm.town_id = li.town_id AND tm.fiscal_year = li.fiscal_year
WHERE li.category = 'Debt Service'
  AND li.fiscal_year = 2026
  AND li.column_type = 'budget'
  AND li.row_type = 'line_item'
GROUP BY towns.name
ORDER BY per_household DESC;
```
