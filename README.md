# 📡 Early Signal Detection System

> Identify emerging technology trends before they go mainstream — using Google Trends, GitHub, and news feeds.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-1.35%2B-FF4B4B?logo=streamlit&logoColor=white)
![Pandas](https://img.shields.io/badge/Pandas-2.0%2B-150458?logo=pandas&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-embedded-003B57?logo=sqlite&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Overview

The Early Signal Detection System is a data pipeline that monitors three live sources — **Google Trends**, **GitHub Trending**, and **technology RSS feeds** — and surfaces keywords that are growing faster than the baseline.

It computes growth rate, moving average, and z-score anomaly detection for each keyword, ranks them by a composite score, and presents the results in an interactive Streamlit dashboard.

**Designed for:** investors, researchers, and analysts who want a quantitative read on what's gaining traction in AI and tech — before it shows up in headlines.

---

## Problem It Solves

Spotting emerging technology trends requires monitoring multiple sources simultaneously. Doing this manually is slow and biased.

This system automates:
- Multi-source data collection (trends + code + media)
- Statistical signal ranking with noise filtering
- Anomaly detection to flag sudden spikes
- A visual dashboard for exploratory analysis

The result is a ranked list of keywords with evidence of acceleration — not just popularity.

---

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| Data collection | `pytrends`, `requests`, `BeautifulSoup` | Google Trends API, GitHub scraper, RSS parser |
| Storage | `pandas`, `SQLite` (`sqlite3`) | CSV snapshots + persistent queryable database |
| Signal analysis | `numpy`, `pandas` | Growth rate, moving average, z-score, anomaly detection |
| Dashboard | `Streamlit`, `Altair` | Interactive analytics UI |
| Configuration | `config.py` | Single source of truth for all constants |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        DATA SOURCES                         │
│                                                             │
│  📈 Google Trends    🐙 GitHub Trending    📰 RSS Feeds     │
└──────────────┬──────────────┬──────────────┬───────────────┘
               │              │              │
               ▼              ▼              ▼
┌─────────────────────────────────────────────────────────────┐
│                    EXTRACT  (etl/extract.py)                │
│   fetch_google_trends()  scrape_github()  fetch_rss_feeds() │
└──────────────────────────┬──────────────────────────────────┘
                           │  raw dict {source → texts/scores}
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                  TRANSFORM  (etl/transform.py)              │
│   normalize_text()  →  count_keyword_occurrences()          │
│   build_signals_df()  →  deduplicate()                      │
└──────────────────────────┬──────────────────────────────────┘
                           │  DataFrame: keyword | source | frequency
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    LOAD  (etl/load.py)                      │
│         save_to_csv()           save_to_sqlite()            │
│      output/signals_*.csv      output/signals.db            │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│               SIGNAL ANALYSIS  (signal_detector.py)         │
│                                                             │
│   growth_rate = (last_N_avg − prev_N_avg) / prev_N_avg      │
│   moving_avg  = rolling mean over N periods                 │
│   z_score     = (value − mean) / std                       │
│   score       = growth_rate × log1p(frequency)              │
│   is_anomaly  = z_score > threshold                         │
└──────────────────────────┬──────────────────────────────────┘
                           │  output/signal_report_*.csv
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                  DASHBOARD  (dashboard.py)                  │
│                                                             │
│   KPI cards  │  Signals table  │  Trend chart  │  Bar chart │
│                   Streamlit + Altair                         │
└─────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
06_early_signal_detection_system/
│
├── config.py               # All constants: paths, keywords, API settings
│
├── etl/
│   ├── extract.py          # E — Google Trends, GitHub, RSS extractors
│   ├── transform.py        # T — text normalisation, keyword frequency counting
│   └── load.py             # L — CSV and SQLite writer
│
├── pipeline.py             # ETL orchestrator + CLI entry point
├── main.py                 # Standalone Google Trends fetcher (quick run)
├── signal_detector.py      # Statistical analysis: growth, z-score, anomaly
├── dashboard.py            # Streamlit dashboard
│
├── data/
│   └── raw_trends_*.csv    # Raw weekly time-series from Google Trends
│
├── output/
│   ├── signals_*.csv       # ETL output: keyword | source | frequency
│   ├── signal_report_*.csv # Analysis output: growth | score | is_anomaly
│   └── signals.db          # SQLite database (persistent across runs)
│
└── requirements.txt
```

---

## How to Run

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Run the full ETL pipeline

Collects data from all three sources and saves to CSV + SQLite:

```bash
python pipeline.py
```

Optional flags:

```bash
python pipeline.py --sources trends github   # skip RSS
python pipeline.py --top 10                  # show top 10 in console
python pipeline.py --debug                   # verbose logging
```

### 3. Run signal detection

Reads the latest CSV from `data/` and computes growth rates, moving averages, and anomaly scores:

```bash
python signal_detector.py
```

Optional flags:

```bash
python signal_detector.py --window 8         # 8-week rolling window
python signal_detector.py --threshold 2.0    # stricter anomaly detection
python signal_detector.py --top 5            # show top 5 signals
python signal_detector.py --csv path/to/file.csv  # use a specific file
```

### 4. Launch the dashboard

```bash
streamlit run dashboard.py
```

Opens at **http://localhost:8501**

---

## Example Output

### Console — `signal_detector.py`

```
════════════════════════════════════════════════════════════════════════
  TOP 10 EMERGING SIGNALS
════════════════════════════════════════════════════════════════════════
  #   Keyword                  Freq   Growth  MovAvg      Z    Score  Anomaly
  --------------------------------------------------------------------
  1   LLM                        45   +12.4%    60.5   1.82   +54.3  🔴
  2   AI Agents                  38   +28.6%    30.0   2.14   +89.7  🔴
  3   Robotics                   24    +5.0%    34.0   0.72   +16.1
  4   Computer Vision             6    -2.1%     8.2  -0.36    -5.2
════════════════════════════════════════════════════════════════════════
  Total signals: 4  |  Anomalies: 2
```

### Output CSV — `output/signal_report_*.csv`

| keyword | frequency | moving_avg | growth_rate | z_score | score | is_anomaly |
|---|---|---|---|---|---|---|
| llm | 45.0 | 60.5 | +12.4 | 1.82 | +54.3 | True |
| ai agents | 38.0 | 30.0 | +28.6 | 2.14 | +89.7 | True |
| robotics | 24.0 | 34.0 | +5.0 | 0.72 | +16.1 | False |
| computer vision | 6.0 | 8.2 | -2.1 | -0.36 | -5.2 | False |

### Dashboard

```
┌──────────────────────────────────────────────────────────────┐
│  Total Signals: 4   Anomalies: 2   Top: LLM   Score: +54.3  │
├──────────────────────────────────────────────────────────────┤
│  Signals Table (anomalies in red)                            │
│  Keyword Trends — line chart (weekly, 52 weeks)              │
│  Growth Rate — bar chart (green / red by sign)               │
│  Sidebar: keyword multiselect filter                         │
└──────────────────────────────────────────────────────────────┘
```

---

## Signal Scoring

Each keyword is scored with a composite formula that rewards both **speed** and **volume**:

```
score = growth_rate × log1p(frequency)
```

- `growth_rate` — percentage change between the last N weeks and the prior N weeks
- `log1p(frequency)` — dampens very high-frequency keywords so low-volume spikes can compete
- `z_score > 1.5` — flags the keyword as an anomaly (sudden spike above historical baseline)

---

## Future Improvements

| Priority | Improvement |
|---|---|
| High | Add Twitter/X API as a 4th signal source |
| High | Scheduled daily runs via cron or Airflow |
| Medium | Email/Telegram alert when a new anomaly is detected |
| Medium | Historical database querying — show signal history per keyword |
| Medium | Configurable keyword list via CLI or UI |
| Low | Sector tagging (AI / robotics / biotech / energy) |
| Low | Correlation analysis — detect when signals move together |
| Low | Docker container for one-command deployment |

---

## Author

**Andrii Avramenko** — Data Analyst / ML Engineer

Built as part of a data analyst portfolio focused on automated signal intelligence and market research tooling.
