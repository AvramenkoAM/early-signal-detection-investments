"""
etl/transform.py — T in ETL.

Takes raw extracted data (from extract.py) and produces a clean pandas
DataFrame ready for loading into CSV and SQLite.

Pipeline:
    1. normalize_text      — lowercase + strip punctuation
    2. count_occurrences   — count how many texts mention each keyword
    3. compute_sentiment   — average VADER compound score per keyword
    4. build_signals_df    — assemble the full signals DataFrame (with weights + sentiment)
    5. deduplicate         — merge duplicate (keyword, source) rows by summing frequency
"""

import re                               # regular expressions for text normalisation
import logging                          # structured log messages
from datetime import datetime, timezone # UTC timestamps for every signal row

import pandas as pd                     # DataFrame construction and manipulation

import config                           # SIGNAL_KEYWORDS, SOURCE_WEIGHTS and other constants

# ── Module-level logger ────────────────────────────────────────────────────────
log = logging.getLogger(__name__)       # scoped to "etl.transform" in log output


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Text Normalisation
# ══════════════════════════════════════════════════════════════════════════════

def normalize_text(text: str) -> str:
    """
    Convert text to lowercase and remove all characters that are not
    letters, digits, or spaces. Collapses multiple spaces into one.

    Examples:
        "AI Agents (2024)!" → "ai agents 2024"
        "LLM/foundation model" → "llm foundation model"
    """
    text = text.lower()                 # case-fold: "LLM" → "llm", "AI" → "ai"
    text = re.sub(r"[^a-z0-9\s]", " ", text)   # replace non-alphanumeric chars with space
    text = re.sub(r"\s+", " ", text)    # collapse consecutive spaces into one
    text = text.strip()                 # remove leading/trailing whitespace
    return text                         # return cleaned string


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Keyword Frequency Counting
# ══════════════════════════════════════════════════════════════════════════════

def count_keyword_occurrences(
    texts: list[str],
    keywords: list[str],
) -> dict[str, int]:
    """
    Count how many text blobs in `texts` contain each keyword as a substring.
    Each text blob is counted at most ONCE per keyword (presence, not total count).

    Args:
        texts:    list of normalised article/repo text strings
        keywords: list of lowercase signal keywords to search for

    Returns:
        {keyword: number_of_texts_that_contain_it}
    """
    counts: dict[str, int] = {kw: 0 for kw in keywords}   # initialise all keywords at zero

    for text in texts:                  # iterate every text blob
        normalised = normalize_text(text)   # normalise before matching (ensures lowercase)
        for kw in keywords:             # check each keyword against this text
            if kw in normalised:        # substring match — handles multi-word keywords
                counts[kw] += 1         # increment: this text mentions this keyword

    return counts                       # {keyword: count_of_texts_mentioning_it}


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Sentiment Scoring (VADER)
# ══════════════════════════════════════════════════════════════════════════════

def compute_keyword_sentiment(texts: list[str], keyword: str) -> float:
    """
    Compute the average VADER compound sentiment for texts that mention `keyword`.

    VADER compound ranges from -1.0 (very negative) to +1.0 (very positive).
    Returns 0.0 if no texts mention the keyword, or if vaderSentiment is missing.

    Why VADER:
        - No model download required — runs offline after pip install
        - Designed for short social-media text (news headlines, Reddit titles)
        - Fast enough to run on hundreds of texts synchronously
    """
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # optional dependency
    except ImportError:
        log.debug("vaderSentiment not installed — sentiment will be 0.0 for all keywords.")
        return 0.0                          # graceful degradation: no sentiment analysis

    analyzer = SentimentIntensityAnalyzer() # single instance per call (lightweight)
    scores: list[float] = []                # accumulate compound scores for matching texts

    kw_lower = keyword.lower()              # normalize once before the loop for efficiency

    for text in texts:                      # iterate every text blob from this source
        if kw_lower not in text.lower():    # skip texts that don't mention this keyword
            continue
        compound = analyzer.polarity_scores(text)["compound"]  # -1.0 … +1.0
        scores.append(compound)             # collect this text's compound score

    if not scores:                          # keyword not found in any text
        return 0.0                          # neutral — no evidence either way

    return round(sum(scores) / len(scores), 4)  # arithmetic mean of all matching compound scores


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Signal Record Assembly
# ══════════════════════════════════════════════════════════════════════════════

def _trends_to_records(
    trends_data: dict[str, float],
) -> list[dict]:
    """
    Convert Google Trends {keyword: avg_score} dict into signal records.
    frequency = interest score (0–100 scale) multiplied by the source weight.
    Only includes keywords with a non-zero score.
    """
    records = []                        # accumulator for signal dicts
    ts      = datetime.now(timezone.utc).isoformat()  # UTC ISO timestamp for this run
    weight  = config.SOURCE_WEIGHTS.get("google_trends", 1.0)  # source importance multiplier

    for keyword, score in trends_data.items():  # iterate each keyword + its score
        if score <= 0:                  # skip keywords with zero interest (no signal)
            continue
        weighted_freq = int(round(score * weight))  # apply source weight to raw score
        records.append({                # build one signal record
            "keyword":   keyword.lower(),   # normalise keyword to lowercase for consistency
            "source":    "google_trends",   # fixed source label
            "frequency": weighted_freq,     # weighted frequency for fair cross-source comparison
            "sentiment": 0.0,               # no text available for Google Trends — neutral
            "timestamp": ts,               # when this record was created
        })

    return records                      # list of {keyword, source, frequency, sentiment, timestamp}


def _texts_to_records(
    texts: list[str],
    source_label: str,
    keywords: list[str],
) -> list[dict]:
    """
    Count keyword occurrences in a list of text blobs and return signal records.
    Applies a per-source weight to frequency and computes VADER sentiment.
    Only includes keywords that appear at least once.

    Args:
        texts:        list of article/repo text strings
        source_label: value to put in the "source" column (e.g. "github_trending")
        keywords:     SIGNAL_KEYWORDS from config
    """
    if not texts:                       # nothing to process — source returned empty data
        log.debug("No texts for source '%s', skipping.", source_label)
        return []                       # return empty list; caller handles gracefully

    counts = count_keyword_occurrences(texts, keywords)  # {keyword: count}
    ts     = datetime.now(timezone.utc).isoformat()      # UTC timestamp

    # Look up source weight; fall back to 1.0 for unknown sources or rss_* prefixes
    source_key = source_label if source_label in config.SOURCE_WEIGHTS else "rss"
    weight     = config.SOURCE_WEIGHTS.get(source_key, 1.0)  # importance multiplier

    records = []                        # accumulator
    for keyword, freq in counts.items():    # iterate each keyword's count
        if freq == 0:                   # skip keywords with zero mentions in this source
            continue
        weighted_freq = int(round(freq * weight))           # apply source importance weight
        sentiment     = compute_keyword_sentiment(texts, keyword)  # VADER avg compound score
        records.append({                # one record per keyword that appeared at least once
            "keyword":   keyword,       # already lowercase from SIGNAL_KEYWORDS list
            "source":    source_label,  # e.g. "github_trending" or "rss_techcrunch"
            "frequency": weighted_freq, # weighted count for cross-source fairness
            "sentiment": sentiment,     # -1.0 … +1.0 average VADER compound score
            "timestamp": ts,            # same timestamp for all records in this batch
        })

    return records                      # list of signal dicts for this source


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — Deduplicate and Assemble Final DataFrame
# ══════════════════════════════════════════════════════════════════════════════

def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge rows that share the same (keyword, source) pair by summing frequencies
    and averaging sentiment scores.

    Keeps the latest timestamp for each (keyword, source) group.
    """
    if df.empty:                        # nothing to deduplicate
        return df                       # return the empty DataFrame unchanged

    agg_dict = {                        # aggregation rules per column
        "frequency": ("frequency", "sum"),    # sum frequencies across duplicates
        "timestamp": ("timestamp", "max"),    # keep the most recent timestamp
    }
    if "sentiment" in df.columns:       # include sentiment only if the column exists
        agg_dict["sentiment"] = ("sentiment", "mean")  # average sentiment across duplicates

    deduped = (
        df.groupby(["keyword", "source"], as_index=False)   # group by identity columns
        .agg(**agg_dict)
    )

    log.debug(
        "Deduplicate: %d rows → %d rows after merging duplicates.",
        len(df), len(deduped),
    )

    return deduped                      # clean DataFrame with no (keyword, source) duplicates


def build_signals_df(raw: dict) -> pd.DataFrame:
    """
    Main transform function. Accepts the raw dict from extract.fetch_all()
    and returns a clean DataFrame with schema:
        keyword | source | frequency | sentiment | timestamp

    Args:
        raw: {
            "trends":  {kw: score},
            "github":  [texts],
            "rss":     {name: [texts]},
            "reddit":  [texts],
        }

    Returns:
        pd.DataFrame — one row per (keyword, source) signal
    """
    all_records: list[dict] = []        # collect records from all sources here

    # ── Google Trends ──────────────────────────────────────────────────────────
    trends_records = _trends_to_records(raw.get("trends", {}))  # convert trends scores
    all_records.extend(trends_records)  # add to master list
    log.info("Transform: %d records from Google Trends.", len(trends_records))

    # ── GitHub Trending ────────────────────────────────────────────────────────
    github_texts   = raw.get("github", [])                     # list of repo text blobs
    github_records = _texts_to_records(
        texts        = github_texts,
        source_label = "github_trending",                       # fixed label for this source
        keywords     = config.SIGNAL_KEYWORDS,
    )
    all_records.extend(github_records)
    log.info("Transform: %d records from GitHub Trending.", len(github_records))

    # ── RSS News Feeds ─────────────────────────────────────────────────────────
    rss_data  = raw.get("rss", {})      # {feed_name: [article texts]}
    rss_total = 0                       # counter for log message

    for feed_name, texts in rss_data.items():   # iterate each feed
        source_label = f"rss_{feed_name}"       # e.g. "rss_techcrunch", "rss_hackernews"
        feed_records = _texts_to_records(
            texts        = texts,
            source_label = source_label,        # unique label per feed for granular tracking
            keywords     = config.SIGNAL_KEYWORDS,
        )
        all_records.extend(feed_records)
        rss_total += len(feed_records)

    log.info("Transform: %d records from %d RSS feeds.", rss_total, len(rss_data))

    # ── Reddit ─────────────────────────────────────────────────────────────────
    reddit_texts   = raw.get("reddit", [])      # list of post text blobs
    reddit_records = _texts_to_records(
        texts        = reddit_texts,
        source_label = "reddit",                # fixed label for Reddit source
        keywords     = config.SIGNAL_KEYWORDS,
    )
    all_records.extend(reddit_records)
    log.info("Transform: %d records from Reddit.", len(reddit_records))

    # ── Build DataFrame ────────────────────────────────────────────────────────
    if not all_records:                 # all sources failed or returned no signal data
        log.warning("Transform: no records produced — returning empty DataFrame.")
        return pd.DataFrame(            # return empty but correctly-typed DataFrame
            columns=["keyword", "source", "frequency", "sentiment", "timestamp"]
        )

    df = pd.DataFrame(all_records)     # convert list of dicts to DataFrame
    df = deduplicate(df)               # merge any (keyword, source) duplicates

    df = df.sort_values(               # sort by frequency descending for easy scanning
        "frequency", ascending=False
    ).reset_index(drop=True)           # clean integer index after sorting

    log.info(
        "Transform complete: %d unique (keyword, source) signals.",
        len(df),
    )

    return df                          # final signals DataFrame ready for loading
