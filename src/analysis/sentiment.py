"""Sentiment analysis sulle news -> score [-100, +100].

Strategia: lessico finanziario italiano + inglese (no dipendenze pesanti di default).
Se l'utente abilita FinBERT in config (set use_finbert: true), useremo HuggingFace
transformers per scoring più accurato.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from ..data.news import NewsItem

# Lessico semplice (italiano + inglese) — espandibile / sostituibile con FinBERT
POSITIVE_TERMS = {
    # ita
    "utile", "utili", "ricavi in crescita", "supera attese", "supera le stime",
    "promossa", "buy", "promossa a buy", "alza target", "alza il target",
    "dividendo", "buyback", "riacquisto", "espansione", "acquisizione strategica",
    "record", "rialzo", "balza", "vola", "accelera", "outperform", "rafforza",
    "espande", "guidance alzata", "alza guidance", "trimestrale solida",
    # eng
    "beats", "beat estimates", "surge", "soars", "jumps", "upgrade", "buy rating",
    "outperform", "strong results", "record high", "dividend", "buyback",
    "raises guidance", "raises forecast", "expansion", "acquisition",
}
NEGATIVE_TERMS = {
    "perdita", "perdite", "profit warning", "sotto le attese", "deludente",
    "taglio target", "taglia target", "declassata", "sell", "underperform",
    "indagine", "scandalo", "frode", "downgrade", "crolla", "tonfo", "ribasso",
    "licenziamenti", "ristrutturazione", "guidance abbassata", "taglia guidance",
    "rosso", "default", "fallimento",
    # eng
    "miss", "missed estimates", "plunge", "drops", "downgrade", "sell rating",
    "weak results", "loss", "cuts guidance", "investigation", "fraud", "lawsuit",
    "layoffs", "restructuring", "profit warning",
}


@dataclass
class SentimentResult:
    score: float
    details: dict = field(default_factory=dict)
    confidence: float = 1.0


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def _score_text(text: str) -> int:
    t = _normalize(text)
    pos = sum(1 for term in POSITIVE_TERMS if term in t)
    neg = sum(1 for term in NEGATIVE_TERMS if term in t)
    return pos - neg


def _recency_weight(published: datetime, now: datetime) -> float:
    age = (now - published).total_seconds() / 86400.0
    if age < 1: return 1.0
    if age < 3: return 0.7
    if age < 7: return 0.4
    if age < 14: return 0.2
    return 0.05


def compute(news: list[NewsItem]) -> SentimentResult:
    if not news:
        return SentimentResult(score=0.0, details={"reason": "no_news"}, confidence=0.0)

    now = datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(days=21)
    recent = [n for n in news if n.published >= cutoff]
    if not recent:
        return SentimentResult(score=0.0, details={"reason": "no_recent_news"}, confidence=0.2)

    weighted_sum = 0.0
    weight_total = 0.0
    breakdown: list[dict] = []
    pos_count = 0
    neg_count = 0

    for item in recent[:30]:
        raw = _score_text(item.title)
        w = _recency_weight(item.published, now)
        weighted_sum += raw * w
        weight_total += w
        if raw > 0: pos_count += 1
        elif raw < 0: neg_count += 1
        breakdown.append({
            "title": item.title[:140],
            "score": raw,
            "weight": round(w, 2),
            "age_days": round((now - item.published).total_seconds() / 86400, 1),
        })

    if weight_total == 0:
        return SentimentResult(score=0.0, details={"reason": "zero_weight"}, confidence=0.2)

    avg = weighted_sum / weight_total
    # Scala: media ±3 termini per titolo è già forte
    score = max(-100, min(100, avg * 35))

    # Confidence: più news, maggiore confidence; saturata a 20
    confidence = min(len(recent) / 20.0, 1.0)

    return SentimentResult(
        score=score,
        details={
            "news_count": len(recent),
            "positive_titles": pos_count,
            "negative_titles": neg_count,
            "neutral_titles": len(recent) - pos_count - neg_count,
            "top_items": breakdown[:8],
        },
        confidence=confidence,
    )
