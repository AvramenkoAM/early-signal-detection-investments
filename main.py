"""
Early Signal Detection System for Investments
----------------------------------------------
Collects Google Trends interest-over-time data for a set of technology
keywords and calculates growth rates to surface the fastest-rising topics.

Usage:
    python main.py

Output:
    output/signals.csv   – ranked growth-rate table
    data/raw_trends.csv  – raw weekly Google Trends data
"""

import sys                          # provides access to interpreter-level operations
import time                         # used for sleep between API requests (rate-limit safety)
import logging                      # structured log output instead of raw print statements
from datetime import datetime       # for timestamping saved files and log messages
from pathlib import Path            # OS-agnostic path construction

import pandas as pd                 # core data manipulation library
from pytrends.request import TrendReq  # Google Trends unofficial API wrapper


# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(                # configure the root logger once, at module level
    level=logging.INFO,             # show INFO and above (INFO, WARNING, ERROR, CRITICAL)
    format="%(asctime)s  %(levelname)-8s  %(message)s",  # timestamp + level + message
    datefmt="%Y-%m-%d %H:%M:%S",   # human-readable date format for log lines
)
log = logging.getLogger(__name__)   # logger scoped to this module (shows "main" in output)


# ── Configuration — sourced from config.py ────────────────────────────────────
import config as _cfg

KEYWORDS        = _cfg.TREND_KEYWORDS   # all tracked keywords across sectors
TIMEFRAME       = _cfg.TRENDS_TIMEFRAME # rolling 12-month window
GEO             = _cfg.TRENDS_GEO       # worldwide
SLEEP_SECONDS   = _cfg.TRENDS_SLEEP_SEC # seconds between batch API calls
RAW_PATH        = _cfg.DATA_DIR         # data/ folder
OUTPUT_PATH     = _cfg.OUTPUT_DIR       # output/ folder


# ── Data collection ────────────────────────────────────────────────────────────
def build_pytrends_client() -> TrendReq:
    """Create and return a configured pytrends session."""
    client = TrendReq(              # instantiate the Google Trends client
        hl="en-US",                 # language for results (affects category/topic labels)
        tz=0,                       # timezone offset from UTC in minutes (0 = UTC)
        timeout=(10, 30),           # (connect_timeout, read_timeout) in seconds
        retries=3,                  # number of automatic retries on network errors
        backoff_factor=0.5,         # exponential back-off multiplier between retries
    )
    return client                   # return the configured client to the caller


def fetch_trends(client: TrendReq, keywords: list[str]) -> pd.DataFrame:
    """
    Fetch weekly interest-over-time data for all keywords from Google Trends.
    Processes keywords in batches of 5 (API hard limit) and merges results.
    Returns a wide DataFrame indexed by date with one column per keyword.
    """
    batches = [keywords[i:i + 5] for i in range(0, len(keywords), 5)]  # split into groups of 5
    log.info(
        "Fetching Google Trends: %d keywords in %d batch(es) …",
        len(keywords), len(batches),
    )

    frames: list[pd.DataFrame] = []  # accumulate one DataFrame per batch

    for i, batch in enumerate(batches, 1):
        log.info("Batch %d/%d: %s", i, len(batches), batch)
        try:
            client.build_payload(kw_list=batch, timeframe=TIMEFRAME, geo=GEO)
            time.sleep(SLEEP_SECONDS)               # respect rate limit between batches
            df_batch = client.interest_over_time()
            if df_batch.empty:
                log.warning("Batch %d returned empty data — skipping.", i)
                continue
            df_batch = df_batch.drop(columns=["isPartial"], errors="ignore")
            frames.append(df_batch)                 # add this batch to results
            log.info("Batch %d: %d weeks × %d keywords", i, len(df_batch), len(batch))
        except Exception as exc:
            log.warning("Batch %d failed: %s — skipping.", i, exc)

    if not frames:
        raise ValueError("All Google Trends batches failed — try again later.")

    # Merge all batches on the date index
    result = frames[0]
    for df_next in frames[1:]:
        result = result.join(df_next, how="outer")  # outer join preserves all dates

    log.info("Trends fetch complete: %d weeks × %d keywords", len(result), len(result.columns))
    return result


# ── Processing ─────────────────────────────────────────────────────────────────
def compute_growth_rates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate growth rate for each keyword by comparing the last 30 days
    to the previous 30 days.

    Growth rate = (last_30_avg - prev_30_avg) / prev_30_avg * 100

    Returns a DataFrame with columns:
        keyword | last_30_avg | prev_30_avg | growth_rate
    """
    df = df.sort_index()            # ensure rows are in chronological order before slicing

    last_30  = df.tail(4)           # last ~4 weekly rows ≈ last 30 days of data
    prev_30  = df.iloc[-8:-4]       # 4 rows before that  ≈ prior 30-day window

    rows = []                       # accumulator for per-keyword result rows

    for keyword in df.columns:      # iterate over each tracked keyword
        last_avg = last_30[keyword].mean()   # average interest score for the recent window
        prev_avg = prev_30[keyword].mean()   # average interest score for the comparison window

        if prev_avg == 0:           # guard against division by zero (keyword had no activity)
            growth = float("nan")   # NaN signals "undefined" rather than crashing
            log.warning("Keyword '%s' had zero interest in the previous window.", keyword)
        else:
            growth = (last_avg - prev_avg) / prev_avg * 100  # percentage change formula

        rows.append({               # append a result dict for this keyword
            "keyword":      keyword,          # keyword label
            "last_30_avg":  round(last_avg, 2),  # rounded average for readability
            "prev_30_avg":  round(prev_avg, 2),
            "growth_rate":  round(growth, 2) if not pd.isna(growth) else None,
        })

    result = pd.DataFrame(rows)     # convert list of dicts to a tidy DataFrame

    result = result.sort_values(    # rank by growth rate, highest first
        "growth_rate",
        ascending=False,
        na_position="last",         # push NaN rows to the bottom of the ranking
    ).reset_index(drop=True)        # clean integer index after sorting

    return result                   # return the sorted growth-rate table


# ── Persistence ────────────────────────────────────────────────────────────────
def save_raw(df: pd.DataFrame) -> Path:
    """Save the raw weekly Trends data to data/ with a timestamp in the filename."""
    RAW_PATH.mkdir(parents=True, exist_ok=True)     # create data/ if it doesn't exist yet

    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")  # compact timestamp for unique filenames
    path = RAW_PATH / f"raw_trends_{ts}.csv"          # full path for the raw file

    df.to_csv(path)                 # write the DataFrame including the date index
    log.info("Raw data saved → %s", path)  # confirm the file location to the operator
    return path                     # return the path so callers can reference it


def save_signals(df: pd.DataFrame) -> Path:
    """Save the processed growth-rate signals to output/ with a timestamp."""
    OUTPUT_PATH.mkdir(parents=True, exist_ok=True)  # create output/ if needed

    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")  # same timestamp pattern for pairing files
    path = OUTPUT_PATH / f"signals_{ts}.csv"          # distinct prefix for processed output

    df.to_csv(path, index=False)    # no row numbers — the table has its own keyword index
    log.info("Signals saved     → %s", path)  # confirm output location
    return path                     # return path for the summary print


# ── Reporting ──────────────────────────────────────────────────────────────────
def print_top_signals(df: pd.DataFrame, n: int = 5) -> None:
    """Print the top-N fastest growing keywords to stdout in a readable format."""
    top = df.dropna(subset=["growth_rate"]).head(n)  # drop undefined rows, take first n

    print()                                          # blank line before the report
    print("=" * 52)
    print(f"  TOP {n} FASTEST-GROWING INVESTMENT SIGNALS")
    print("=" * 52)
    print(f"  {'Rank':<5} {'Keyword':<20} {'Growth':>8}  {'Last30':>7}  {'Prev30':>7}")
    print("-" * 52)

    for rank, (_, row) in enumerate(top.iterrows(), start=1):  # enumerate for 1-based rank
        growth_str = f"{row['growth_rate']:+.1f}%"  # + sign for positive, – for negative
        print(
            f"  {rank:<5} "                          # rank column
            f"{row['keyword']:<20} "                  # keyword padded to 20 chars
            f"{growth_str:>8}  "                      # growth rate right-aligned
            f"{row['last_30_avg']:>7.1f}  "           # recent average interest score
            f"{row['prev_30_avg']:>7.1f}"             # prior average interest score
        )

    print("=" * 52)
    print()                                          # trailing blank line


# ── Orchestration ──────────────────────────────────────────────────────────────
def run() -> None:
    """
    Main pipeline:
    1. Connect to Google Trends
    2. Fetch interest-over-time data
    3. Compute growth rates
    4. Save raw and processed outputs
    5. Print top signals
    """
    log.info("Starting Early Signal Detection System")  # announce pipeline start

    try:
        client = build_pytrends_client()           # step 1: build the HTTP session
        raw_df = fetch_trends(client, KEYWORDS)    # step 2: pull data from Google Trends

    except Exception as exc:                       # catch network errors, rate limits, etc.
        log.error("Failed to fetch Google Trends data: %s", exc)  # log the error message
        sys.exit(1)                                # exit with non-zero code to signal failure

    try:
        signals_df = compute_growth_rates(raw_df)  # step 3: calculate growth rates

    except Exception as exc:                       # catch unexpected processing errors
        log.error("Failed to compute growth rates: %s", exc)
        sys.exit(1)

    save_raw(raw_df)                               # step 4a: persist raw time-series
    save_signals(signals_df)                       # step 4b: persist processed signals

    print_top_signals(signals_df, n=5)             # step 5: display top results in console

    log.info("Pipeline complete. %d keywords analysed.", len(signals_df))  # final status


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":          # run only when executed directly, not when imported
    run()                           # kick off the full pipeline
