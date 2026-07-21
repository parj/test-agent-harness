"""
One-off ingestion of the Kaggle "banking_dataset_kaggle" archive into
Postgres, loaded into its own `banking` schema so it can be queried as a
distinct datasource (see datasources/registry.py) alongside the ERP and
bank-feed DuckDB sources.

Source: the archive's bundled SQLite db (data/database/bank_sqlite.db)
rather than the CSVs or the insert scripts — it's the only complete copy.
The CSV export is missing transactions.csv entirely and branches.csv is
missing the city/country columns.

Idempotent: rerunning truncates and reloads all seven tables.

Run:  python -m db.load_banking_dataset [archive dir or path to .db file]
      (defaults to ~/Downloads/archive)
"""
import glob
import os
import sqlite3
import sys

import psycopg

from config import settings

DDL = """
CREATE SCHEMA IF NOT EXISTS banking;

CREATE TABLE IF NOT EXISTS banking.customers (
    customer_id VARCHAR(20) PRIMARY KEY,
    first_name VARCHAR(50),
    last_name VARCHAR(50),
    email VARCHAR(100),
    city VARCHAR(50),
    credit_score INT,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS banking.merchants (
    merchant_id VARCHAR(20) PRIMARY KEY,
    merchant_name VARCHAR(100),
    city VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS banking.branches (
    branch_id VARCHAR(20) PRIMARY KEY,
    branch_name VARCHAR(100),
    manager_name VARCHAR(100),
    city VARCHAR(50),
    country VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS banking.accounts (
    account_id VARCHAR(20) PRIMARY KEY,
    customer_id VARCHAR(20) REFERENCES banking.customers(customer_id),
    account_type VARCHAR(20),
    balance_usd NUMERIC(12,2),
    open_date DATE
);
CREATE INDEX IF NOT EXISTS accounts_customer_idx ON banking.accounts (customer_id);

CREATE TABLE IF NOT EXISTS banking.cards (
    card_id VARCHAR(20) PRIMARY KEY,
    account_id VARCHAR(20) REFERENCES banking.accounts(account_id),
    card_type VARCHAR(20),
    expiration_date DATE
);
CREATE INDEX IF NOT EXISTS cards_account_idx ON banking.cards (account_id);

CREATE TABLE IF NOT EXISTS banking.loans (
    loan_id VARCHAR(20) PRIMARY KEY,
    customer_id VARCHAR(20) REFERENCES banking.customers(customer_id),
    loan_amount NUMERIC(12,2),
    interest_rate NUMERIC(5,2),
    start_date DATE
);
CREATE INDEX IF NOT EXISTS loans_customer_idx ON banking.loans (customer_id);

CREATE TABLE IF NOT EXISTS banking.transactions (
    transaction_id VARCHAR(25) PRIMARY KEY,
    account_id VARCHAR(20) REFERENCES banking.accounts(account_id),
    merchant_id VARCHAR(20) REFERENCES banking.merchants(merchant_id),
    amount_usd NUMERIC(12,2),
    transaction_date TIMESTAMP
);
CREATE INDEX IF NOT EXISTS transactions_account_idx ON banking.transactions (account_id);
CREATE INDEX IF NOT EXISTS transactions_merchant_idx ON banking.transactions (merchant_id);
"""

# (table, columns) — parents before children so FK inserts succeed.
TABLES = [
    ("customers", ["customer_id", "first_name", "last_name", "email", "city",
                    "credit_score", "created_at"]),
    ("merchants", ["merchant_id", "merchant_name", "city"]),
    ("branches", ["branch_id", "branch_name", "manager_name", "city", "country"]),
    ("accounts", ["account_id", "customer_id", "account_type", "balance_usd", "open_date"]),
    ("cards", ["card_id", "account_id", "card_type", "expiration_date"]),
    ("loans", ["loan_id", "customer_id", "loan_amount", "interest_rate", "start_date"]),
    ("transactions", ["transaction_id", "account_id", "merchant_id", "amount_usd",
                       "transaction_date"]),
]


def find_sqlite_db(source: str) -> str:
    if os.path.isfile(source):
        return source
    hits = glob.glob(os.path.join(source, "**", "bank_sqlite.db"), recursive=True)
    if not hits:
        raise FileNotFoundError(f"no bank_sqlite.db found under {source}")
    return hits[0]


def main():
    source = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/Downloads/archive")
    db_path = find_sqlite_db(source)
    print(f"loading from {db_path}")

    sconn = sqlite3.connect(db_path)
    pconn = psycopg.connect(settings.postgres_dsn)
    pconn.execute(DDL)
    pconn.execute(
        "TRUNCATE TABLE banking.customers, banking.accounts, banking.cards, "
        "banking.merchants, banking.branches, banking.loans, banking.transactions"
    )

    total = 0
    for table, columns in TABLES:
        cur = sconn.execute(f"SELECT {', '.join(columns)} FROM {table}")
        with pconn.cursor().copy(
            f"COPY banking.{table} ({', '.join(columns)}) FROM STDIN"
        ) as copy:
            n = 0
            for row in cur:
                copy.write_row(row)
                n += 1
        print(f"loaded banking.{table}: {n} rows")
        total += n

    pconn.execute(
        "ANALYZE banking.customers, banking.accounts, banking.cards, "
        "banking.merchants, banking.branches, banking.loans, banking.transactions"
    )
    pconn.commit()
    pconn.close()
    sconn.close()
    print(f"done: {total} rows loaded into schema 'banking'")


if __name__ == "__main__":
    main()
