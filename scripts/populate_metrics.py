"""
Populate town demographics and tax metrics into budgets.db.

Run from project root:
    python scripts/populate_metrics.py

Sources (retrieved 2026-04-10):
    Population & Median HH Income:
        - ACS 2024 5-Year Estimates via massachusetts-demographics.com
        - datausa.io town profiles
    Households:
        - Census 2020 decennial / ACS 2024 via datausa.io, census.gov QuickFacts,
          massachusetts-demographics.com
    Student Enrollment (2025-26):
        - MA DESE profiles.doe.mass.edu/statereport/enrollmentbygrade.aspx
    Area (sq miles):
        - Wikipedia town articles, census.gov QuickFacts
    Tax Rates (FY2026):
        - Town assessor websites, winchesternews.org, westonobserver.org,
          theswellesleyreport.com, needhamobserver.com, belmontonian.com
        - MA DOR: mass.gov/info-details/fy2026-tax-levies-assessed-values-and-tax-rates
    Total Assessed Value / Tax Levy / Levy Limit:
        - Town tax classification hearing reports and news articles
        - Bedford Citizen, Lexington tax classification presentation
    Bedford new growth: thebedfordcitizen.org

Missing / NULL fields:
    - road_miles: not easily available for most towns, left NULL
    - levy_limit / new_growth: only available for some towns
    - total_assessed_value / total_levy: partial coverage
    - Weston households (4,577) from datausa.io — may include group quarters
    - Wellesley & Weston median income capped at $250,001 (Census top-code)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.schema import get_db, ensure_town, init_db

# fmt: off
TOWNS = {
    #  name: (area_sq_miles, school_ranking)
    "winchester":  (6.0,  "A+"),
    "lexington":   (16.5, "A+"),
    "belmont":     (4.7,  "A"),
    "wellesley":   (10.2, "A+"),
    "needham":     (12.6, "A+"),
    "bedford":     (13.7, "A"),
    "weston":      (17.0, "A+"),
    "concord":     (24.9, "A+"),
}

# FY2026 metrics keyed by town name
# Fields: population, households, median_household_income, student_enrollment,
#         road_miles, total_assessed_value, tax_rate, total_levy, levy_limit, new_growth
METRICS_FY2026 = {
    "winchester": {
        "population":              23406,
        "households":              8340,
        "median_household_income": 230198,
        "student_enrollment":      4341,
        "road_miles":              None,
        "total_assessed_value":    None,         # not found in public sources
        "tax_rate":                10.56,
        "total_levy":              None,
        "levy_limit":              None,
        "new_growth":              None,
    },
    "lexington": {
        "population":              34295,
        "households":              13414,        # existing DB value, consistent with ACS
        "median_household_income": 238444,
        "student_enrollment":      6524,
        "road_miles":              None,
        "total_assessed_value":    None,
        "tax_rate":                13.00,
        "total_levy":              None,
        "levy_limit":              265340792,
        "new_growth":              None,
    },
    "belmont": {
        "population":              27175,
        "households":              10358,
        "median_household_income": 183137,
        "student_enrollment":      4433,
        "road_miles":              None,
        "total_assessed_value":    None,
        "tax_rate":                11.51,
        "total_levy":              138849576,
        "levy_limit":              None,
        "new_growth":              None,
    },
    "wellesley": {
        "population":              30347,
        "households":              9170,
        "median_household_income": 250001,       # Census top-coded
        "student_enrollment":      3922,
        "road_miles":              None,
        "total_assessed_value":    18_000_000_000,  # "over $18B" per Swellesley Report
        "tax_rate":                10.17,
        "total_levy":              None,
        "levy_limit":              None,
        "new_growth":              None,
    },
    "needham": {
        "population":              32459,
        "households":              11600,
        "median_household_income": 214308,
        "student_enrollment":      5427,
        "road_miles":              None,
        "total_assessed_value":    16_735_000_000,
        "tax_rate":                10.83,
        "total_levy":              192_400_000,   # FY2026 per Needham Observer
        "levy_limit":              None,
        "new_growth":              None,
    },
    "bedford": {
        "population":              14727,
        "households":              5540,
        "median_household_income": 172400,
        "student_enrollment":      2397,
        "road_miles":              None,
        "total_assessed_value":    5_858_101_445,
        "tax_rate":                12.49,
        "total_levy":              None,
        "levy_limit":              None,
        "new_growth":              None,
    },
    "weston": {
        "population":              11579,
        "households":              4577,
        "median_household_income": 250001,       # Census top-coded
        "student_enrollment":      2079,
        "road_miles":              None,
        "total_assessed_value":    None,
        "tax_rate":                10.88,
        "total_levy":              None,
        "levy_limit":              None,
        "new_growth":              None,
    },
    "concord": {
        "population":              18223,
        "households":              7481,
        "median_household_income": 195350,
        "student_enrollment":      1868,         # Concord district only, not Concord-Carlisle
        "road_miles":              None,
        "total_assessed_value":    None,
        "tax_rate":                13.05,
        "total_levy":              None,
        "levy_limit":              None,
        "new_growth":              None,
    },
}
# fmt: on


def main():
    init_db()

    # Upsert towns (ensure_town manages its own connection)
    town_ids = {}
    for name, (area, ranking) in TOWNS.items():
        tid = ensure_town(name, area_sq_miles=area, school_ranking=ranking)
        town_ids[name] = tid

    # Update area/ranking for pre-existing towns, then insert metrics
    conn = get_db()
    try:
        for name, (area, ranking) in TOWNS.items():
            conn.execute(
                "UPDATE towns SET area_sq_miles = ?, school_ranking = ? WHERE id = ?",
                (area, ranking, town_ids[name]),
            )

        # Upsert FY2026 metrics
        for name, m in METRICS_FY2026.items():
            tid = town_ids[name]
            conn.execute(
                """INSERT INTO town_metrics
                   (town_id, fiscal_year, population, households,
                    median_household_income, student_enrollment, road_miles,
                    total_assessed_value, tax_rate, total_levy, levy_limit, new_growth)
                   VALUES (?, 2026, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(town_id, fiscal_year) DO UPDATE SET
                    population = excluded.population,
                    households = excluded.households,
                    median_household_income = excluded.median_household_income,
                    student_enrollment = excluded.student_enrollment,
                    road_miles = excluded.road_miles,
                    total_assessed_value = excluded.total_assessed_value,
                    tax_rate = excluded.tax_rate,
                    total_levy = excluded.total_levy,
                    levy_limit = excluded.levy_limit,
                    new_growth = excluded.new_growth
                """,
                (
                    tid,
                    m["population"],
                    m["households"],
                    m["median_household_income"],
                    m["student_enrollment"],
                    m["road_miles"],
                    m["total_assessed_value"],
                    m["tax_rate"],
                    m["total_levy"],
                    m["levy_limit"],
                    m["new_growth"],
                ),
            )

        conn.commit()
        print(f"Inserted/updated metrics for {len(METRICS_FY2026)} towns (FY2026)")

        # Verify
        rows = conn.execute(
            """SELECT t.name, m.population, m.households, m.median_household_income,
                      m.student_enrollment, m.tax_rate, m.total_assessed_value
               FROM town_metrics m JOIN towns t ON t.id = m.town_id
               WHERE m.fiscal_year = 2026
               ORDER BY t.name"""
        ).fetchall()
        print(f"\nFY2026 metrics ({len(rows)} towns):")
        print(f"{'Town':<12} {'Pop':>7} {'HH':>6} {'MHI':>8} {'Enroll':>6} {'Rate':>6} {'Assessed':>15}")
        print("-" * 70)
        for r in rows:
            assessed = f"${r[6]/1e9:.1f}B" if r[6] else "N/A"
            print(f"{r[0]:<12} {r[1]:>7,} {r[2]:>6,} ${r[3]:>7,} {r[4]:>6,} ${r[5]:>5.2f} {assessed:>15}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
