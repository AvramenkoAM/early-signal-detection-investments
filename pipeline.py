"""
pipeline.py — ETL orchestrator + CLI entry point.

Runs the full Extract → Transform → Load pipeline and prints a summary.

Usage examples:
    python pipeline.py                            # run all three sources
    python pipeline.py --sources trends github    # skip RSS feeds
    python pipeline.py --sources rss              # news only
    python pipeline.py --top 10                   # show top 10 signals
    python pipeline.py --db output/custom.db      # custom database path
    python pipeline.py --debug                    # verbose logging
"""

import sys                              # sys.exit for non-zero failure codes
import argparse                         # standard-library CLI argument parser
import logging                          # structured log messages
from pathlib import Path                # OS-agnostic path objects

import config                           # project constants (paths, keywords, etc.)
from etl.extract   import fetch_all     # E: collect raw data from all sources
from etl.transform import build_signals_df   # T: clean, count, normalise
from etl.load      import save_all      # L: write CSV + SQLite


# ══════════════════════════════════════════════════════════════════════════════
# Logging setup
# ══════════════════════════════════════════════════════════════════════════════

def setup_logging(debug: bool = False) -> None:
    """Configure the root logger for the whole pipeline."""
    level = logging.DEBUG if debug else logging.INFO    # DEBUG flag increases verbosity
    logging.basicConfig(                # apply to the root logger (affects all modules)
        level   = level,
        format  = config.LOG_FORMAT,    # timestamp + level + module + message
        datefmt = config.LOG_DATEFMT,   # "2026-06-08 15:00:00" style timestamps
    )


# ══════════════════════════════════════════════════════════════════════════════
# CLI argument parser
# ══════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    """
    Define and parse CLI arguments.
    All arguments are optional — sensible defaults come from config.py.
    """
    parser = argparse.ArgumentParser(   # create the parser with a descriptive help message
        prog        = "pipeline.py",
        description = (
            "Early Signal Detection ETL Pipeline — "
            "collects tech trend signals from Google Trends, GitHub, and news RSS feeds."
        ),
        formatter_class = argparse.RawDescriptionHelpFormatter,  # preserve newlines in help text
    )

    parser.add_argument(                # which data sources to run
        "--sources",
        nargs   = "+",                  # accept one or more values: --sources trends github
        choices = ["trends", "github", "rss"],  # only these three are valid
        default = ["trends", "github", "rss"],  # default: run all three
        metavar = "SOURCE",
        help    = "Sources to collect from (default: all). Options: trends github rss",
    )

    parser.add_argument(                # where to write output files
        "--output-dir",
        type    = Path,                 # convert string argument to a Path object
        default = config.OUTPUT_DIR,    # default from config.py
        metavar = "DIR",
        help    = f"Directory for CSV output (default: {config.OUTPUT_DIR})",
    )

    parser.add_argument(                # custom SQLite database path
        "--db",
        type    = Path,
        default = config.DB_PATH,       # default: output/signals.db
        metavar = "FILE",
        help    = f"SQLite database file path (default: {config.DB_PATH})",
    )

    parser.add_argument(                # how many top signals to print in the summary
        "--top",
        type    = int,
        default = 5,                    # default: show top 5
        metavar = "N",
        help    = "Number of top signals to print (default: 5)",
    )

    parser.add_argument(                # verbose debug logging flag
        "--debug",
        action  = "store_true",         # flag; adds --debug with no value required
        help    = "Enable DEBUG-level logging for verbose output",
    )

    return parser.parse_args()          # parse sys.argv and return the Namespace object


# ══════════════════════════════════════════════════════════════════════════════
# Summary printer
# ══════════════════════════════════════════════════════════════════════════════

def print_summary(df, top_n: int, load_result: dict) -> None:
    """Print a formatted table of the top-N fastest-growing signals to stdout."""
    print()                             # blank separator before the summary block
    print("═" * 62)
    print("  EARLY SIGNAL DETECTION — TOP SIGNALS THIS RUN")
    print("═" * 62)

    if df.empty:                        # edge case: pipeline ran but found no signals
        print("  No signals detected. Check logs for extraction errors.")
        print("═" * 62)
        return                          # nothing more to print

    top = df.head(top_n)                # take the first N rows (already sorted by frequency)

    # Header row
    print(f"  {'#':<4} {'Keyword':<22} {'Source':<22} {'Freq':>5}")
    print("  " + "-" * 58)

    for i, row in enumerate(top.itertuples(index=False), start=1):  # 1-based rank
        keyword_short = row.keyword[:21]     # truncate long keywords to fit the column
        source_short  = row.source[:21]      # truncate long source names

        print(
            f"  {i:<4} "                     # rank
            f"{keyword_short:<22} "           # keyword (left-aligned, padded)
            f"{source_short:<22} "            # source (left-aligned, padded)
            f"{row.frequency:>5}"             # frequency (right-aligned integer)
        )

    print("═" * 62)

    # Load summary below the table
    if load_result.get("csv_path"):     # only print if a file was actually written
        print(f"  CSV  → {load_result['csv_path']}")
    print(f"  DB   → {load_result['db_path']}")
    print(f"  Rows → {load_result['rows']} inserted this run")
    print()                             # trailing blank line


# ══════════════════════════════════════════════════════════════════════════════
# Main pipeline orchestrator
# ══════════════════════════════════════════════════════════════════════════════

def run_pipeline(
    sources:    list[str],
    output_dir: Path,
    db_path:    Path,
    top_n:      int,
) -> None:
    """
    Orchestrate the full ETL pipeline:
        1. Extract  — fetch raw data from requested sources
        2. Transform — normalise text, count keyword frequencies, deduplicate
        3. Load     — write results to CSV and SQLite
        4. Report   — print top-N signals summary to stdout
    """
    log = logging.getLogger(__name__)   # use the module-level logger (after setup_logging was called)

    log.info("Pipeline starting  |  sources: %s", sources)  # announce which sources will run

    # ── EXTRACT ────────────────────────────────────────────────────────────────
    log.info("Step 1/3 — Extract")
    raw = fetch_all(sources=sources)    # call all requested extractors; failures return empty data

    # Summarise what was collected
    n_trends = len(raw.get("trends", {}))       # number of trend keyword scores
    n_github = len(raw.get("github", []))        # number of GitHub repo text blobs
    n_rss    = sum(                              # total article texts across all feeds
        len(v) for v in raw.get("rss", {}).values()
    )
    log.info(
        "Extract complete: trends=%d kw, github=%d repos, rss=%d articles",
        n_trends, n_github, n_rss,
    )

    # ── TRANSFORM ──────────────────────────────────────────────────────────────
    log.info("Step 2/3 — Transform")
    signals_df = build_signals_df(raw)  # produce clean signals DataFrame

    if signals_df.empty:                # nothing to save — likely all sources failed
        log.error("No signals produced. Verify network connectivity and try again.")
        sys.exit(1)                     # exit with error code so CI/cron jobs can detect failure

    # ── LOAD ───────────────────────────────────────────────────────────────────
    log.info("Step 3/3 — Load")
    load_result = save_all(             # write to CSV and SQLite
        df         = signals_df,
        output_dir = output_dir,
        db_path    = db_path,
    )

    # ── REPORT ─────────────────────────────────────────────────────────────────
    print_summary(signals_df, top_n, load_result)   # always print to stdout for operator review

    log.info(
        "Pipeline complete  |  %d signals  |  %d rows saved",
        len(signals_df), load_result["rows"],
    )


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":              # run only when executed directly, not when imported
    args = parse_args()                 # parse CLI arguments first
    setup_logging(debug=args.debug)     # configure logging before any module uses it

    run_pipeline(                       # hand off to the pipeline with parsed arguments
        sources    = args.sources,      # e.g. ["trends", "github"]
        output_dir = args.output_dir,   # Path object for CSV output
        db_path    = args.db,           # Path object for SQLite DB
        top_n      = args.top,          # how many signals to print
    )
