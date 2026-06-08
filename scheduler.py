"""
scheduler.py — Automated daily pipeline runner.
================================================
Uses APScheduler to run the full pipeline on a cron schedule:
    1. ETL pipeline  (pipeline.py)   — collect data from all sources
    2. Signal detector (signal_detector.py) — compute growth, anomalies
    3. Email alert   (notifier.py)   — send if anomalies were detected

Default schedule: every day at 08:00 local time (configurable via .env).

Usage:
    python scheduler.py                  # start the scheduler (blocking)
    python scheduler.py --run-now        # run immediately, then start schedule
    python scheduler.py --hour 9 --minute 30   # override schedule time
"""

from __future__ import annotations          # X | Y type hints on Python 3.9

import sys                                  # sys.exit for fatal errors
import logging                              # structured log messages
import argparse                             # CLI argument parsing
from pathlib import Path                    # path to latest CSV

import config                               # paths, schedule config


# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level   = logging.INFO,
    format  = config.LOG_FORMAT,
    datefmt = config.LOG_DATEFMT,
)
log = logging.getLogger(__name__)           # scoped to "scheduler" in log output


# ══════════════════════════════════════════════════════════════════════════════
# 1. PIPELINE JOB — runs as a single unit on each scheduled tick
# ══════════════════════════════════════════════════════════════════════════════

def run_pipeline_job() -> None:
    """
    Execute the full pipeline in sequence:
        ETL → signal detection → email alert (if anomalies).
    Errors in any stage are logged but do not crash the scheduler.
    """
    log.info("═" * 60)
    log.info("Scheduled job starting …")

    # ── Step 1: ETL ────────────────────────────────────────────────────────────
    log.info("Step 1/3 — Running ETL pipeline …")
    try:
        from etl.extract   import fetch_all         # E — collect raw data
        from etl.transform import build_signals_df  # T — clean and count
        from etl.load      import save_all          # L — write CSV + SQLite

        raw        = fetch_all()                    # collect from all configured sources
        signals_df = build_signals_df(raw)          # transform to signals DataFrame

        if signals_df.empty:                        # no data — abort this run gracefully
            log.error("ETL produced no signals — skipping detection and alert.")
            return                                  # nothing to analyse or report

        save_all(                                   # persist to CSV and SQLite
            df         = signals_df,
            output_dir = config.OUTPUT_DIR,
            db_path    = config.DB_PATH,
        )
        log.info("ETL complete: %d signals collected.", len(signals_df))

    except Exception as exc:                        # catch import errors, network failures, etc.
        log.error("ETL step failed: %s", exc, exc_info=True)
        return                                      # abort this run — no point detecting on old data

    # ── Step 2: Signal detection ───────────────────────────────────────────────
    log.info("Step 2/3 — Running signal detection …")
    try:
        import glob                                 # find latest raw trends CSV
        from signal_detector import detect_signals  # statistical analysis module

        raw_files = sorted(                         # find all raw trend CSVs
            glob.glob(str(config.DATA_DIR / "raw_trends_*.csv"))
        )

        if not raw_files:                           # no raw CSV — ETL may have only written snapshots
            log.warning("No raw_trends CSV found — skipping signal detection.")
            return

        latest_csv = Path(raw_files[-1])            # pick the most recent file
        report_df  = detect_signals(                # run the full statistical pipeline
            csv_path  = latest_csv,
            threshold = config.ANOMALY_THRESHOLD,   # use global threshold from config
            save      = True,                       # write signal_report_*.csv to output/
        )
        log.info(
            "Detection complete: %d signals, %d anomalies.",
            len(report_df),
            int(report_df["is_anomaly"].sum()),
        )

    except Exception as exc:
        log.error("Signal detection step failed: %s", exc, exc_info=True)
        return                                      # skip alert if detection failed

    # ── Step 3: Email alert ─────────────────────────────────────────────────────
    log.info("Step 3/3 — Sending email alert (if anomalies found) …")
    try:
        from notifier import send_anomaly_alert     # HTML email sender

        sent = send_anomaly_alert(report_df)        # sends only if anomalies exist and email is enabled
        if sent:
            log.info("Alert email delivered.")
        else:
            log.info("No alert sent (no anomalies, or email not enabled).")

    except Exception as exc:
        log.error("Email alert step failed: %s", exc, exc_info=True)
        # don't return here — pipeline completed successfully even if email failed

    log.info("Scheduled job finished.")
    log.info("═" * 60)


# ══════════════════════════════════════════════════════════════════════════════
# 2. CLI ARGUMENT PARSER
# ══════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    """Define and parse CLI arguments for the scheduler."""
    parser = argparse.ArgumentParser(
        prog        = "scheduler.py",
        description = (
            "Start the automated daily pipeline. "
            "Use --run-now to trigger an immediate run before entering the schedule."
        ),
    )
    parser.add_argument(                        # trigger one immediate run at startup
        "--run-now",
        action = "store_true",
        help   = "Run the pipeline immediately before entering the schedule loop.",
    )
    parser.add_argument(                        # override scheduled hour
        "--hour",
        type    = int,
        default = config.SCHEDULER_HOUR,        # default from config / .env
        metavar = "H",
        help    = f"Hour to run daily (0–23, default: {config.SCHEDULER_HOUR})",
    )
    parser.add_argument(                        # override scheduled minute
        "--minute",
        type    = int,
        default = config.SCHEDULER_MINUTE,
        metavar = "M",
        help    = f"Minute to run daily (0–59, default: {config.SCHEDULER_MINUTE})",
    )
    return parser.parse_args()


# ══════════════════════════════════════════════════════════════════════════════
# 3. ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """Set up APScheduler and start the blocking event loop."""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler  # blocking = keeps process alive
        from apscheduler.triggers.cron       import CronTrigger        # cron-style scheduling
    except ImportError:
        log.error(
            "APScheduler not installed. Run: pip install apscheduler"
        )
        sys.exit(1)                             # can't start without the scheduler library

    args      = parse_args()                    # parse CLI args
    scheduler = BlockingScheduler(              # blocking scheduler keeps the process running
        timezone = "Europe/Kyiv"                # Kyiv timezone for cron schedule
    )

    scheduler.add_job(                          # register the pipeline job
        run_pipeline_job,                       # function to call on each tick
        trigger = CronTrigger(                  # cron-based trigger
            hour   = args.hour,                 # e.g. 8
            minute = args.minute,               # e.g. 0  → runs at 08:00 daily
        ),
        id              = "daily_pipeline",     # unique job ID for logging and management
        name            = "Early Signal Detection — Daily Run",
        misfire_grace_time = 60 * 10,           # allow up to 10 min late start (e.g. after sleep)
        coalesce        = True,                 # if multiple runs were missed, fire only once
    )

    log.info(
        "Scheduler started. Pipeline will run daily at %02d:%02d.",
        args.hour, args.minute,
    )

    if args.run_now:                            # --run-now: fire immediately before entering loop
        log.info("--run-now flag set: executing pipeline now …")
        run_pipeline_job()                      # synchronous immediate run

    try:
        scheduler.start()                       # enter blocking loop — Ctrl+C to stop
    except (KeyboardInterrupt, SystemExit):     # graceful shutdown on Ctrl+C
        log.info("Scheduler stopped by user.")
        scheduler.shutdown(wait=False)          # stop without waiting for running jobs


if __name__ == "__main__":
    main()                                      # entry point: python scheduler.py
