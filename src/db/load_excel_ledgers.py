"""
One-off ingestion of the user's real Excel ledger exports into Postgres,
normalized into a single `ledger.general_ledger` table so the agent can
query them as one origin datasource (results get cached into ClickHouse
automatically, same as every other source).

Reads from a source directory (default: ~/Downloads) plus its immediate
subdirectories (Google Drive downloads unzip into a nested folder), by
matching known filenames. Files that don't match are ignored — this is
not a generic "load any spreadsheet" tool.

Idempotent: rerunning replaces the rows for each source_file rather than
appending duplicates.

Run:  python -m db.load_excel_ledgers [source_dir]
"""
import glob
import os
import sys

import pandas as pd
import psycopg

from config import settings

DDL = """
CREATE SCHEMA IF NOT EXISTS ledger;

CREATE TABLE IF NOT EXISTS ledger.general_ledger (
    id BIGSERIAL PRIMARY KEY,
    source_file TEXT NOT NULL,
    entered_date DATE,
    effective_date DATE,
    account_code TEXT,
    account_name TEXT,
    debit NUMERIC,
    credit NUMERIC,
    memo TEXT,
    dept TEXT,
    cost_center TEXT,
    currency TEXT,
    journal_type TEXT,
    transaction_ref TEXT,
    source_ref TEXT
);
CREATE INDEX IF NOT EXISTS general_ledger_source_idx ON ledger.general_ledger (source_file);
CREATE INDEX IF NOT EXISTS general_ledger_account_idx ON ledger.general_ledger (account_code);
"""

COLUMNS = [
    "source_file", "entered_date", "effective_date", "account_code", "account_name",
    "debit", "credit", "memo", "dept", "cost_center", "currency", "journal_type",
    "transaction_ref", "source_ref",
]


def _row(source_file, **kwargs) -> tuple:
    values = {c: None for c in COLUMNS}
    values["source_file"] = source_file
    values.update(kwargs)
    return tuple(values[c] for c in COLUMNS)


def load_general_ledger_xlsx(path: str) -> list[tuple]:
    df = pd.read_excel(path)
    rows = []
    for r in df.itertuples(index=False):
        rows.append(_row(
            "general_ledger",
            entered_date=r.TxnDate.date() if pd.notna(r.TxnDate) else None,
            effective_date=r.TxnDate.date() if pd.notna(r.TxnDate) else None,
            account_code=str(r.AccountNumber),
            account_name=r.AccountName,
            debit=float(r.Debit) if pd.notna(r.Debit) else None,
            credit=float(r.Credit) if pd.notna(r.Credit) else None,
            memo=r.Description,
            dept=r.Dept,
            cost_center=r.CostCenter,
            currency=r.Currency,
            transaction_ref=r.GLID,
        ))
    return rows


def load_sample_gl_data_xlsx(path: str, source_file: str) -> list[tuple]:
    df = pd.read_excel(path)
    df.columns = [c.strip().lower() for c in df.columns]
    has_type = "type" in df.columns
    rows = []
    for d in df.to_dict("records"):
        rows.append(_row(
            source_file,
            entered_date=d["entered date"].date() if pd.notna(d["entered date"]) else None,
            effective_date=d["effective date"].date() if pd.notna(d["effective date"]) else None,
            account_code=str(d["account"]),
            account_name=d["account_description"],
            debit=float(d["debit"]) if pd.notna(d["debit"]) else None,
            credit=float(d["credit"]) if pd.notna(d["credit"]) else None,
            memo=d["memo"],
            journal_type=d["type"] if has_type and pd.notna(d.get("type")) else None,
            transaction_ref=d["transaction"],
            source_ref=str(d["source"]) if pd.notna(d["source"]) else None,
        ))
    return rows


# (source_file tag, filename to look for, loader)
KNOWN_FILES = [
    ("general_ledger", "General-Ledger.xlsx", load_general_ledger_xlsx),
    ("sample_gl_data_1", "Sample GL Data 1.xlsx", load_sample_gl_data_xlsx),
    ("sample_gl_data_2", "Sample GL Data 2.xlsx", load_sample_gl_data_xlsx),
    ("sample_gl_data_3", "Sample GL Data 3.xlsx", load_sample_gl_data_xlsx),
]

# Present in the source directory but has no usable data (empty template) —
# skipped rather than silently loading zero rows.
SKIPPED_FILES = ["Sample Ledger and Balance Sheet.xls"]


def find_file(source_dir: str, filename: str) -> str | None:
    matches = glob.glob(os.path.join(source_dir, "**", filename), recursive=True)
    return matches[0] if matches else None


def main():
    source_dir = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/Downloads")

    con = psycopg.connect(settings.postgres_dsn)
    con.execute(DDL)

    total = 0
    for source_file, filename, loader in KNOWN_FILES:
        path = find_file(source_dir, filename)
        if not path:
            print(f"skip {filename}: not found under {source_dir}")
            continue
        if source_file == "general_ledger":
            rows = loader(path)
        else:
            rows = loader(path, source_file)
        con.execute("DELETE FROM ledger.general_ledger WHERE source_file = %s", (source_file,))
        with con.cursor().copy(
            f"COPY ledger.general_ledger ({', '.join(COLUMNS)}) FROM STDIN"
        ) as copy:
            for row in rows:
                copy.write_row(row)
        print(f"loaded {filename}: {len(rows)} rows -> source_file='{source_file}'")
        total += len(rows)

    for filename in SKIPPED_FILES:
        path = find_file(source_dir, filename)
        if path:
            print(f"skip {filename}: template with no ledger rows (0 GL entries, blank balance sheet)")

    con.commit()
    con.close()
    print(f"done: {total} rows in ledger.general_ledger")


if __name__ == "__main__":
    main()
