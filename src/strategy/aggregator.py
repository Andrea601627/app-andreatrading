"""Aggrega gli score multi-dimensione in un singolo score finale per orizzonte."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..analysis.fundamental import FundamentalResult
from ..analysis.ml_forecast import ForecastResult
from ..analysis.regime import MarketRegime
from ..analysis.sentiment import SentimentResult
from ..analysis.technical import TechnicalResult
from ..utils.config import load_config


@dataclass
class AggregatedScore:
    horizon: str                # "short_medium" | "long_term"
    final_score: float          # -100..+100
    components: dict = field(default_factory=dict)
    weights_used: dict = field(default_factory=dict)
    confidence: float = 1.0
    regime: MarketRegime | None = None
    notes: list[str] = field(default_factory=list)


def _regime_modulated_weights(base: dict, regime: MarketRegime | None) -> dict:
    """In regime ad alta volatilità, alza tecnica/sentiment; abbassa fondamentali."""
    if regime is None or regime.volatility != "high":
        return dict(base)
    w = dict(base)
    delta = 0.05
    w["technical"] = min(0.7, w["technical"] + delta)
    w["sentiment"] = min(0.3, w["sentiment"] + delta / 2)
    w["fundamental"] = max(0.05, w["fundamental"] - delta)
    w["ml_forecast"] = max(0.05, w["ml_forecast"] - delta / 2)
    # rinormalizza
    s = sum(w.values())
    return {k: v / s for k, v in w.items()}


def aggregate(
    horizon: str,
    technical: TechnicalResult,
    fundamental: FundamentalResult,
    forecast: ForecastResult,
    sentiment: SentimentResult,
    regime: MarketRegime | None = None,
) -> AggregatedScore:
    cfg = load_config()
    base_weights = cfg["weights"][horizon]
    weights = _regime_modulated_weights(base_weights, regime)

    # Score componenti pesati per confidence (se confidence=0 il peso scivola sulle altre)
    components = {
        "technical": (technical.score, technical.confidence),
        "fundamental": (fundamental.score, fundamental.confidence),
        "ml_forecast": (forecast.score, forecast.confidence),
        "sentiment": (sentiment.score, sentiment.confidence),
    }

    effective_weight_sum = 0.0
    weighted = 0.0
    for k, (score, conf) in components.items():
        w = weights[k] * conf
        weighted += score * w
        effective_weight_sum += w

    if effective_weight_sum == 0:
        final = 0.0
        notes = ["all_components_zero_confidence"]
    else:
        final = weighted / effective_weight_sum
        notes = []

    final = max(-100.0, min(100.0, final))

    overall_conf = effective_weight_sum / sum(weights.values()) if weights else 0

    return AggregatedScore(
        horizon=horizon,
        final_score=round(final, 2),
        components={
            k: {"score": round(v[0], 2), "confidence": round(v[1], 3)}
            for k, v in components.items()
        },
        weights_used={k: round(v, 3) for k, v in weights.items()},
        confidence=round(overall_conf, 3),
        regime=regime,
        notes=notes,
    )
