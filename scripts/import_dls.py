"""Import DLS Community Comparison Excel files into budgets.db.

Creates dls_* tables with foreign keys to the towns table.
Idempotent — safe to re-run.

Usage: python3 scripts/import_dls.py
"""

import os
import sys
import sqlite3
import openpyxl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from schema import get_db


def ensure_town_inline(conn: sqlite3.Connection, name: str) -> int:
    """Insert or get town id using the caller's connection."""
    conn.execute("INSERT OR IGNORE INTO towns (name) VALUES (?)", (name,))
    return conn.execute("SELECT id FROM towns WHERE name = ?", (name,)).fetchone()[0]

DLS_DIR = os.path.join(os.path.dirname(__file__), '..', 'budgets', 'DLS')

TARGETS = [
    'Winchester', 'Lexington', 'Belmont', 'Wellesley',
    'Needham', 'Bedford', 'Weston', 'Concord',
    'Arlington',  # include for reference even if no FY27 PDF yet
]


def create_dls_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dls_spending (
            id INTEGER PRIMARY KEY,
            town_id INTEGER REFERENCES towns(id),
            dor_code TEXT,
            general_government REAL,
            police REAL,
            fire REAL,
            other_public_safety REAL,
            education REAL,
            public_works REAL,
            human_services REAL,
            culture_recreation REAL,
            fixed_costs REAL,
            intergovernment REAL,
            other_expenses REAL,
            debt_service REAL,
            UNIQUE(town_id)
        );

        CREATE TABLE IF NOT EXISTS dls_levies (
            id INTEGER PRIMARY KEY,
            town_id INTEGER REFERENCES towns(id),
            dor_code TEXT,
            residential_tax_rate REAL,
            open_space_tax_rate REAL,
            commercial_tax_rate REAL,
            industrial_tax_rate REAL,
            personal_property_tax_rate REAL,
            residential_levy REAL,
            open_space_levy REAL,
            commercial_levy REAL,
            industrial_levy REAL,
            personal_prop_levy REAL,
            total_tax_levy REAL,
            residential_pct_of_levy REAL,
            cip_pct_of_levy REAL,
            UNIQUE(town_id)
        );

        CREATE TABLE IF NOT EXISTS dls_assessed_values (
            id INTEGER PRIMARY KEY,
            town_id INTEGER REFERENCES towns(id),
            dor_code TEXT,
            residential REAL,
            open_space REAL,
            commercial REAL,
            industrial REAL,
            personal_property REAL,
            total_assessed_value REAL,
            residential_pct REAL,
            cip_pct REAL,
            UNIQUE(town_id)
        );

        CREATE TABLE IF NOT EXISTS dls_prop25 (
            id INTEGER PRIMARY KEY,
            town_id INTEGER REFERENCES towns(id),
            dor_code TEXT,
            new_growth REAL,
            override REAL,
            debt_excluded REAL,
            max_levy_limit REAL,
            excess_levy_capacity REAL,
            levy_ceiling REAL,
            override_capacity REAL,
            UNIQUE(town_id)
        );

        CREATE TABLE IF NOT EXISTS dls_general (
            id INTEGER PRIMARY KEY,
            town_id INTEGER REFERENCES towns(id),
            dor_code TEXT,
            form_of_government TEXT,
            school_structure TEXT,
            population_2023 INTEGER,
            single_family_tax_bill REAL,
            income_per_capita REAL,
            eqv_per_capita REAL,
            land_area REAL,
            population_density REAL,
            road_miles REAL,
            UNIQUE(town_id)
        );

        CREATE TABLE IF NOT EXISTS dls_financial_indicators (
            id INTEGER PRIMARY KEY,
            town_id INTEGER REFERENCES towns(id),
            dor_code TEXT,
            gf_debt_service REAL,
            gf_debt_service_pct REAL,
            free_cash REAL,
            stabilization_fund REAL,
            moodys_rating TEXT,
            sp_rating TEXT,
            UNIQUE(town_id)
        );
    """)


def read_xlsx(filename: str) -> list[dict]:
    """Read Excel file, return list of dicts keyed by header."""
    path = os.path.join(DLS_DIR, filename)
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    # Find the header row (first row with 'Municipality' or 'DOR Code')
    header_idx = 0
    for i, row in enumerate(rows):
        if row and any(str(v or '').strip() == 'Municipality' for v in row):
            header_idx = i
            break

    headers = [str(h or '').strip() for h in rows[header_idx]]
    result = []
    for row in rows[header_idx + 1:]:
        d = {}
        for h, v in zip(headers, row):
            d[h] = v
        if d.get('Municipality') in TARGETS:
            result.append(d)
    return result


def n(val):
    """Coerce to float or None."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def import_spending(conn):
    rows = read_xlsx('CC_GF_Spend_by_Fun.xlsx')
    for r in rows:
        town_id = ensure_town_inline(conn, r['Municipality'].lower())
        conn.execute("""
            INSERT INTO dls_spending (town_id, dor_code, general_government, police, fire,
                other_public_safety, education, public_works, human_services,
                culture_recreation, fixed_costs, intergovernment, other_expenses, debt_service)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(town_id) DO UPDATE SET
                general_government=excluded.general_government, police=excluded.police,
                fire=excluded.fire, other_public_safety=excluded.other_public_safety,
                education=excluded.education, public_works=excluded.public_works,
                human_services=excluded.human_services, culture_recreation=excluded.culture_recreation,
                fixed_costs=excluded.fixed_costs, intergovernment=excluded.intergovernment,
                other_expenses=excluded.other_expenses, debt_service=excluded.debt_service
        """, (town_id, r.get('DOR Code'), n(r.get('General Government')), n(r.get('Police')),
              n(r.get('Fire')), n(r.get('Other Public Safety')), n(r.get('Education')),
              n(r.get('Public Works')), n(r.get('Human Services')),
              n(r.get('Culture and Recreation')), n(r.get('Fixed Costs')),
              n(r.get('Intergovernment')), n(r.get('Other Expenses')), n(r.get('Debt Service'))))
    print(f"  dls_spending: {len(rows)} towns")


def import_levies(conn):
    rows = read_xlsx('CC_Levies_and_Tax_by_Class.xlsx')
    for r in rows:
        town_id = ensure_town_inline(conn, r['Municipality'].lower())
        conn.execute("""
            INSERT INTO dls_levies (town_id, dor_code, residential_tax_rate, open_space_tax_rate,
                commercial_tax_rate, industrial_tax_rate, personal_property_tax_rate,
                residential_levy, open_space_levy, commercial_levy, industrial_levy,
                personal_prop_levy, total_tax_levy, residential_pct_of_levy, cip_pct_of_levy)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(town_id) DO UPDATE SET
                residential_tax_rate=excluded.residential_tax_rate,
                commercial_tax_rate=excluded.commercial_tax_rate,
                total_tax_levy=excluded.total_tax_levy
        """, (town_id, r.get('DOR Code'), n(r.get('Residential Tax Rate')),
              n(r.get('Open Space Tax Rate')), n(r.get('Commercial Tax Rate')),
              n(r.get('Industrial Tax Rate')), n(r.get('Personal Property Tax Rate')),
              n(r.get('Residential Levy')), n(r.get('Open Space Levy')),
              n(r.get('Commercial Levy')), n(r.get('Industrial Levy')),
              n(r.get('Personal Prop Levy')), n(r.get('Total Tax Levy')),
              n(r.get('R/O % of Total Levy')), n(r.get('CIP as % of Total Levy'))))
    print(f"  dls_levies: {len(rows)} towns")


def import_assessed_values(conn):
    rows = read_xlsx('CC_Assessed_Value_by_Class.xlsx')
    for r in rows:
        town_id = ensure_town_inline(conn, r['Municipality'].lower())
        conn.execute("""
            INSERT INTO dls_assessed_values (town_id, dor_code, residential, open_space,
                commercial, industrial, personal_property, total_assessed_value,
                residential_pct, cip_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(town_id) DO UPDATE SET
                total_assessed_value=excluded.total_assessed_value,
                residential=excluded.residential
        """, (town_id, r.get('DOR Code'), n(r.get('Assessed Value Residential')),
              n(r.get('Assessed Value Open Space')), n(r.get('Assessed Value Commercial')),
              n(r.get('Assessed Value Industrial')), n(r.get('Assessed Value Pers Prop')),
              n(r.get('Total Assessed Value')), n(r.get('R/O % of Total Value')),
              n(r.get('CIP % of Total Value'))))
    print(f"  dls_assessed_values: {len(rows)} towns")


def import_prop25(conn):
    rows = read_xlsx('CC_Prop2_5_Levy_Capacity.xlsx')
    for r in rows:
        town_id = ensure_town_inline(conn, r['Municipality'].lower())
        conn.execute("""
            INSERT INTO dls_prop25 (town_id, dor_code, new_growth, override, debt_excluded,
                max_levy_limit, excess_levy_capacity, levy_ceiling, override_capacity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(town_id) DO UPDATE SET
                new_growth=excluded.new_growth, max_levy_limit=excluded.max_levy_limit,
                excess_levy_capacity=excluded.excess_levy_capacity,
                levy_ceiling=excluded.levy_ceiling, override_capacity=excluded.override_capacity
        """, (town_id, r.get('DOR Code'), n(r.get('Total New Growth Applied to Levy Limit')),
              n(r.get('Override')), n(r.get('Debt Excluded on the DE-1')),
              n(r.get('Maximum Levy Limit')), n(r.get('Excess Levy Capacity')),
              n(r.get('Levy Ceiling')), n(r.get('Override Capacity'))))
    print(f"  dls_prop25: {len(rows)} towns")


def import_general(conn):
    rows = read_xlsx('CommunityComparisonGeneral.xlsx')
    for r in rows:
        town_id = ensure_town_inline(conn, r['Municipality'].lower())
        conn.execute("""
            INSERT INTO dls_general (town_id, dor_code, form_of_government, school_structure,
                population_2023, single_family_tax_bill, income_per_capita, eqv_per_capita,
                land_area, population_density, road_miles)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(town_id) DO UPDATE SET
                population_2023=excluded.population_2023,
                single_family_tax_bill=excluded.single_family_tax_bill,
                road_miles=excluded.road_miles
        """, (town_id, r.get('DOR Code'), r.get('Form of Government'), r.get('School Structure'),
              n(r.get('2023 Population')), n(r.get('FY 2025 Single Family Tax Bill')),
              n(r.get('2022 DOR Income Per Capita')), n(r.get('2024 EQV Per Capita')),
              n(r.get('Land Area')), n(r.get('Population Density')),
              n(r.get('2018 Total Road Miles'))))
    print(f"  dls_general: {len(rows)} towns")


def import_financial_indicators(conn):
    rows = read_xlsx('Other_Finacial_Indicators.xlsx')
    for r in rows:
        town_id = ensure_town_inline(conn, r['Municipality'].lower())
        conn.execute("""
            INSERT INTO dls_financial_indicators (town_id, dor_code, gf_debt_service,
                gf_debt_service_pct, free_cash, stabilization_fund, moodys_rating, sp_rating)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(town_id) DO UPDATE SET
                free_cash=excluded.free_cash, stabilization_fund=excluded.stabilization_fund,
                moodys_rating=excluded.moodys_rating, sp_rating=excluded.sp_rating
        """, (town_id, r.get('DOR Code'), n(r.get('FY 2024 General Fund Debt Service')),
              n(r.get('FY 2024 GF Debt Serv % of Budget')),
              n(r.get('Free Cash Amount as of 7/1/2024')),
              n(r.get('FY 2024 Stabilization Fund')),
              r.get('Moodys Bond Rating'), r.get('S&P Bond Rating')))
    print(f"  dls_financial_indicators: {len(rows)} towns")


def main():
    conn = get_db()
    print("Creating DLS tables...")
    create_dls_tables(conn)

    print("Importing DLS data...")
    import_spending(conn)
    import_levies(conn)
    import_assessed_values(conn)
    import_prop25(conn)
    import_general(conn)
    import_financial_indicators(conn)

    conn.commit()
    conn.close()
    print("\nDone. All DLS data imported.")


if __name__ == '__main__':
    main()
