"""
config.py — single source of truth for all pipeline settings.
Import this module in extract / transform / load instead of hardcoding values.

Credentials (email, Reddit) are read from environment variables so they are
never hardcoded. Set them in a .env file (see .env.example) or export them
in your shell before running the pipeline.
"""

import os                           # read environment variables for credentials
from pathlib import Path            # OS-agnostic path handling; works on Mac, Linux, Windows

try:
    from dotenv import load_dotenv  # load .env file into os.environ automatically
    load_dotenv(Path(__file__).parent / ".env", override=False)  # project-local .env
except ImportError:
    pass                            # dotenv is optional — env vars can be set in the shell

try:
    import streamlit as st          # on Streamlit Cloud, secrets live in st.secrets
    for key, val in st.secrets.items():
        os.environ.setdefault(key, str(val))  # push Streamlit secrets into env vars
except Exception:
    pass                            # not running inside Streamlit — skip silently

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT_DIR    = Path(__file__).parent # directory that contains this config file
DATA_DIR    = ROOT_DIR / "data"     # raw scraped data landing zone
OUTPUT_DIR  = ROOT_DIR / "output"   # processed CSV + SQLite outputs
DB_PATH     = OUTPUT_DIR / "signals.db"  # default SQLite database file

# ── Sectors ────────────────────────────────────────────────────────────────────
# Each keyword maps to a sector label used for filtering in the dashboard.
KEYWORD_SECTORS: dict[str, str] = {
    # AI / Technology
    "ai agents":            "AI/Tech",
    "llm":                  "AI/Tech",
    "robotics":             "AI/Tech",
    "computer vision":      "AI/Tech",
    "generative ai":        "AI/Tech",
    "quantum computing":    "AI/Tech",
    "autonomous vehicles":  "AI/Tech",
    # Energy / Climate
    "solar energy":         "Energy",
    "nuclear fusion":       "Energy",
    "battery storage":      "Energy",
    "green hydrogen":       "Energy",
    "electric vehicles":    "Energy",
    "carbon capture":       "Energy",
    # Biotech / Health
    "longevity":            "Biotech",
    "glp-1":                "Biotech",
    "gene therapy":         "Biotech",
    "weight loss drug":     "Biotech",
    "mental health":        "Biotech",
    # Finance
    "venture capital":      "Finance",
    "ipo":                  "Finance",
    "private equity":       "Finance",
    "bitcoin":              "Finance",
    "stablecoin":           "Finance",
    # Industry / Manufacturing
    "reshoring":            "Industry",
    "supply chain":         "Industry",
    "semiconductor":        "Industry",
    "automation":           "Industry",
    # Consumer / Business
    "creator economy":      "Consumer",
    "subscription model":   "Consumer",
    "e-commerce":           "Consumer",
    "remote work":          "Consumer",
}

# Dashboard colour per sector (Altair/CSS hex)
SECTOR_COLORS: dict[str, str] = {
    "AI/Tech":   "#4299e1",   # blue
    "Energy":    "#48bb78",   # green
    "Biotech":   "#9f7aea",   # purple
    "Finance":   "#ed8936",   # orange
    "Industry":  "#a0aec0",   # grey
    "Consumer":  "#f687b3",   # pink
    "Other":     "#cbd5e0",   # light grey fallback
}

# ── Google Trends ──────────────────────────────────────────────────────────────
# One representative keyword per sector — processed in batches of 5.
# pytrends allows max 5 keywords per request; extract.py batches automatically.
TREND_KEYWORDS = [
    # AI/Tech
    "AI agents",
    "quantum computing",
    "autonomous vehicles",
    # Energy
    "solar energy",
    "nuclear fusion",
    # Biotech
    "longevity",
    "weight loss drug",
    # Finance
    "venture capital",
    "bitcoin",
    # Industry / Consumer
    "reshoring",
    "creator economy",
]

TRENDS_TIMEFRAME = "today 12-m"     # rolling 12-month window; gives ~52 weekly data points
TRENDS_GEO       = ""               # empty = worldwide; use "US", "GB", etc. for a country filter
TRENDS_SLEEP_SEC = 15               # seconds between batch API calls (avoids 429 errors)

# ── Signal vocabulary ──────────────────────────────────────────────────────────
# Used for keyword-frequency counting in GitHub + RSS + Reddit text.
# All entries must be lowercase — transform.py normalises before matching.
SIGNAL_KEYWORDS = [
    # AI / Technology
    "ai agents", "llm", "large language model", "generative ai",
    "machine learning", "deep learning", "computer vision", "robotics",
    "autonomous", "multimodal", "foundation model", "reinforcement learning",
    "quantum computing", "edge computing", "neural network", "transformer",
    "vector database", "rag", "fine-tuning", "inference",
    # Energy / Climate
    "solar energy", "nuclear fusion", "battery storage", "green hydrogen",
    "electric vehicles", "carbon capture", "renewable energy", "wind energy",
    "energy storage", "climate tech",
    # Biotech / Health
    "longevity", "glp-1", "gene therapy", "crispr", "weight loss drug",
    "mental health", "biotech", "drug discovery", "precision medicine",
    # Finance / Crypto
    "venture capital", "ipo", "private equity", "bitcoin", "stablecoin",
    "defi", "tokenization", "fintech", "crypto", "hedge fund",
    # Industry / Manufacturing
    "reshoring", "supply chain", "semiconductor", "automation", "3d printing",
    "manufacturing", "industrial robot",
    # Consumer / Business
    "creator economy", "subscription model", "e-commerce", "remote work",
    "gig economy", "startup", "unicorn", "saas",
]

# ── GitHub Trending ────────────────────────────────────────────────────────────
GITHUB_TRENDING_URL      = "https://github.com/trending"         # all languages
GITHUB_TRENDING_URL_PY   = "https://github.com/trending/python"  # Python-specific

# ── RSS News Feeds ─────────────────────────────────────────────────────────────
# Extended set: tech + business + finance + health
RSS_FEEDS = {
    # Tech
    "techcrunch":      "https://techcrunch.com/feed/",
    "hackernews":      "https://news.ycombinator.com/rss",
    "the_verge":       "https://www.theverge.com/rss/index.xml",
    "mit_tech_review": "https://www.technologyreview.com/feed/",
    # Business / Finance
    "reuters_business": "https://feeds.reuters.com/reuters/businessNews",
    "cnbc":             "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "yahoo_finance":    "https://finance.yahoo.com/news/rssindex",
    "entrepreneur":     "https://www.entrepreneur.com/latest.rss",
    # Deep research
    "hbr":             "https://feeds.hbr.org/harvardbusiness",
    "nature":          "https://www.nature.com/nature.rss",
}

# ── HTTP settings ──────────────────────────────────────────────────────────────
REQUEST_TIMEOUT = 15                # seconds; applied to both connect and read phases

# Mimic a real browser so servers don't block automated requests
REQUEST_HEADERS = {
    "User-Agent":      (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",   # request English content
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ── Signal analysis ────────────────────────────────────────────────────────────
ANOMALY_THRESHOLD = 1.5             # z-score above which a keyword is flagged as anomalous

# Weight applied to each source when computing the final frequency.
# Higher weight = this source's signal counts more in the composite score.
SOURCE_WEIGHTS: dict[str, float] = {
    "google_trends":  1.5,          # public search interest — strongest market signal
    "github_trending": 1.3,         # developer adoption — early-mover indicator
    "reddit":          1.1,         # community discussion — real-time sentiment
    "rss":             1.0,         # news coverage — lagging indicator (baseline)
}

# ── Email alerts ───────────────────────────────────────────────────────────────
# Credentials are read from environment variables — never hardcode them here.
EMAIL_ENABLED   = os.getenv("SIGNAL_EMAIL_ENABLED",   "false").lower() == "true"
EMAIL_SENDER    = os.getenv("SIGNAL_EMAIL_SENDER",    "")   # e.g. you@gmail.com
EMAIL_PASSWORD  = os.getenv("SIGNAL_EMAIL_PASSWORD",  "")   # Gmail App Password (16 chars)
EMAIL_RECIPIENT = os.getenv("SIGNAL_EMAIL_RECIPIENT", "")   # who receives the alert
EMAIL_SMTP_HOST = os.getenv("SIGNAL_SMTP_HOST",       "smtp.gmail.com")
EMAIL_SMTP_PORT = int(os.getenv("SIGNAL_SMTP_PORT",   "587"))  # 587 = STARTTLS

# ── Reddit API ─────────────────────────────────────────────────────────────────
# Register an app at https://www.reddit.com/prefs/apps to get these credentials.
REDDIT_CLIENT_ID     = os.getenv("REDDIT_CLIENT_ID",     "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT    = "EarlySignalDetector/1.0"         # required by Reddit API ToS
REDDIT_SUBREDDITS    = [                                  # subreddits to monitor
    "MachineLearning",
    "artificial",
    "technology",
    "singularity",
]
REDDIT_POST_LIMIT    = 50                                 # posts to fetch per subreddit

# ── Scheduler ──────────────────────────────────────────────────────────────────
SCHEDULER_HOUR   = int(os.getenv("SIGNAL_SCHEDULE_HOUR",   "8"))   # run daily at 08:00
SCHEDULER_MINUTE = int(os.getenv("SIGNAL_SCHEDULE_MINUTE", "0"))

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_FORMAT    = "%(asctime)s  %(levelname)-8s  [%(module)s]  %(message)s"  # format with module name
LOG_DATEFMT   = "%Y-%m-%d %H:%M:%S"   # human-readable timestamps
LOG_LEVEL     = "INFO"                 # default verbosity; override with --debug CLI flag
