"""News & sentiment headlines per ticker.

Fonti: yfinance news (gratis) + Google News RSS come fallback.
Il modello FinBERT viene caricato lazy in analysis/sentiment.py.
"""
from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser
import yfinance as yf

from ..utils.logger import get_logger

log = get_logger()

_cache: dict[str, tuple[float, list]] = {}
_TTL = 1800  # 30 min


@dataclass
class NewsItem:
    title: str
    publisher: str
    link: str
    published: datetime
    source: str  # "yfinance" | "google_rss"


def _from_yfinance(ticker: str) -> list[NewsItem]:
    try:
        items = yf.Ticker(ticker).news or []
    except Exception as e:
        log.debug(f"yfinance news failed for {ticker}: {e}")
        return []
    out: list[NewsItem] = []
    for it in items:
        try:
            ts = it.get("providerPublishTime") or it.get("pubDate")
            if isinstance(ts, (int, float)):
                pub = datetime.fromtimestamp(ts, tz=timezone.utc)
            else:
                pub = datetime.now(tz=timezone.utc)
            out.append(NewsItem(
                title=it.get("title", "").strip(),
                publisher=it.get("publisher", "yahoo"),
                link=it.get("link", ""),
                published=pub,
                source="yfinance",
            ))
        except Exception:
            continue
    return out


def _from_google_rss(ticker: str, name: str | None = None) -> list[NewsItem]:
    query = name or ticker.replace(".MI", "")
    q = urllib.parse.quote_plus(f"{query} azioni borsa")
    url = f"https://news.google.com/rss/search?q={q}&hl=it&gl=IT&ceid=IT:it"
    try:
        feed = feedparser.parse(url)
    except Exception as e:
        log.debug(f"google rss failed for {ticker}: {e}")
        return []
    out: list[NewsItem] = []
    for e in feed.entries[:20]:
        try:
            pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc) if e.get("published_parsed") else datetime.now(tz=timezone.utc)
            out.append(NewsItem(
                title=e.title,
                publisher=getattr(e, "source", {}).get("title", "google") if hasattr(e, "source") else "google",
                link=e.link,
                published=pub,
                source="google_rss",
            ))
        except Exception:
            continue
    return out


def get_news(ticker: str, name: str | None = None, limit: int = 20) -> list[NewsItem]:
    now = time.time()
    cached = _cache.get(ticker)
    if cached and (now - cached[0]) < _TTL:
        return cached[1][:limit]

    items = _from_yfinance(ticker)
    if len(items) < 5:
        items.extend(_from_google_rss(ticker, name))
    # dedup by title
    seen: set[str] = set()
    deduped: list[NewsItem] = []
    for it in sorted(items, key=lambda x: x.published, reverse=True):
        key = it.title.lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)
    _cache[ticker] = (now, deduped)
    return deduped[:limit]
