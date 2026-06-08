"""
signal_detector.py — Emerging Trend Signal Detection Module
============================================================
Reads a CSV produced by the ETL pipeline and applies statistical methods
to identify the fastest-growing, most anomalous market signals.

Supports two CSV formats automatically:
  • Time-series  — wide format: date | keyword_A | keyword_B | ...
                   (produced by main.py / Google Trends raw export)
  • Snapshot     — long format: keyword | source | frequency | timestamp
                   (produced by pipeline.py ETL output)

Statistical methods:
  • Growth rate  — (last_N_avg − prev_N_avg) / prev_N_avg × 100
  • Moving avg   — rolling mean over the last N periods
  • Z-score      — (value − mean) / std; used for anomaly detection
  • Score        — growth_rate × log1p(frequency); ranks emerging signals

Output DataFrame columns:
  keyword | growth_rate | moving_avg | frequency | z_score | score | is_anomaly

Usage:
  python signal_detector.py                       # auto-picks latest raw CSV
  python signal_detector.py --csv path/to/file.csv
  python signal_detector.py --top 10 --window 4 --threshold 1.5
"""

from __future__ import annotations             # supports X | Y type hints on Python 3.9

import sys                                      # sys.exit for CLI error handling
import glob                                     # file pattern matching to find latest CSV
import logging                                  # structured log messages
import argparse                                 # CLI argument parsing
from pathlib import Path                        # OS-agnostic path operations
from datetime import datetime                   # timestamping output files

import numpy as np                              # numerical operations (log, std, mean)
import pandas as pd                             # DataFrame construction and manipulation

import config                                   # project constants (paths, log format)
import notifier                                # email alert module


# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(                            # configure root logger once at module level
    level   = logging.INFO,                     # show INFO and above
    format  = config.LOG_FORMAT,                # "[module] timestamp level message"
    datefmt = config.LOG_DATEFMT,
)
log = logging.getLogger(__name__)               # "signal_detector" in log output


# ── Constants ─────────────────────────────────────────────────────────────────
DEFAULT_WINDOW    = 4                           # default rolling window in weeks (~1 month)
DEFAULT_THRESHOLD = 1.5                         # z-score threshold for anomaly labelling
DEFAULT_TOP_N     = 10                          # number of top signals to display


# ══════════════════════════════════════════════════════════════════════════════
# 1. DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════

def _detect_csv_format(df: pd.DataFrame) -> str:
    """
    Inspect column names to decide if a CSV is time-series or snapshot format.

    Time-series: has a 'date' column + keyword columns with numeric values.
    Snapshot:    has 'keyword', 'source', 'frequency' columns (ETL output).

    Returns "timeseries" or "snapshot".
    """
    cols = set(df.columns.str.lower())          # lowercase all column names for robust matching
    if "keyword" in cols and "frequency" in cols:  # ETL snapshot columns present
        return "snapshot"
    if "date" in cols:                          # raw trends CSV has a date index column
        return "timeseries"
    raise ValueError(                           # unrecognised format — inform the caller clearly
        f"Cannot detect CSV format. Columns found: {list(df.columns)}"
    )


def load_timeseries(path: Path) -> pd.DataFrame:
    """
    Load a wide-format time-series CSV (date + one column per keyword).
    Parses dates and sets them as the DataFrame index.

    Returns a DataFrame indexed by date with one column per keyword.
    """
    df = pd.read_csv(path, parse_dates=["date"])  # parse 'date' column as datetime
    df = df.set_index("date")                    # make date the index for easy slicing
    df = df.sort_index()                         # ensure chronological order
    df.columns = [c.lower() for c in df.columns]  # normalise column names to lowercase
    log.info("Loaded time-series: %d weeks × %d keywords from %s", len(df), len(df.columns), path)
    return df                                    # wide DataFrame: date → keyword_A, keyword_B …


def load_snapshot(path: Path) -> pd.DataFrame:
    """
    Load a long-format snapshot CSV (keyword | source | frequency | timestamp).
    Aggregates frequency across sources by keyword (sum).

    Returns a single-row-per-keyword Series indexed by keyword.
    """
    df = pd.read_csv(path)                       # read ETL output CSV
    df["keyword"] = df["keyword"].str.lower().str.strip()  # normalise keyword text
    agg = (                                      # sum frequencies across all sources per keyword
        df.groupby("keyword")["frequency"]
        .sum()
        .rename("frequency")
    )
    log.info(
        "Loaded snapshot: %d unique keywords from %s (aggregated across sources)",
        len(agg), path,
    )
    return agg                                   # Series: keyword → total_frequency


def load_csv(path: Path) -> tuple[pd.DataFrame | pd.Series, str]:
    """
    Load a CSV file and auto-detect its format.
    Returns a (data, format_name) tuple.
    """
    raw = pd.read_csv(path, nrows=2)             # read just 2 rows to detect format cheaply
    fmt = _detect_csv_format(raw)                # "timeseries" or "snapshot"
    log.info("CSV format detected: %s", fmt)

    if fmt == "timeseries":                      # full time-series analysis
        return load_timeseries(path), fmt
    else:                                        # cross-sectional snapshot analysis
        return load_snapshot(path), fmt


# ══════════════════════════════════════════════════════════════════════════════
# 2. STATISTICAL COMPUTATIONS
# ══════════════════════════════════════════════════════════════════════════════

def compute_growth_rate(series: pd.Series, window: int) -> float:
    """
    Calculate the percentage growth between the last N periods and the
    preceding N periods.

    Formula:  growth = (mean(last_N) − mean(prev_N)) / mean(prev_N) × 100

    Returns NaN if there are fewer than 2×window data points, or if
    the previous window average is zero (undefined growth).
    """
    if len(series) < 2 * window:                # not enough history to compare two windows
        return float("nan")                     # signal "insufficient data" to the caller

    last_avg = series.iloc[-window:].mean()     # average of the most recent N periods
    prev_avg = series.iloc[-2 * window:-window].mean()  # average of the N periods before that

    if prev_avg == 0:                           # division by zero guard
        return float("nan")                     # growth is undefined when baseline is 0

    return float((last_avg - prev_avg) / prev_avg * 100)  # percentage change


def compute_moving_average(series: pd.Series, window: int) -> float:
    """
    Compute the rolling mean of the last `window` observations.
    Returns NaN if the series has fewer points than the window.
    """
    if len(series) < window:                    # not enough data for a full window
        return float("nan")
    return float(series.iloc[-window:].mean())  # arithmetic mean of the most recent N values


def compute_zscore(series: pd.Series) -> pd.Series:
    """
    Compute the z-score for every point in the series.

    Formula:  z = (x − mean) / std

    Uses ddof=0 (population std) to avoid NaN on small series.
    Returns a Series of the same length; NaN where std == 0 (constant series).
    """
    mu  = series.mean()                         # arithmetic mean of the full series
    std = series.std(ddof=0)                    # population standard deviation

    if std == 0:                                # constant series → all z-scores would be 0/0
        return pd.Series(np.zeros(len(series)), index=series.index)  # z = 0 for flat lines

    return (series - mu) / std                  # element-wise z-score calculation


def detect_anomaly(series: pd.Series, threshold: float) -> bool:
    """
    Determine whether the LATEST value in the series is anomalously high.

    A value is anomalous if its z-score exceeds `threshold` (default 1.5).
    A z-score of 1.5 means the value is 1.5 standard deviations above the mean.

    Returns True if the latest point is an anomaly, False otherwise.
    """
    zscores  = compute_zscore(series)           # compute z-scores for the entire series
    latest_z = float(zscores.iloc[-1])          # z-score of the most recent observation
    return latest_z > threshold                 # True = anomalous spike detected


def score_signal(growth_rate: float, frequency: float) -> float:
    """
    Compute a composite signal score that balances growth speed and volume.

    Formula:  score = growth_rate × log1p(frequency)

    Why log1p?
      • Prevents low-volume keywords with extreme growth from dominating.
      • log1p(0) = 0, so zero-frequency keywords score 0.
      • log1p is always defined (no log(0) error).

    Negative growth → negative score (keyword is declining, not emerging).
    """
    if np.isnan(growth_rate) or np.isnan(frequency):  # guard against NaN inputs
        return float("nan")
    return float(growth_rate * np.log1p(frequency))   # composite ranking score


# ══════════════════════════════════════════════════════════════════════════════
# 3. PER-KEYWORD ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def analyse_keyword(
    series:    pd.Series,
    keyword:   str,
    window:    int,
    threshold: float,
) -> dict:
    """
    Run the full statistical pipeline for a single keyword's time series.

    Returns a flat dict with all computed metrics — one row in the final report.
    """
    frequency   = float(series.iloc[-1])        # latest observed frequency value
    growth_rate = compute_growth_rate(series, window)       # % change vs prior window
    moving_avg  = compute_moving_average(series, window)    # rolling mean of last N periods
    latest_z    = float(compute_zscore(series).iloc[-1])    # z-score of the latest value
    anomaly     = detect_anomaly(series, threshold)          # True if z > threshold
    sig_score   = score_signal(growth_rate, frequency)       # composite ranking score

    return {                                    # one dict per keyword → one DataFrame row
        "keyword":     keyword,                 # keyword label
        "frequency":   frequency,               # most recent raw frequency value
        "moving_avg":  round(moving_avg,  2),   # N-period rolling mean (rounded for readability)
        "growth_rate": round(growth_rate, 2) if not np.isnan(growth_rate) else None,
        "z_score":     round(latest_z,    2),   # z-score of latest observation
        "score":       round(sig_score,   2) if not np.isnan(sig_score) else None,
        "is_anomaly":  anomaly,                 # True/False anomaly flag
    }


# ══════════════════════════════════════════════════════════════════════════════
# 4. REPORT BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_report_from_timeseries(
    df:        pd.DataFrame,
    window:    int,
    threshold: float,
) -> pd.DataFrame:
    """
    Analyse every keyword column in a wide-format time-series DataFrame.
    Returns the assembled signals report, sorted by score descending.
    """
    rows = []                                   # accumulator — one dict per keyword

    for keyword in df.columns:                  # iterate every tracked keyword
        series = df[keyword].dropna()           # drop NaN rows for this keyword
        if len(series) < 2:                     # need at least 2 points for any meaningful stats
            log.warning("Skipping '%s': too few data points (%d).", keyword, len(series))
            continue
        row = analyse_keyword(series, keyword, window, threshold)  # full stats dict
        rows.append(row)                        # append to result accumulator

    return _finalise_report(rows)               # sort, index, return


def build_report_from_snapshot(
    freq_series: pd.Series,
    threshold:   float,
) -> pd.DataFrame:
    """
    Build a report from a snapshot (no time dimension).
    Growth rate cannot be computed, so cross-sectional z-scores are used instead.

    Anomaly = keyword whose frequency is unusually high vs all other keywords.
    """
    rows = []                                   # accumulator

    all_zscores = compute_zscore(freq_series)   # z-score across keywords (cross-sectional)

    for keyword, freq in freq_series.items():   # iterate each keyword's aggregated frequency
        z     = float(all_zscores[keyword])     # this keyword's z-score vs all others
        anom  = z > threshold                   # anomaly if significantly above average
        sig_score = score_signal(0.0, freq)     # growth rate unknown → use 0 (conservative)
        rows.append({
            "keyword":     keyword,
            "frequency":   float(freq),
            "moving_avg":  float(freq),         # no time series → use frequency as surrogate
            "growth_rate": None,                # undefined without time dimension
            "z_score":     round(z, 2),
            "score":       round(sig_score, 2),
            "is_anomaly":  anom,
        })
        log.debug(
            "Snapshot '%s': freq=%.0f  z=%.2f  anomaly=%s",
            keyword, freq, z, anom,
        )

    return _finalise_report(rows)               # sort, index, return


def _finalise_report(rows: list[dict]) -> pd.DataFrame:
    """
    Convert a list of result dicts to a clean, sorted DataFrame.
    Sorts by score descending (None scores go to the bottom).
    """
    if not rows:                                # no keywords analysed — return empty frame
        return pd.DataFrame(columns=[
            "keyword", "frequency", "moving_avg", "growth_rate", "z_score", "score", "is_anomaly"
        ])

    df = pd.DataFrame(rows)                     # convert dicts to DataFrame

    df = df.sort_values(                        # sort by composite score, highest first
        "score",
        ascending  = False,
        na_position = "last",                   # push None-score rows to the bottom
    ).reset_index(drop=True)                    # clean 0-based integer index after sorting

    return df                                   # final sorted report DataFrame


# ══════════════════════════════════════════════════════════════════════════════
# 5. REPORTING
# ══════════════════════════════════════════════════════════════════════════════

def print_top_signals(df: pd.DataFrame, top_n: int) -> None:
    """
    Print a formatted table of the top-N emerging signals to stdout.
    Anomalous signals are highlighted with a 🔴 marker.
    """
    top = df.head(top_n)                        # take the first N rows (already sorted by score)

    print()                                     # blank line before the output block
    print("═" * 72)
    print(f"  TOP {top_n} EMERGING SIGNALS")
    print("═" * 72)
    print(
        f"  {'#':<3} "
        f"{'Keyword':<22} "
        f"{'Freq':>6} "
        f"{'Growth':>8} "
        f"{'MovAvg':>7} "
        f"{'Z':>6} "
        f"{'Score':>8} "
        f"{'Anomaly':<8}"
    )
    print("  " + "-" * 68)

    for i, row in enumerate(top.itertuples(index=False), start=1):  # 1-based rank
        growth_str = f"{row.growth_rate:+.1f}%" if row.growth_rate is not None else "  N/A  "
        score_str  = f"{row.score:+.1f}"        if row.score  is not None else "  N/A  "
        anom_flag  = "🔴" if row.is_anomaly else "  "  # visual highlight for anomalies

        print(
            f"  {i:<3} "
            f"{str(row.keyword)[:21]:<22} "      # truncate long keyword names
            f"{row.frequency:>6.0f} "
            f"{growth_str:>8} "
            f"{row.moving_avg:>7.1f} "
            f"{row.z_score:>6.2f} "
            f"{score_str:>8} "
            f"{anom_flag}"
        )

    print("═" * 72)
    anomaly_count = df["is_anomaly"].sum()       # count flagged anomalies in the full result
    print(f"  Total signals: {len(df)}  |  Anomalies: {anomaly_count}")
    print()                                     # trailing blank line


def save_report(df: pd.DataFrame, output_dir: Path) -> Path:
    """
    Save the signals report to a timestamped CSV file in output_dir.
    Returns the file path.
    """
    output_dir.mkdir(parents=True, exist_ok=True)    # create output/ if needed

    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")  # compact timestamp for unique filenames
    path = output_dir / f"signal_report_{ts}.csv"    # e.g. output/signal_report_20260608.csv

    df.to_csv(path, index=False)                # write without the row-number index
    log.info("Report saved → %s  (%d signals)", path, len(df))

    return path                                 # return for pipeline integration


# ══════════════════════════════════════════════════════════════════════════════
# 6. MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def detect_signals(
    csv_path:  Path,
    window:    int   = DEFAULT_WINDOW,
    threshold: float = DEFAULT_THRESHOLD,
    top_n:     int   = DEFAULT_TOP_N,
    save:      bool  = True,
) -> pd.DataFrame:
    """
    Full signal detection pipeline for a single CSV file.

    Steps:
      1. Load and detect CSV format
      2. Compute growth, moving average, z-score, anomaly flag, score
      3. Sort and print top-N signals
      4. Optionally save the report to output/

    Args:
        csv_path:  path to the input CSV file
        window:    number of periods for growth and moving average (default 4 weeks)
        threshold: z-score threshold for anomaly detection (default 1.5)
        top_n:     how many top signals to display in stdout (default 10)
        save:      if True, writes report CSV to output/ (default True)

    Returns:
        pd.DataFrame with columns:
            keyword | frequency | moving_avg | growth_rate | z_score | score | is_anomaly
    """
    log.info(
        "Signal detection start  |  csv=%s  window=%d  threshold=%.1f",
        csv_path.name, window, threshold,
    )

    data, fmt = load_csv(csv_path)              # load data and detect its format

    if fmt == "timeseries":                     # time-series: per-keyword temporal analysis
        report = build_report_from_timeseries(data, window, threshold)
    else:                                       # snapshot: cross-sectional analysis
        report = build_report_from_snapshot(data, threshold)

    if report.empty:                            # no signals produced — likely bad input
        log.error("No signals computed. Check the input CSV format.")
        return report                           # return empty DataFrame; caller handles it

    print_top_signals(report, top_n)            # display formatted table to stdout

    if save:                                    # write CSV only when explicitly enabled
        save_report(report, config.OUTPUT_DIR)

    anomaly_count = int(report["is_anomaly"].sum())
    log.info(
        "Signal detection complete  |  %d signals  |  %d anomalies",
        len(report),
        anomaly_count,
    )

    if anomaly_count > 0:                       # only attempt alert when anomalies exist
        notifier.send_anomaly_alert(report)     # sends email if EMAIL_ENABLED=true in .env

    return report                               # return full DataFrame for downstream use


# ══════════════════════════════════════════════════════════════════════════════
# 7. CLI
# ══════════════════════════════════════════════════════════════════════════════

def _find_latest_csv() -> Path | None:
    """
    Auto-discover the most recent raw trends CSV in the data/ directory.
    Falls back to the most recent signals CSV in output/ if no raw file exists.
    Returns None if no CSV is found in either location.
    """
    raw_files = sorted(glob.glob(str(config.DATA_DIR / "raw_trends_*.csv")))  # find raw CSVs
    if raw_files:                               # prefer raw trends (richer time-series data)
        return Path(raw_files[-1])              # return the lexicographically last (= newest)

    output_files = sorted(glob.glob(str(config.OUTPUT_DIR / "signals_*.csv")))  # fallback
    if output_files:
        return Path(output_files[-1])           # return most recent ETL output CSV

    return None                                 # no input files found; caller must handle this


def parse_args() -> argparse.Namespace:
    """Define and parse CLI arguments for standalone execution."""
    parser = argparse.ArgumentParser(
        prog        = "signal_detector.py",
        description = "Detect emerging market signals from keyword frequency data.",
        formatter_class = argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(                        # optional path to a specific CSV file
        "--csv",
        type    = Path,
        default = None,                         # if not provided, auto-discover latest file
        metavar = "FILE",
        help    = "Path to input CSV (default: auto-picks latest raw_trends_*.csv)",
    )
    parser.add_argument(                        # rolling window size for growth and MA
        "--window",
        type    = int,
        default = DEFAULT_WINDOW,               # 4 weeks ≈ 1 month
        metavar = "N",
        help    = f"Rolling window in periods for growth/MA (default: {DEFAULT_WINDOW})",
    )
    parser.add_argument(                        # z-score threshold for anomaly labelling
        "--threshold",
        type    = float,
        default = DEFAULT_THRESHOLD,
        metavar = "Z",
        help    = f"Z-score threshold for anomaly detection (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(                        # how many top signals to print
        "--top",
        type    = int,
        default = DEFAULT_TOP_N,
        metavar = "N",
        help    = f"Number of top signals to print (default: {DEFAULT_TOP_N})",
    )
    parser.add_argument(                        # suppress saving to disk (useful for testing)
        "--no-save",
        action  = "store_true",
        help    = "Run analysis without saving a report CSV",
    )

    return parser.parse_args()                  # parse sys.argv and return Namespace


if __name__ == "__main__":                      # only run when executed directly
    args = parse_args()                         # parse CLI arguments

    csv_path = args.csv or _find_latest_csv()   # use provided path or auto-discover

    if csv_path is None:                        # no CSV found anywhere — cannot proceed
        log.error(
            "No input CSV found. Run pipeline.py first, or specify --csv path/to/file.csv"
        )
        sys.exit(1)                             # non-zero exit code signals failure to the shell

    log.info("Input file: %s", csv_path)        # confirm which file will be processed

    detect_signals(                             # run the full detection pipeline
        csv_path  = csv_path,
        window    = args.window,
        threshold = args.threshold,
        top_n     = args.top,
        save      = not args.no_save,           # invert the --no-save flag
    )
