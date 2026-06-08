"""
etl/extract.py — E in ETL.

Four independent extractors, each returning a normalised raw-data dict:
    fetch_google_trends  → {keyword: avg_interest_score}
    scrape_github        → list of text strings (repo names + descriptions)
    fetch_rss_feeds      → {source_name: [text strings]}
    fetch_reddit         → list of text strings (post titles + bodies)

Public API:
    fetch_all(sources) → dict bundling all results
"""

from __future__ import annotations      # enables X | Y union type hints on Python 3.9

import time                             # pausing between API calls to respect rate limits
import logging                          # structured log messages instead of bare print()

import warnings                          # suppress noisy but harmless BS4 XML-as-HTML warning
import requests                         # HTTP client for scraping and RSS fetching
from bs4 import BeautifulSoup           # HTML/XML parser for GitHub and RSS content
from bs4 import XMLParsedAsHTMLWarning  # specific warning class to filter cleanly

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)  # silence RSS XML warning

import config                           # project-level configuration constants

# ── Module-level logger ────────────────────────────────────────────────────────
log = logging.getLogger(__name__)       # scoped to "etl.extract" in log output


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 1 — Google Trends
# ══════════════════════════════════════════════════════════════════════════════

def _trends_batch(
    client,
    batch: list[str],
    timeframe: str,
    geo: str,
    sleep_sec: int,
) -> dict[str, list[float]]:
    """
    Fetch a single batch of ≤5 keywords from Google Trends.
    Returns {keyword: [weekly_values]} time-series dict, or {} on failure.
    """
    try:
        client.build_payload(           # configure the query for this batch
            kw_list   = batch,          # max 5 keywords per request
            timeframe = timeframe,
            geo       = geo,
        )
        time.sleep(sleep_sec)           # respect rate limit between requests

        df = client.interest_over_time()
        if df.empty:
            log.warning("Google Trends: empty response for batch %s", batch)
            return {}

        df = df.drop(columns=["isPartial"], errors="ignore")
        return {kw: df[kw].tolist() for kw in batch if kw in df.columns}

    except Exception as exc:
        log.warning("Google Trends batch %s failed: %s", batch, exc)
        return {}                       # skip this batch, continue with others


def fetch_google_trends(
    keywords: list[str],
    timeframe: str,
    geo: str,
    sleep_sec: int,
) -> dict[str, float]:
    """
    Query Google Trends for a list of keywords in batches of 5 (API limit).
    Returns {keyword: avg_interest_last_4_weeks} for all successfully fetched keywords.

    Batching allows tracking many keywords across different sectors.
    Each batch is an independent request; scores within a batch are comparable (0-100).
    """
    try:
        from pytrends.request import TrendReq
    except ImportError:
        log.error("pytrends is not installed — skipping Google Trends source.")
        return {}

    if not keywords:
        return {}

    # Split keywords into batches of 5 (pytrends hard limit per request)
    batches = [keywords[i:i + 5] for i in range(0, len(keywords), 5)]
    log.info(
        "Google Trends: querying %d keywords in %d batch(es) …",
        len(keywords), len(batches),
    )

    try:
        client = TrendReq(
            hl            = "en-US",
            tz            = 0,
            timeout       = (10, 30),
            retries       = 3,
            backoff_factor = 0.5,
        )
    except Exception as exc:
        log.error("Google Trends: failed to create client: %s", exc)
        return {}

    all_series: dict[str, list[float]] = {}  # {keyword: [weekly values]}

    for i, batch in enumerate(batches, 1):
        log.info("Google Trends: batch %d/%d — %s", i, len(batches), batch)
        series = _trends_batch(client, batch, timeframe, geo, sleep_sec)
        all_series.update(series)        # merge batch results

    if not all_series:
        log.warning("Google Trends: all batches returned empty data.")
        return {}

    # Compute average of last 4 weeks for each keyword
    result: dict[str, float] = {}
    for kw, values in all_series.items():
        last_4 = values[-4:] if len(values) >= 4 else values  # last ~30 days
        if last_4:
            result[kw] = round(sum(last_4) / len(last_4), 2)

    log.info("Google Trends: received scores for %d keywords.", len(result))
    return result


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 2 — GitHub Trending
# ══════════════════════════════════════════════════════════════════════════════

def _parse_github_html(html: str) -> list[str]:
    """
    Parse GitHub trending HTML and extract repo names and descriptions.
    Returns a list of plain-text strings, one per trending repository.
    """
    soup = BeautifulSoup(html, "html.parser")   # parse the full page HTML

    texts = []                                  # accumulator for extracted text fragments

    # GitHub trending repos are wrapped in <article class="Box-row"> elements
    articles = soup.find_all("article")         # find all article tags on the page

    for article in articles:                    # iterate each trending repo card
        # Repo name: inside <h2> or <h1> — grab all anchor text
        name_tag = article.find(["h2", "h1"])  # try h2 first, fall back to h1
        name = name_tag.get_text(separator=" ", strip=True) if name_tag else ""  # clean whitespace

        # Repo description: typically in a <p> tag inside the article
        desc_tag = article.find("p")           # first paragraph = description
        desc = desc_tag.get_text(strip=True) if desc_tag else ""  # strip leading/trailing spaces

        combined = f"{name} {desc}".strip()    # merge name and description into one text blob
        if combined:                           # skip empty articles (edge case)
            texts.append(combined)             # add to our list

    return texts                               # list of repo text strings


def scrape_github(
    url: str,
    headers: dict,
    timeout: int,
) -> list[str]:
    """
    Scrape the GitHub trending page and return a list of text strings
    (repo name + description) for all visible trending repos.

    Returns [] on failure.
    """
    log.info("GitHub Trending: scraping %s …", url)  # announce the target URL

    try:
        response = requests.get(        # fire the HTTP GET request
            url,                        # target URL from config
            headers=headers,            # browser-like headers to avoid 403 blocks
            timeout=timeout,            # fail fast if GitHub is slow
        )
        response.raise_for_status()     # raise HTTPError for 4xx/5xx responses

        texts = _parse_github_html(response.text)   # delegate HTML parsing to helper

        log.info("GitHub Trending: extracted text from %d repos.", len(texts))
        return texts                    # list of text blobs

    except requests.RequestException as exc:    # network errors, timeouts, bad status codes
        log.error("GitHub Trending fetch failed: %s", exc)
        return []                       # empty list; pipeline continues without this source

    except Exception as exc:            # BeautifulSoup or unexpected parsing errors
        log.error("GitHub Trending parse error: %s", exc)
        return []


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 3 — RSS News Feeds
# ══════════════════════════════════════════════════════════════════════════════

def _parse_feed_xml(xml_text: str) -> list[str]:
    """
    Parse an RSS 2.0 or Atom XML feed and return a list of article text blobs.
    Each blob = article title + description/summary joined with a space.
    """
    soup = BeautifulSoup(xml_text, "html.parser")   # html.parser handles RSS XML without lxml

    texts = []                                      # accumulator

    # ── RSS 2.0 format ── <channel> → <item> → <title> + <description>
    items = soup.find_all("item")                   # RSS 2.0 article containers
    for item in items:                              # iterate each article
        title = item.find("title")                  # <title> tag inside this <item>
        desc  = item.find("description")            # <description> may contain HTML
        title_text = title.get_text(strip=True) if title else ""    # raw text from title
        desc_text  = desc.get_text(strip=True)  if desc  else ""    # strip inner HTML tags
        blob = f"{title_text} {desc_text}".strip()  # merge into one searchable string
        if blob:                                    # skip empty entries
            texts.append(blob)                      # add to result list

    # ── Atom format ── <feed> → <entry> → <title> + <summary>/<content>
    if not texts:                                   # fallback: try Atom if no RSS items found
        entries = soup.find_all("entry")            # Atom article containers
        for entry in entries:                       # iterate each entry
            title   = entry.find("title")           # <title> inside this <entry>
            summary = entry.find("summary") or entry.find("content")  # Atom summary/content
            title_text   = title.get_text(strip=True)   if title   else ""
            summary_text = summary.get_text(strip=True) if summary else ""
            blob = f"{title_text} {summary_text}".strip()
            if blob:
                texts.append(blob)

    return texts                                    # list of article text blobs


def fetch_rss_feeds(
    feeds: dict[str, str],
    headers: dict,
    timeout: int,
) -> dict[str, list[str]]:
    """
    Fetch all configured RSS feeds and return a dict mapping source name
    to a list of article text strings.

    Failures on individual feeds are logged and skipped; others continue.
    Returns {} only if all feeds fail.
    """
    results: dict[str, list[str]] = {}  # {source_name: [article_text, ...]}

    for source_name, url in feeds.items():   # iterate each configured feed
        log.info("RSS: fetching '%s' from %s …", source_name, url)

        try:
            response = requests.get(    # fire HTTP GET for this feed
                url,
                headers=headers,        # browser-like headers
                timeout=timeout,        # per-feed timeout
            )
            response.raise_for_status() # raise on HTTP error status

            texts = _parse_feed_xml(response.text)  # parse the XML/HTML response

            log.info("RSS '%s': extracted %d articles.", source_name, len(texts))
            results[source_name] = texts            # store under the source name

        except requests.RequestException as exc:    # network / HTTP error
            log.warning("RSS feed '%s' failed: %s — skipping.", source_name, exc)
            results[source_name] = []               # empty list; don't abort the loop

        except Exception as exc:                    # unexpected parsing error
            log.warning("RSS feed '%s' parse error: %s — skipping.", source_name, exc)
            results[source_name] = []

    return results                      # {source_name: [texts]} for all attempted feeds


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 4 — Reddit
# ══════════════════════════════════════════════════════════════════════════════

def fetch_reddit(
    subreddits:    list[str],
    post_limit:    int,
    client_id:     str,
    client_secret: str,
    user_agent:    str,
) -> list[str]:
    """
    Fetch recent hot posts from the configured subreddits via the Reddit API (PRAW).
    Returns a list of text strings (title + first 300 chars of body), one per post.

    Returns [] if PRAW is not installed or credentials are missing.

    Setup:
        1. Visit https://www.reddit.com/prefs/apps
        2. Create a "script" app — note client_id and client_secret
        3. Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in your .env file
    """
    if not client_id or not client_secret:      # credentials not configured — skip gracefully
        log.warning("Reddit credentials not set — skipping Reddit source.")
        return []                               # caller continues without this source

    try:
        import praw                             # optional dependency — not installed by default
    except ImportError:
        log.error("praw is not installed — skipping Reddit source. Run: pip install praw")
        return []                               # graceful degradation

    log.info(
        "Reddit: fetching from %d subreddits (limit=%d per sub) …",
        len(subreddits), post_limit,
    )

    texts: list[str] = []                       # accumulator for post text blobs

    try:
        reddit = praw.Reddit(                   # create read-only Reddit session
            client_id     = client_id,          # from app registration
            client_secret = client_secret,      # from app registration
            user_agent    = user_agent,         # required by Reddit ToS
        )

        for sub_name in subreddits:             # iterate each configured subreddit
            try:
                subreddit = reddit.subreddit(sub_name)   # get subreddit handle

                for post in subreddit.hot(limit=post_limit):  # iterate hot posts
                    title = post.title or ""                   # post title (always present)
                    body  = (post.selftext or "")[:300]        # first 300 chars of body
                    blob  = f"{title} {body}".strip()          # merge into one text blob
                    if blob:                                    # skip empty/deleted posts
                        texts.append(blob)                     # add to result list

                log.info(
                    "Reddit r/%s: fetched %d posts.",
                    sub_name, post_limit,
                )

            except Exception as exc:            # individual subreddit failure — skip, continue
                log.warning("Reddit r/%s failed: %s — skipping.", sub_name, exc)

    except Exception as exc:                    # PRAW session creation failed (bad credentials)
        log.error("Reddit session error: %s", exc)
        return []                               # return empty — pipeline continues

    log.info("Reddit: total %d post blobs collected.", len(texts))
    return texts                                # list of text blobs for transform step


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API — fetch_all
# ══════════════════════════════════════════════════════════════════════════════

def fetch_all(sources: list[str] | None = None) -> dict:
    """
    Run all (or a subset of) extractors and return their raw results in one dict.

    Args:
        sources: optional list of source names to run, e.g. ["trends", "github"].
                 Defaults to all four if None.

    Returns:
        {
            "trends":  {keyword: avg_score},          # Google Trends data
            "github":  [text, ...],                   # GitHub repo text blobs
            "rss":     {source_name: [text, ...]},    # RSS article text blobs
            "reddit":  [text, ...],                   # Reddit post text blobs
        }
    """
    active = set(sources) if sources else {"trends", "github", "rss", "reddit"}  # which sources to run

    raw: dict = {                       # initialise all keys with empty defaults
        "trends": {},                   # will be populated by Google Trends extractor
        "github": [],                   # will be populated by GitHub scraper
        "rss":    {},                   # will be populated by RSS fetcher
        "reddit": [],                   # will be populated by Reddit extractor
    }

    if "trends" in active:             # only run if caller requested this source
        raw["trends"] = fetch_google_trends(
            keywords   = config.TREND_KEYWORDS,    # which keywords to query
            timeframe  = config.TRENDS_TIMEFRAME,  # 12-month rolling window
            geo        = config.TRENDS_GEO,        # worldwide
            sleep_sec  = config.TRENDS_SLEEP_SEC,  # rate-limit pause
        )

    if "github" in active:             # only run if caller requested this source
        raw["github"] = scrape_github(
            url     = config.GITHUB_TRENDING_URL,   # trending page URL
            headers = config.REQUEST_HEADERS,        # browser-like headers
            timeout = config.REQUEST_TIMEOUT,        # connection timeout
        )

    if "rss" in active:                # only run if caller requested this source
        raw["rss"] = fetch_rss_feeds(
            feeds   = config.RSS_FEEDS,             # {name: url} dict from config
            headers = config.REQUEST_HEADERS,        # same browser headers
            timeout = config.REQUEST_TIMEOUT,
        )

    if "reddit" in active:             # only run if caller requested this source
        raw["reddit"] = fetch_reddit(
            subreddits    = config.REDDIT_SUBREDDITS,     # list of subreddit names
            post_limit    = config.REDDIT_POST_LIMIT,     # posts per subreddit
            client_id     = config.REDDIT_CLIENT_ID,      # from .env
            client_secret = config.REDDIT_CLIENT_SECRET,  # from .env
            user_agent    = config.REDDIT_USER_AGENT,     # required by Reddit API
        )

    return raw                          # single dict with all raw data for transform step
