"""
Generates the sample finance data the harness runs against:

  sample_data/finops.duckdb   — ERP: chart of accounts, GL entries across
                                Q1/Q2, budget, vendors, and a deliberately
                                large ap_invoices table (~150K rows) so the
                                expensive-query approval flow has something
                                real to gate.
  sample_data/bank_feed.duckdb — bank transactions + balances (a second
                                datasource, so multi-source routing is
                                exercised).
  sample_data/datasources.json — default datasource registry config.

Deterministic (seeded RNG) so tests can assert against stable totals.
The quarterly account totals intentionally land near the numbers shown in
the FinAgent design mock (revenue 2.34M → 2.18M, software +67%, …).

Run:  python -m db.seed
"""
import datetime as dt
import json
import os
import random

import duckdb

SAMPLE_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "sample_data"))

ACCOUNTS = [
    ("4100", "Revenue", "revenue"),
    ("4200", "Other Income", "revenue"),
    ("5100", "COGS", "expense"),
    ("6100", "Payroll", "expense"),
    ("6200", "Rent", "expense"),
    ("6300", "Software", "expense"),
    ("6400", "Travel", "expense"),
    ("6500", "Digital Advertising", "expense"),
    ("6600", "Professional Services", "expense"),
    ("6700", "Insurance", "expense"),
    ("6800", "Utilities", "expense"),
    ("6900", "Office Supplies", "expense"),
]

# (account_code, q1_total, q2_total) — GL entries are generated to sum to these.
QUARTER_TOTALS = {
    "4100": (2_340_000, 2_180_000),
    "4200": (42_000, 39_000),
    "5100": (1_420_000, 1_510_000),
    "6100": (480_000, 492_000),
    "6200": (120_000, 120_000),
    "6300": (85_000, 142_000),
    "6400": (34_000, 28_000),
    "6500": (96_000, 88_000),
    "6600": (54_000, 61_000),
    "6700": (18_000, 18_000),
    "6800": (12_500, 13_100),
    "6900": (7_400, 6_900),
}

VENDORS_BY_ACCOUNT = {
    "4100": ["Acme Corp", "Globex", "Initech", "Umbrella Ltd", "Stark Industries", "Wayne Enterprises"],
    "4200": ["Interest Income", "FX Gains"],
    "5100": ["RawMat Supply Co", "Pacific Components", "Shenzhen Parts", "Nordic Steel"],
    "6100": ["Payroll Run"],
    "6200": ["Downtown Properties"],
    "6300": ["Snowdrift Cloud", "Salesforce", "Atlassian", "Figma", "Datadog", "GitHub"],
    "6400": ["United Airlines", "Marriott", "Uber for Business"],
    "6500": ["Google Ads", "Meta Ads", "LinkedIn Ads"],
    "6600": ["KPMG", "Baker McKenzie", "Recruiting Partners"],
    "6700": ["Chubb Insurance"],
    "6800": ["City Power & Light", "Metro Water"],
    "6900": ["Staples", "Amazon Business"],
}

Q1 = (dt.date(2026, 1, 1), dt.date(2026, 3, 31))
Q2 = (dt.date(2026, 4, 1), dt.date(2026, 6, 30))

AP_INVOICE_COUNT = 150_000


def _spread(rng: random.Random, total: float, count: int) -> list[float]:
    """Split `total` into `count` positive amounts that sum to ~total."""
    weights = [rng.uniform(0.5, 1.5) for _ in range(count)]
    scale = total / sum(weights)
    amounts = [round(w * scale, 2) for w in weights]
    amounts[-1] = round(amounts[-1] + (total - sum(amounts)), 2)
    return amounts


def _random_date(rng: random.Random, start: dt.date, end: dt.date) -> dt.date:
    return start + dt.timedelta(days=rng.randint(0, (end - start).days))


def seed_erp(path: str):
    rng = random.Random(42)
    if os.path.exists(path):
        os.remove(path)
    con = duckdb.connect(path)

    con.execute("""
        CREATE TABLE accounts (
            account_code VARCHAR PRIMARY KEY,
            account_name VARCHAR,
            account_type VARCHAR
        )
    """)
    con.executemany("INSERT INTO accounts VALUES (?, ?, ?)", ACCOUNTS)

    con.execute("""
        CREATE TABLE gl_entries (
            entry_id INTEGER,
            entry_date DATE,
            account_code VARCHAR,
            account_name VARCHAR,
            quarter VARCHAR,
            amount DOUBLE,
            vendor VARCHAR,
            memo VARCHAR
        )
    """)
    names = {code: name for code, name, _ in ACCOUNTS}
    entry_id = 0
    gl_rows = []
    for code, (q1_total, q2_total) in QUARTER_TOTALS.items():
        for quarter, (start, end), total in (("Q1", Q1, q1_total), ("Q2", Q2, q2_total)):
            count = max(12, int(total / 3_000))
            for amount in _spread(rng, total, count):
                entry_id += 1
                vendor = rng.choice(VENDORS_BY_ACCOUNT[code])
                gl_rows.append((
                    entry_id, _random_date(rng, start, end), code, names[code],
                    quarter, amount, vendor, f"{names[code]} — {vendor}",
                ))
    con.executemany("INSERT INTO gl_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?)", gl_rows)

    con.execute("""
        CREATE TABLE budget (account_code VARCHAR, quarter VARCHAR, amount DOUBLE)
    """)
    budget_rows = []
    for code, (q1_total, q2_total) in QUARTER_TOTALS.items():
        budget_rows.append((code, "Q1", round(q1_total * rng.uniform(0.95, 1.08), 2)))
        budget_rows.append((code, "Q2", round(q1_total * rng.uniform(0.98, 1.05), 2)))
    con.executemany("INSERT INTO budget VALUES (?, ?, ?)", budget_rows)

    con.execute("""
        CREATE TABLE vendors (
            vendor_id INTEGER, vendor_name VARCHAR, category VARCHAR
        )
    """)
    vendor_rows, vid = [], 0
    for code, vendor_names in VENDORS_BY_ACCOUNT.items():
        for v in vendor_names:
            vid += 1
            vendor_rows.append((vid, v, names[code]))
    con.executemany("INSERT INTO vendors VALUES (?, ?, ?)", vendor_rows)

    # The big one — makes SELECT * expensive enough to trip approval gating.
    con.execute("""
        CREATE TABLE ap_invoices (
            invoice_id INTEGER,
            vendor_name VARCHAR,
            invoice_date DATE,
            due_date DATE,
            amount DOUBLE,
            status VARCHAR,
            age_days INTEGER
        )
    """)
    # Bulk-generate in SQL — executemany at this volume is far too slow.
    all_vendors = [v for names_ in VENDORS_BY_ACCOUNT.values() for v in names_]
    vendor_list = ", ".join("'" + v.replace("'", "''") + "'" for v in all_vendors)
    con.execute("SELECT setseed(0.42)")
    con.execute(f"""
        INSERT INTO ap_invoices
        SELECT
            i AS invoice_id,
            list_extract([{vendor_list}], 1 + CAST(floor(random() * {len(all_vendors)}) AS INTEGER)) AS vendor_name,
            invoice_date,
            due_date,
            round(40 + random() * 17960, 2) AS amount,
            CASE
                WHEN random() < 0.72 THEN 'paid'
                WHEN due_date < DATE '2026-07-20' THEN 'overdue'
                ELSE 'open'
            END AS status,
            greatest(datediff('day', due_date, DATE '2026-07-20'), 0) AS age_days
        FROM (
            SELECT
                i,
                invoice_date,
                invoice_date
                    + list_extract([15, 30, 45, 60], 1 + CAST(floor(random() * 4) AS INTEGER)) AS due_date
            FROM (
                SELECT i, DATE '2025-07-01' + CAST(floor(random() * 384) AS INTEGER) AS invoice_date
                FROM range(1, {AP_INVOICE_COUNT + 1}) t(i)
            )
        )
    """)

    con.close()


def seed_bank(path: str):
    rng = random.Random(7)
    if os.path.exists(path):
        os.remove(path)
    con = duckdb.connect(path)

    con.execute("""
        CREATE TABLE bank_balances (
            account_name VARCHAR, currency VARCHAR, balance DOUBLE, as_of TIMESTAMP
        )
    """)
    now = dt.datetime(2026, 7, 20, 6, 0, 0)
    con.executemany("INSERT INTO bank_balances VALUES (?, ?, ?, ?)", [
        ("Operating — First National", "GBP", 1_284_412.55, now),
        ("Payroll — First National", "GBP", 402_118.20, now),
        ("Reserve — Barclays", "GBP", 2_750_000.00, now),
    ])

    con.execute("""
        CREATE TABLE bank_transactions (
            txn_id INTEGER, account_name VARCHAR, txn_date DATE,
            description VARCHAR, amount DOUBLE, balance_after DOUBLE
        )
    """)
    accounts = ["Operating — First National", "Payroll — First National", "Reserve — Barclays"]
    rows = []
    balance = 1_500_000.0
    for i in range(1, 2_001):
        amount = round(rng.uniform(-40_000, 35_000), 2)
        balance = round(balance + amount, 2)
        rows.append((
            i, rng.choice(accounts),
            _random_date(rng, dt.date(2026, 1, 1), dt.date(2026, 7, 19)),
            rng.choice(["ACH payment", "Wire transfer", "Card settlement",
                        "Payroll run", "Vendor payment", "Customer receipt"]),
            amount, balance,
        ))
    con.executemany("INSERT INTO bank_transactions VALUES (?, ?, ?, ?, ?, ?)", rows)
    con.close()


def write_datasources_config(dir_path: str):
    config = {
        "default": "finops_erp",
        "sources": [
            {"name": "finops_erp", "kind": "duckdb", "icon": "🦆",
             "params": {"path": "finops.duckdb"}},
            {"name": "bank_feed", "kind": "duckdb", "icon": "🏦",
             "params": {"path": "bank_feed.duckdb"}},
        ],
    }
    # Optional live sources from env — added when their DSNs are provided.
    if os.environ.get("SEED_POSTGRES_DSN"):
        config["sources"].append({
            "name": "app_db", "kind": "postgres", "icon": "🐘",
            "params": {"dsn": os.environ["SEED_POSTGRES_DSN"]},
        })
    if os.environ.get("SEED_CLICKHOUSE_HOST"):
        config["sources"].append({
            "name": "warehouse", "kind": "clickhouse", "icon": "⚡",
            "params": {"host": os.environ["SEED_CLICKHOUSE_HOST"],
                       "port": int(os.environ.get("SEED_CLICKHOUSE_PORT", "8123")),
                       "database": os.environ.get("SEED_CLICKHOUSE_DB", "default")},
        })
    if os.environ.get("SEED_TRINO_HOST"):
        config["sources"].append({
            "name": "lakehouse", "kind": "trino", "icon": "🚄",
            "params": {"host": os.environ["SEED_TRINO_HOST"],
                       "port": int(os.environ.get("SEED_TRINO_PORT", "8080")),
                       "catalog": os.environ.get("SEED_TRINO_CATALOG", "hive"),
                       "schema": os.environ.get("SEED_TRINO_SCHEMA", "default")},
        })
    with open(os.path.join(dir_path, "datasources.json"), "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def main():
    os.makedirs(SAMPLE_DIR, exist_ok=True)
    erp = os.path.join(SAMPLE_DIR, "finops.duckdb")
    bank = os.path.join(SAMPLE_DIR, "bank_feed.duckdb")
    seed_erp(erp)
    seed_bank(bank)
    write_datasources_config(SAMPLE_DIR)

    con = duckdb.connect(erp, read_only=True)
    gl = con.execute("SELECT COUNT(*) FROM gl_entries").fetchone()[0]
    ap = con.execute("SELECT COUNT(*) FROM ap_invoices").fetchone()[0]
    con.close()
    print(f"Seeded {erp}: {gl:,} gl_entries, {ap:,} ap_invoices")
    print(f"Seeded {bank}: bank_balances + bank_transactions")
    print(f"Wrote {os.path.join(SAMPLE_DIR, 'datasources.json')}")


if __name__ == "__main__":
    main()
