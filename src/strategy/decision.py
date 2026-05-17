"""Mappa score aggregato -> decisione operativa."""
from __future__ import annotations

from dataclasses import dataclass

from ..utils.config import load_config
from .aggregator import AggregatedScore

DECISIONS = ("STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL")


@dataclass
class Decision:
    action: str
    score: float
    confidence: float
    horizon: str
    notes: list[str]


def decide(agg: AggregatedScore) -> Decision:
    th = load_config()["decision_thresholds"]
    s = agg.final_score
    notes = list(agg.notes)

    # Confidence troppo bassa -> HOLD forzato
    if agg.confidence < 0.3:
        notes.append(f"low_confidence:{agg.confidence:.2f}")
        action = "HOLD"
    elif s >= th["strong_buy"]:
        action = "STRONG_BUY"
    elif s >= th["buy"]:
        action = "BUY"
    elif s <= th["strong_sell"]:
        action = "STRONG_SELL"
    elif s <= th["sell"]:
        action = "SELL"
    else:
        action = "HOLD"

    return Decision(
        action=action,
        score=s,
        confidence=agg.confidence,
        horizon=agg.horizon,
        notes=notes,
    )
