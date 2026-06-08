"""
dashboard.py — Streamlit dashboard for Early Signal Detection
=============================================================
Visualises the output of signal_detector.py and raw trends data.

Sections:
  1. Sidebar — run selector + keyword filter
  2. KPI cards — snapshot metrics
  3. Top signals table — anomalies highlighted in red
  4. Line chart — weekly keyword trends over time
  5. Bar chart — growth rate comparison across keywords
  6. Signal history — score evolution across all saved runs

Usage:
  streamlit run dashboard.py
"""

from __future__ import annotations               # X | Y type hints on Python 3.9

import glob                                       # pattern-based file discovery
import json                                       # watchlist persistence
from pathlib import Path                          # OS-agnostic path handling

import altair as alt                              # declarative chart library
import pandas as pd                               # data loading and wrangling
import streamlit as st                            # web dashboard framework

import config                                     # project paths (DATA_DIR, OUTPUT_DIR)


# ── Page configuration ────────────────────────────────────────────────────────

st.set_page_config(
    page_title = "Early Signal Detection",        # browser tab title
    page_icon  = "📡",                            # favicon emoji
    layout     = "wide",                          # use full browser width
    initial_sidebar_state = "expanded",           # sidebar open by default
)

# Global CSS overrides — muted header colour, tighter metric cards
st.markdown(
    """
    <style>
        .block-container { padding-top: 2rem; padding-bottom: 2rem; }
        [data-testid="stMetricLabel"] { font-size: 0.78rem; color: #888; }
        [data-testid="stMetricValue"] { font-size: 1.6rem; font-weight: 700; }
        h2 { margin-top: 0.5rem; margin-bottom: 0.2rem; }
    </style>
    """,
    unsafe_allow_html=True,                       # CSS injection requires this flag
)


# ══════════════════════════════════════════════════════════════════════════════
# 1. DATA LOADING — cached so Streamlit doesn't reload on every interaction
# ══════════════════════════════════════════════════════════════════════════════

def _list_report_files() -> list[Path]:
    """Return all signal report CSVs sorted newest-first."""
    files = sorted(                               # sort lexicographically (timestamp in name)
        glob.glob(str(config.OUTPUT_DIR / "signal_report_*.csv")),
        reverse=True,                             # newest first for the selectbox default
    )
    return [Path(f) for f in files]               # return as Path objects


@st.cache_data
def load_signals(path: str) -> pd.DataFrame:
    """Load a single signal report CSV."""
    df = pd.read_csv(path)
    df["is_anomaly"] = df["is_anomaly"].astype(bool)
    df["keyword"]    = df["keyword"].str.lower().str.strip()
    return df


# Map raw source labels → readable display names
_SOURCE_LABELS: dict[str, str] = {
    "google_trends":        "📈 Google Trends",
    "github_trending":      "🐙 GitHub",
    "reddit":               "👾 Reddit",
    "rss_techcrunch":       "📰 TechCrunch",
    "rss_hackernews":       "📰 Hacker News",
    "rss_the_verge":        "📰 The Verge",
    "rss_mit_tech_review":  "📰 MIT Tech Review",
    "rss_cnbc":             "📰 CNBC",
    "rss_yahoo_finance":    "📰 Yahoo Finance",
    "rss_entrepreneur":     "📰 Entrepreneur",
    "rss_hbr":              "📰 HBR",
    "rss_nature":           "📰 Nature",
    "rss_reuters_business": "📰 Reuters",
}

def _normalise_source(raw: str) -> str:
    """Map raw source label → readable display name."""
    if raw in _SOURCE_LABELS:
        return _SOURCE_LABELS[raw]
    if raw.startswith("rss_"):                    # unknown RSS feed — show cleaned name
        return f"📰 {raw.replace('rss_', '').replace('_', ' ').title()}"
    return raw.replace("_", " ").title()          # fallback: clean up underscores


@st.cache_data
def load_source_map() -> dict[str, set[str]]:
    """
    Load the latest ETL signals_*.csv that contains a 'source' column.
    Returns {keyword: {"Google Trends", "RSS", ...}}.
    Skips files from main.py which lack the source column.
    """
    files = sorted(glob.glob(str(config.OUTPUT_DIR / "signals_*.csv")), reverse=True)

    for path in files:                            # try newest first
        df = pd.read_csv(path)
        if "source" not in df.columns:            # skip main.py output (no source column)
            continue
        df["keyword"]  = df["keyword"].str.lower().str.strip()
        df["category"] = df["source"].apply(_normalise_source)

        source_map: dict[str, set[str]] = {}
        for _, row in df.iterrows():
            source_map.setdefault(row["keyword"], set()).add(row["category"])
        return source_map                         # return as soon as a valid file is found

    return {}                                     # no valid ETL file found


@st.cache_data                                    # cache history — only recomputes when files change
def load_history() -> pd.DataFrame:
    """
    Load ALL signal report CSVs and assemble a time-series history DataFrame.
    Schema: run_time | keyword | score | growth_rate | is_anomaly

    Used for the 'Signal History' chart that shows how scores evolved across runs.
    """
    files = _list_report_files()                  # all report files, newest first
    if not files:                                 # no history yet
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []              # accumulate one frame per run

    for path in files:                            # iterate every saved report
        try:
            df = pd.read_csv(path)               # load this run's report
            df["is_anomaly"] = df["is_anomaly"].astype(bool)

            # Extract run timestamp from filename: signal_report_YYYYMMDD_HHMMSS.csv
            stem     = path.stem                 # e.g. "signal_report_20260608_152934"
            ts_part  = stem.replace("signal_report_", "")   # "20260608_152934"
            run_time = pd.to_datetime(ts_part, format="%Y%m%d_%H%M%S")  # parse to datetime
            df["run_time"] = run_time            # add run timestamp column

            frames.append(df[["run_time", "keyword", "score", "growth_rate", "is_anomaly"]])
        except Exception:                        # skip malformed files silently
            continue

    if not frames:                               # all files were unreadable
        return pd.DataFrame()

    history = pd.concat(frames, ignore_index=True)   # merge all runs into one DataFrame
    history["keyword"] = history["keyword"].str.title()  # title-case for display
    return history                               # multi-run time-series DataFrame


@st.cache_data
def load_trends() -> pd.DataFrame:
    """
    Load ALL raw_trends_*.csv files and merge them into one wide DataFrame.
    Each file may have different keyword columns — outer join keeps all keywords.
    Columns are normalised to lowercase for consistent matching with sidebar filters.
    """
    files = sorted(glob.glob(str(config.DATA_DIR / "raw_trends_*.csv")))

    if not files:
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    for path in files:
        df = pd.read_csv(path, parse_dates=["date"])
        df = df.sort_values("date")
        df.columns = [c.lower() if c != "date" else c for c in df.columns]
        frames.append(df)

    if len(frames) == 1:
        return frames[0].reset_index(drop=True)

    # Merge all frames on date — outer join keeps every keyword column
    merged = frames[0]
    for df_next in frames[1:]:
        new_cols = [c for c in df_next.columns if c != "date" and c not in merged.columns]
        if new_cols:                              # only merge if new keywords exist
            merged = merged.merge(
                df_next[["date"] + new_cols],
                on  = "date",
                how = "outer",
            )

    merged = merged.sort_values("date").reset_index(drop=True)
    return merged                                 # wide format with ALL tracked keywords


# ══════════════════════════════════════════════════════════════════════════════
# 2. SIDEBAR — FILTERS
# ══════════════════════════════════════════════════════════════════════════════

def _sector_for(keyword: str) -> str:
    """Look up sector for a keyword; fallback to 'Other'."""
    return config.KEYWORD_SECTORS.get(keyword.lower(), "Other")  # case-insensitive lookup


def render_sidebar() -> tuple[pd.DataFrame, list[str]]:
    """
    Render sidebar: run selector + sector filter + keyword filter.
    Returns (signals_df, selected_keywords).
    """
    with st.sidebar:
        st.markdown("## 📡 Signal Radar")
        st.markdown(
            "Track emerging investment signals across AI, Energy, "
            "Biotech, Finance, and more."
        )
        st.divider()

        # ── Run selector ──────────────────────────────────────────────────────
        report_files = _list_report_files()

        if not report_files:
            st.error("No signal reports found. Run signal_detector.py first.")
            st.stop()

        file_labels  = [p.name for p in report_files]
        chosen_label = st.selectbox(
            label = "Report",
            options = file_labels,
            index   = 0,
            help    = "Choose a saved signal report to display.",
        )
        chosen_path = config.OUTPUT_DIR / chosen_label
        signals     = load_signals(str(chosen_path))

        # Attach sector to signals for filtering
        signals["sector"] = signals["keyword"].apply(_sector_for)

        st.divider()

        # ── Source filter ─────────────────────────────────────────────────────
        source_map  = load_source_map()
        all_sources = sorted({s for cats in source_map.values() for s in cats})
        all_sources = all_sources or ["📈 Google Trends", "🐙 GitHub", "📰 RSS"]

        c1, c2 = st.columns([3, 2])
        c1.markdown("**Джерела**")
        all_src = c2.checkbox("Всі", value=True, key="all_sources")

        selected_sources = st.multiselect(
            label   = "Джерела",
            options = all_sources,
            default = all_sources if all_src else [],
            label_visibility = "collapsed",
        )
        active_sources = set(selected_sources) if selected_sources else set(all_sources)

        # ── Sector filter ─────────────────────────────────────────────────────
        all_sectors = sorted(signals["sector"].unique().tolist())

        c1, c2 = st.columns([3, 2])
        c1.markdown("**Сектори**")
        all_sec = c2.checkbox("Всі", value=True, key="all_sectors")

        selected_sectors = st.multiselect(
            label   = "Сектори",
            options = all_sectors,
            default = all_sectors if all_sec else [],
            label_visibility = "collapsed",
        )
        active_sectors = selected_sectors if selected_sectors else all_sectors

        # ── Keyword filter ─────────────────────────────────────────────────────
        kw_in_source  = {kw for kw, cats in source_map.items() if cats & active_sources}
        kw_in_sectors = signals[signals["sector"].isin(active_sectors)]["keyword"].tolist()
        kw_filtered   = [kw for kw in kw_in_sectors if kw in kw_in_source] or kw_in_sectors

        c1, c2 = st.columns([3, 2])
        c1.markdown("**Keywords**")
        all_kw = c2.checkbox("Всі", value=True, key="all_keywords")

        selected_kw = st.multiselect(
            label   = "Keywords",
            options = kw_filtered,
            default = kw_filtered if all_kw else [],
            label_visibility = "collapsed",
        )

        st.divider()
        st.caption("Data sources")
        st.markdown("- 📈 Google Trends")
        st.markdown("- 🐙 GitHub Trending")
        st.markdown("- 📰 Reuters · CNBC · HBR · Nature")
        st.markdown("- 📰 TechCrunch · MIT Tech Review")

    final_kw = selected_kw if selected_kw else kw_in_sectors
    return signals, final_kw


# ══════════════════════════════════════════════════════════════════════════════
# 3. KPI CARDS
# ══════════════════════════════════════════════════════════════════════════════

def render_kpis(signals: pd.DataFrame, filename: str) -> None:
    """Render four KPI metric cards: total signals, anomalies, top keyword, top score."""
    total      = len(signals)                     # total number of tracked keywords
    anomalies  = int(signals["is_anomaly"].sum()) # count of anomaly-flagged rows
    top_row    = signals.iloc[0] if not signals.empty else None  # highest-scored signal

    top_kw     = top_row["keyword"].title() if top_row is not None else "—"  # display name
    top_score  = top_row["score"]           if top_row is not None else None  # numeric score

    col1, col2, col3, col4 = st.columns(4)        # four equal-width columns

    with col1:
        st.metric("Total Signals", total)         # total keyword count

    with col2:
        delta_label = f"{anomalies} flagged" if anomalies else "None"  # human-readable delta
        st.metric(
            "Anomalies",
            anomalies,
            delta       = delta_label,
            delta_color = "inverse",              # red for anomalies (higher = worse)
        )

    with col3:
        st.metric("Top Signal", top_kw)           # keyword with the highest score

    with col4:
        score_label = f"{top_score:+.1f}" if top_score is not None else "N/A"  # formatted score
        st.metric("Top Score", score_label)

    if filename:                                  # only show if filename was passed
        st.caption(f"Loaded from: `{filename}`")


# ══════════════════════════════════════════════════════════════════════════════
# 4. WATCHLIST — persistent keyword tracking
# ══════════════════════════════════════════════════════════════════════════════

_WATCHLIST_PATH = config.OUTPUT_DIR / "watchlist.json"  # persists between sessions


def load_watchlist() -> set[str]:
    """Load watched keywords from disk. Returns empty set if file doesn't exist."""
    if _WATCHLIST_PATH.exists():
        return set(json.loads(_WATCHLIST_PATH.read_text()))
    return set()


def save_watchlist(keywords: set[str]) -> None:
    """Persist watched keywords to disk."""
    _WATCHLIST_PATH.write_text(json.dumps(sorted(keywords)))


# ══════════════════════════════════════════════════════════════════════════════
# 5. SIGNALS TABLE
# ══════════════════════════════════════════════════════════════════════════════

def render_table(signals: pd.DataFrame, selected: list[str]) -> None:
    """
    Render an editable signals table with:
      - ⭐ Watch column — click to add keyword to watchlist (persisted to disk)
      - 🔴 Anomaly rows highlighted in red
      - Sector, growth rate, z-score, composite score columns
    """
    st.subheader("Top Signals")

    df       = signals[signals["keyword"].isin(selected)].copy()
    df       = df.reset_index(drop=True)
    watchlist = load_watchlist()                  # set of currently watched keywords

    # "Select all" toggle above the table
    select_all = st.checkbox(
        "Вибрати всі",
        value = len(watchlist) > 0 and all(k.lower() in watchlist for k in df["keyword"]),
        key   = "select_all_signals",
    )

    # Build editable DataFrame
    display = pd.DataFrame({
        "⭐":          df["keyword"].apply(
            lambda k: True if select_all else k.lower() in watchlist  # all or individual
        ),
        "Keyword":     df["keyword"].str.title(),
        "Sector":      df["keyword"].apply(_sector_for),
        "Frequency":   df["frequency"].astype(int),
        "Growth Rate": df["growth_rate"].apply(
            lambda x: f"{x:+.1f}%" if pd.notna(x) else "N/A"
        ),
        "Mov. Avg":    df["moving_avg"].round(1),
        "Z-Score":     df["z_score"].round(2),
        "Score":       df["score"].apply(
            lambda x: f"{x:+.1f}" if pd.notna(x) else "N/A"
        ),
        "🔴 Anomaly":  df["is_anomaly"],
    })

    edited = st.data_editor(                      # editable table — only ⭐ column is editable
        display,
        column_config={
            "⭐": st.column_config.CheckboxColumn(
                label  = "⭐",
                help   = "Add to watchlist",
                default = False,
            ),
            "🔴 Anomaly": st.column_config.CheckboxColumn(
                label   = "🔴",
                help    = "Anomaly detected",
                disabled = True,                  # read-only
            ),
            "Keyword":     st.column_config.TextColumn(disabled=True),
            "Sector":      st.column_config.TextColumn(disabled=True),
            "Frequency":   st.column_config.NumberColumn(disabled=True),
            "Growth Rate": st.column_config.TextColumn(disabled=True),
            "Mov. Avg":    st.column_config.NumberColumn(disabled=True),
            "Z-Score":     st.column_config.NumberColumn(disabled=True),
            "Score":       st.column_config.TextColumn(disabled=True),
        },
        use_container_width = True,
        hide_index          = False,
        height              = 320,
        key                 = "signals_table",
    )

    # Persist watchlist changes when user ticks/unticks
    new_watchlist: set[str] = set()
    for i, row in edited.iterrows():
        kw = display.at[i, "Keyword"].lower()     # map back to lowercase keyword
        if row["⭐"]:
            new_watchlist.add(kw)
    if new_watchlist != watchlist:                # only write if something changed
        save_watchlist(new_watchlist)
        st.toast(f"Watchlist updated — {len(new_watchlist)} keyword(s) watched", icon="⭐")

    # Show watchlist summary below table if non-empty
    if new_watchlist:
        st.caption(
            "⭐ Watching: " + " · ".join(k.title() for k in sorted(new_watchlist))
        )


# ══════════════════════════════════════════════════════════════════════════════
# 5. LINE CHART — KEYWORD TRENDS OVER TIME
# ══════════════════════════════════════════════════════════════════════════════

def render_trend_chart(trends: pd.DataFrame, selected: list[str]) -> None:
    """
    Render a multi-line Altair chart showing weekly keyword frequency over time.
    Only selected keywords are shown.
    """
    st.subheader("Keyword Trends Over Time")

    if trends.empty:                              # no raw data → show placeholder
        st.info("No raw trend data available. Run the ETL pipeline first.")
        return

    available_cols = [c for c in trends.columns if c != "date"]   # all keyword columns
    cols_to_show   = [                            # filter to selected keywords (case-insensitive)
        c for c in available_cols
        if c.lower() in [s.lower() for s in selected]
    ]

    if not cols_to_show:                          # selection produces no matching columns
        st.warning("No matching trend columns for the selected keywords.")
        return

    melted = (                                    # melt wide format → long for Altair
        trends[["date"] + cols_to_show]
        .melt(id_vars="date", var_name="keyword", value_name="value")
    )
    melted["keyword"] = melted["keyword"].str.title()  # title-case for legend labels

    line = (
        alt.Chart(melted)
        .mark_line(strokeWidth=2.5)               # smooth line, 2.5px stroke
        .encode(
            x = alt.X(
                "date:T",
                title  = None,                    # no axis title — date context is obvious
                axis   = alt.Axis(format="%b %Y", labelAngle=-30),  # "Jun 2025" format
            ),
            y = alt.Y(
                "value:Q",
                title = "Frequency (Google Trends index)",
                scale = alt.Scale(zero=False),    # don't force y-axis to start at 0
            ),
            color = alt.Color(                    # one colour per keyword
                "keyword:N",
                legend = alt.Legend(title=None, orient="bottom"),  # legend below chart
            ),
            tooltip = [                           # hover tooltip with all details
                alt.Tooltip("date:T",    title="Date",      format="%Y-%m-%d"),
                alt.Tooltip("keyword:N", title="Keyword"),
                alt.Tooltip("value:Q",   title="Frequency"),
            ],
        )
    )

    points = line.mark_point(size=40, filled=True)  # dot overlay for each data point

    st.altair_chart(
        (line + points)                           # combine line and point layers
        .properties(height=340)
        .interactive(),                           # enable pan and zoom
        use_container_width=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 6. BAR CHART — GROWTH RATE COMPARISON
# ══════════════════════════════════════════════════════════════════════════════

def render_growth_chart(signals: pd.DataFrame, selected: list[str]) -> None:
    """
    Render a horizontal bar chart.
    Uses growth_rate when available (time-series mode);
    falls back to z_score when all growth rates are N/A (snapshot mode).
    """
    df = signals[signals["keyword"].isin(selected)].copy()

    has_growth = df["growth_rate"].notna().any()  # True = time-series data available

    if has_growth:
        st.subheader("Growth Rate Comparison")
        df = df.dropna(subset=["growth_rate"])
        metric_col   = "growth_rate"
        metric_title = "Growth Rate (%)"
        fmt          = "+.0f"
        zero_ref     = 0
    else:
        st.subheader("Signal Strength (Z-Score)")  # fallback for snapshot data
        metric_col   = "z_score"
        metric_title = "Z-Score (vs avg)"
        fmt          = "+.2f"
        zero_ref     = 0

    if df.empty:
        st.info("No data available for the selected keywords.")
        return

    df["label"]  = df["keyword"].str.title()
    df["sector"] = df["keyword"].apply(_sector_for)   # sector for colour
    df["trend"]  = df["growth_rate"].apply(
        lambda x: "Growing 📈" if x >= 0 else "Declining 📉"
    )

    # Build colour scale from SECTOR_COLORS config
    sector_list   = list(config.SECTOR_COLORS.keys())
    sector_colors = list(config.SECTOR_COLORS.values())

    bars = (
        alt.Chart(df)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            x = alt.X(
                f"{metric_col}:Q",
                title = metric_title,
                axis  = alt.Axis(format=fmt),
            ),
            y = alt.Y(
                "label:N",
                sort  = "-x",
                title = None,
            ),
            color = alt.Color(
                "sector:N",
                scale  = alt.Scale(domain=sector_list, range=sector_colors),
                legend = alt.Legend(title="Sector", orient="bottom"),
            ),
            opacity = alt.condition(
                alt.datum[metric_col] >= 0,
                alt.value(1.0),
                alt.value(0.55),
            ),
            tooltip = [
                alt.Tooltip("label:N",           title="Keyword"),
                alt.Tooltip("sector:N",          title="Sector"),
                alt.Tooltip(f"{metric_col}:Q",   title=metric_title, format=fmt),
                alt.Tooltip("frequency:Q",       title="Frequency"),
            ],
        )
    )

    zero_line = (
        alt.Chart(pd.DataFrame({"x": [zero_ref]}))
        .mark_rule(color="#555", strokeDash=[5, 4], strokeWidth=1)
        .encode(x="x:Q")
    )

    st.altair_chart(
        (bars + zero_line).properties(height=280),
        use_container_width=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 7. SIGNAL HISTORY CHART
# ══════════════════════════════════════════════════════════════════════════════

def render_history(selected: list[str]) -> None:
    """
    Render a multi-line chart showing how each keyword's composite score
    evolved across all saved pipeline runs.

    Requires at least 2 saved reports to be meaningful.
    """
    st.subheader("Signal History Across Runs")

    history = load_history()                      # load all saved reports

    if history.empty:                             # no history yet
        st.info("No history data yet. Run signal_detector.py at least once.")
        return

    n_runs = history["run_time"].nunique()        # number of distinct pipeline runs

    if n_runs < 2:
        st.info(f"Тільки {n_runs} запуск збережено. Запусти pipeline ще раз щоб побачити динаміку.")
        return

    # Filter to selected keywords
    df = history[history["keyword"].str.lower().isin([k.lower() for k in selected])].copy()

    if df.empty:
        st.warning("No history data for the selected keywords.")
        return

    line = (
        alt.Chart(df)
        .mark_line(strokeWidth=2.5, point=alt.OverlayMarkDef(size=50, filled=True))
        .encode(
            x = alt.X(
                "run_time:T",
                title = "Run Date",
                axis  = alt.Axis(format="%Y-%m-%d %H:%M", labelAngle=-30),
            ),
            y = alt.Y(
                "score:Q",
                title = "Composite Score",
                scale = alt.Scale(zero=False),    # don't force y-axis to 0
            ),
            color = alt.Color(
                "keyword:N",
                legend = alt.Legend(title=None, orient="bottom"),
            ),
            strokeDash = alt.condition(           # dashed line for anomaly runs
                alt.datum.is_anomaly,
                alt.value([4, 4]),                # dashed = anomaly
                alt.value([1, 0]),                # solid = normal
            ),
            tooltip = [
                alt.Tooltip("run_time:T",    title="Run",    format="%Y-%m-%d %H:%M"),
                alt.Tooltip("keyword:N",     title="Keyword"),
                alt.Tooltip("score:Q",       title="Score",   format="+.1f"),
                alt.Tooltip("growth_rate:Q", title="Growth",  format="+.1f"),
                alt.Tooltip("is_anomaly:N",  title="Anomaly"),
            ],
        )
        .properties(height=300)
        .interactive()
    )

    st.altair_chart(line, use_container_width=True)  # full width

    st.caption(                                   # legend explanation below chart
        f"Based on {n_runs} saved runs.  "
        "Dashed segments = anomaly detected in that run."
    )


# ══════════════════════════════════════════════════════════════════════════════
# 8. SECTOR OVERVIEW — one tab per sector with trend + bar charts
# ══════════════════════════════════════════════════════════════════════════════

def render_sector_overview(signals: pd.DataFrame, trends: pd.DataFrame) -> None:
    """
    Render a tabbed section with one tab per sector.
    Each tab shows:
      - Line chart: Google Trends weekly data for keywords in this sector
      - Bar chart: Z-score / growth rate comparison within the sector
    """
    st.subheader("Огляд по секторах")

    signals = signals.copy()
    signals["sector"] = signals["keyword"].apply(_sector_for)

    # Only show sectors that have at least one keyword in current signals
    active_sectors = [
        s for s in config.SECTOR_COLORS
        if s in signals["sector"].values
    ]

    if not active_sectors:
        st.info("Немає даних по секторах.")
        return

    tabs = st.tabs(active_sectors)                # one tab per sector

    for tab, sector in zip(tabs, active_sectors):
        with tab:
            sector_kw = signals[signals["sector"] == sector]["keyword"].tolist()

            col_left, col_right = st.columns([3, 2])

            # ── Trend line ────────────────────────────────────────────────────
            with col_left:
                if not trends.empty:
                    trend_cols = [
                        c for c in trends.columns
                        if c != "date" and c.lower() in [k.lower() for k in sector_kw]
                    ]
                    if trend_cols:
                        melted = (
                            trends[["date"] + trend_cols]
                            .melt(id_vars="date", var_name="keyword", value_name="value")
                        )
                        melted["keyword"] = melted["keyword"].str.title()
                        color  = config.SECTOR_COLORS.get(sector, "#888")

                        chart = (
                            alt.Chart(melted)
                            .mark_line(strokeWidth=2)
                            .encode(
                                x = alt.X("date:T", title=None,
                                          axis=alt.Axis(format="%b %Y", labelAngle=-30)),
                                y = alt.Y("value:Q", title="Google Trends Index",
                                          scale=alt.Scale(zero=False)),
                                color = alt.Color("keyword:N",
                                    legend=alt.Legend(title=None, orient="bottom")),
                                tooltip=[
                                    alt.Tooltip("date:T", format="%Y-%m-%d"),
                                    alt.Tooltip("keyword:N"),
                                    alt.Tooltip("value:Q", title="Index"),
                                ],
                            )
                            .properties(height=220, title="Тренди Google")
                            .interactive()
                        )
                        st.altair_chart(chart, use_container_width=True)
                    else:
                        st.caption("Немає Google Trends даних для цього сектору.")
                else:
                    st.caption("Запусти main.py щоб отримати Google Trends дані.")

            # ── Z-score / growth bar ──────────────────────────────────────────
            with col_right:
                df_sec = signals[signals["sector"] == sector].copy()
                df_sec["label"] = df_sec["keyword"].str.title()

                has_growth = df_sec["growth_rate"].notna().any()
                metric_col   = "growth_rate" if has_growth else "z_score"
                metric_title = "Growth %" if has_growth else "Z-Score"
                fmt          = "+.0f" if has_growth else "+.2f"

                if not df_sec.empty:
                    color = config.SECTOR_COLORS.get(sector, "#888")
                    bars = (
                        alt.Chart(df_sec)
                        .mark_bar(cornerRadiusEnd=4, color=color)
                        .encode(
                            x = alt.X(f"{metric_col}:Q", title=metric_title,
                                      axis=alt.Axis(format=fmt)),
                            y = alt.Y("label:N", sort="-x", title=None),
                            opacity = alt.condition(
                                alt.datum[metric_col] >= 0,
                                alt.value(1.0), alt.value(0.5),
                            ),
                            tooltip=[
                                alt.Tooltip("label:N",           title="Keyword"),
                                alt.Tooltip(f"{metric_col}:Q",   title=metric_title, format=fmt),
                                alt.Tooltip("frequency:Q",       title="Frequency"),
                            ],
                        )
                        .properties(height=220, title=metric_title)
                    )
                    zero = (
                        alt.Chart(pd.DataFrame({"x": [0]}))
                        .mark_rule(color="#aaa", strokeDash=[4, 3])
                        .encode(x="x:Q")
                    )
                    st.altair_chart(bars + zero, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# 9. MAIN — wires all components together
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """App entry point. Renders sidebar, then all dashboard sections."""

    # ── Header ────────────────────────────────────────────────────────────────
    st.markdown("# 📡 Early Signal Detection")
    st.markdown("Tracking emerging trends across AI, robotics, and technology markets.")
    st.divider()

    # ── Sidebar: run selector + keyword filter ────────────────────────────────
    signals, selected = render_sidebar()          # returns (DataFrame, [keywords])

    # ── Load raw trends (always latest file) ─────────────────────────────────
    trends = load_trends()                        # weekly time-series for line chart

    # ── KPI cards ─────────────────────────────────────────────────────────────
    render_kpis(signals, "")                      # filename shown inside sidebar now
    st.divider()

    # ── Signals table ─────────────────────────────────────────────────────────
    render_table(signals, selected)
    st.divider()

    # ── Trend line + Growth bar ───────────────────────────────────────────────
    left, right = st.columns([3, 2])

    with left:
        render_trend_chart(trends, selected)

    with right:
        render_growth_chart(signals, selected)

    st.divider()

    # ── Sector overview — tabs per sector ─────────────────────────────────────
    render_sector_overview(signals, trends)
    st.divider()

    # ── Signal history across runs ────────────────────────────────────────────
    render_history(selected)


if __name__ == "__main__":
    main()                                        # entry point for `streamlit run dashboard.py`
