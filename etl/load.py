"""
etl/load.py — L in ETL.

Two load targets:
    1. CSV file  — human-readable, easy to open in Excel or share
    2. SQLite DB — queryable storage with schema enforcement

Database schema (table: signals):
    id        INTEGER  PRIMARY KEY AUTOINCREMENT
    keyword   TEXT     NOT NULL
    source    TEXT     NOT NULL
    frequency INTEGER  NOT NULL
    timestamp TEXT     NOT NULL     (ISO-8601 UTC string)

Public API:
    save_all(df, output_dir, db_path) — saves to both CSV and SQLite
"""

import sqlite3                          # standard-library SQLite driver — no extra install needed
import logging                          # structured log messages
from datetime import datetime           # for timestamping the CSV filename
from pathlib import Path                # OS-agnostic path handling

import pandas as pd                     # DataFrame type hint + to_csv / itertuples

# ── Module-level logger ────────────────────────────────────────────────────────
log = logging.getLogger(__name__)       # scoped to "etl.load" in log output


# ══════════════════════════════════════════════════════════════════════════════
# TARGET 1 — CSV
# ══════════════════════════════════════════════════════════════════════════════

def save_to_csv(df: pd.DataFrame, output_dir: Path) -> Path:
    """
    Write the signals DataFrame to a timestamped CSV file.
    Returns the full path of the saved file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)   # create output/ if it doesn't exist

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")  # compact timestamp for unique names
    filepath = output_dir / f"signals_{ts}.csv"          # e.g. output/signals_20260608_153000.csv

    df.to_csv(filepath, index=False)    # write without the pandas row-number index
    log.info("CSV saved → %s  (%d rows)", filepath, len(df))  # confirm path and row count

    return filepath                     # return path so pipeline.py can display it


# ══════════════════════════════════════════════════════════════════════════════
# TARGET 2 — SQLite
# ══════════════════════════════════════════════════════════════════════════════

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS signals (
    id        INTEGER  PRIMARY KEY AUTOINCREMENT,  -- auto-incrementing surrogate key
    keyword   TEXT     NOT NULL,                   -- normalised keyword string
    source    TEXT     NOT NULL,                   -- e.g. "google_trends", "rss_techcrunch"
    frequency INTEGER  NOT NULL,                   -- how many times keyword appeared in source
    timestamp TEXT     NOT NULL                    -- ISO-8601 UTC string of pipeline run time
);
"""                                     # triple-quoted string keeps SQL readable

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_signals_keyword
    ON signals (keyword);
"""                                     # index on keyword speeds up SELECT WHERE keyword = ?

_INSERT_SQL = """
INSERT INTO signals (keyword, source, frequency, timestamp)
VALUES (?, ?, ?, ?);
"""                                     # parameterised query prevents SQL injection


def init_db(db_path: Path) -> sqlite3.Connection:
    """
    Open (or create) the SQLite database and ensure the signals table exists.
    Returns an open connection — caller is responsible for closing it.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)   # create parent dirs if needed

    conn = sqlite3.connect(str(db_path))    # open file-based SQLite db (creates if absent)
    conn.execute(_CREATE_TABLE_SQL)          # create signals table if it doesn't exist
    conn.execute(_CREATE_INDEX_SQL)          # create index for fast keyword lookups
    conn.execute("PRAGMA journal_mode=WAL;") # WAL mode: better concurrency for reads during writes
    conn.commit()                            # persist schema changes immediately

    log.debug("SQLite: database initialised at %s", db_path)
    return conn                             # return open connection to the caller


def save_to_db(df: pd.DataFrame, db_path: Path) -> int:
    """
    Insert all rows from the signals DataFrame into the SQLite signals table.
    Uses parameterised INSERT for safety.

    Returns the number of rows inserted.
    """
    if df.empty:                        # nothing to insert — avoid opening DB unnecessarily
        log.warning("SQLite: DataFrame is empty, nothing to insert.")
        return 0                        # report zero rows inserted

    conn = init_db(db_path)             # ensure table exists and get a connection

    rows_inserted = 0                   # counter for the log summary

    try:
        with conn:                      # context manager handles transaction commit/rollback
            for row in df.itertuples(index=False):  # iterate rows as named tuples (fast)
                conn.execute(           # execute a single parameterised INSERT
                    _INSERT_SQL,        # SQL template with ? placeholders
                    (                   # tuple of values matching the ? placeholders
                        str(row.keyword),    # keyword text
                        str(row.source),     # source label
                        int(row.frequency),  # frequency as integer
                        str(row.timestamp),  # ISO-8601 string
                    ),
                )
                rows_inserted += 1      # increment counter for each successful insert

        log.info("SQLite: inserted %d rows into %s", rows_inserted, db_path)

    except sqlite3.Error as exc:        # catch DB errors (disk full, locked, schema mismatch)
        log.error("SQLite insert failed: %s", exc)
        raise                           # re-raise so pipeline.py can catch and exit cleanly

    finally:
        conn.close()                    # always close the connection, even on error

    return rows_inserted                # return count for pipeline summary


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API — save_all
# ══════════════════════════════════════════════════════════════════════════════

def save_all(
    df: pd.DataFrame,
    output_dir: Path,
    db_path: Path,
) -> dict[str, object]:
    """
    Save signals to both CSV and SQLite in one call.

    Returns a dict with paths and row counts for the pipeline summary:
        {"csv_path": Path, "db_path": Path, "rows": int}
    """
    if df.empty:                        # nothing to save — warn and return early
        log.warning("Load: signals DataFrame is empty — nothing saved.")
        return {"csv_path": None, "db_path": db_path, "rows": 0}

    csv_path = save_to_csv(df, output_dir)   # write CSV file
    rows     = save_to_db(df, db_path)       # insert into SQLite

    return {                            # return summary for pipeline.py to display
        "csv_path": csv_path,           # Path to the new CSV file
        "db_path":  db_path,            # Path to the SQLite DB (may already exist)
        "rows":     rows,               # number of rows inserted this run
    }
