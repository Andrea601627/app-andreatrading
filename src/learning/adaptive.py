"""Adattamento bayesiano dei pesi multi-fattore basato sui trade chiusi.

Logica:
  - per ogni dimensione (tecnica/fondamentale/ml/sentiment) calcola accuracy
    rispetto al P&L dei trade
  - se una dimensione ha accuracy significativamente < 50% (p<0.05, n>=30),
    riduci il suo peso (entro max_weight_delta)
  - propone modifiche, salva su DB; se delta > soglia richiede approvazione utente
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from scipy import stats

from ..utils.config import load_config
from ..utils.db import connect
from ..utils.logger import get_logger

log = get_logger()


def _fetch_dimension_outcomes() -> list[dict[str, Any]]:
    """Per ogni trade chiuso, recupera score per dimensione + esito."""
    out = []
    with connect() as c:
        # joina trade chiusi con signals coevi (stesso ticker, signal pre-trade)
        rows = c.execute("""
            SELECT t.id, t.ticker, t.pnl_pct, t.horizon, t.reason_json,
                    t.executed_at
            FROM trades t
            WHERE t.side='BUY' AND t.closed_at IS NOT NULL
              AND t.pnl_pct IS NOT NULL
        """).fetchall()

    for r in rows:
        try:
            reason = json.loads(r["reason_json"] or "{}")
        except Exception:
            reason = {}
        comp = reason.get("components", {})
        out.append({
            "ticker": r["ticker"],
            "horizon": r["horizon"],
            "pnl_pct": r["pnl_pct"],
            "scores": {k: v.get("score", 0) for k, v in comp.items()},
        })
    return out


def evaluate_dimensions(min_n: int | None = None) -> dict[str, dict]:
    cfg = load_config()["learning"]
    min_n = min_n or cfg["min_trades_before_adjust"]
    trades = _fetch_dimension_outcomes()
    if len(trades) < min_n:
        return {"_meta": {"n": len(trades), "min_n": min_n, "ready": False}}

    dims = ("technical", "fundamental", "ml_forecast", "sentiment")
    out: dict[str, dict] = {"_meta": {"n": len(trades), "min_n": min_n, "ready": True}}

    for d in dims:
        scores = []
        pnls = []
        for t in trades:
            s = t["scores"].get(d)
            if s is None:
                continue
            scores.append(s)
            pnls.append(t["pnl_pct"])
        if len(scores) < min_n:
            out[d] = {"n": len(scores), "tested": False}
            continue

        # correlazione score -> pnl
        if len(set(scores)) < 2:
            continue
        corr, p_value = stats.pearsonr(scores, pnls)
        # accuracy direzionale: trade con score>0 che chiudono in profit
        n_correct = sum(
            1 for s, p in zip(scores, pnls) if (s > 0 and p > 0) or (s < 0 and p < 0)
        )
        accuracy = n_correct / len(scores)

        out[d] = {
            "n": len(scores),
            "tested": True,
            "correlation": round(corr, 3),
            "p_value": round(p_value, 4),
            "directional_accuracy": round(accuracy, 3),
        }
    return out


def propose_weight_adjustments() -> list[dict]:
    cfg = load_config()["learning"]
    eval_data = evaluate_dimensions()
    if not eval_data.get("_meta", {}).get("ready"):
        return []

    suggestions = []
    for dim in ("technical", "fundamental", "ml_forecast", "sentiment"):
        d = eval_data.get(dim, {})
        if not d.get("tested"):
            continue
        # condizione: correlazione negativa con p significativo
        if d["correlation"] < -0.1 and d["p_value"] < cfg["significance_p_value"]:
            suggestions.append({
                "dimension": dim,
                "action": "reduce_weight",
                "rationale": (f"corr={d['correlation']:.3f} p={d['p_value']:.4f} "
                               f"n={d['n']} acc={d['directional_accuracy']:.2%}"),
                "delta": -0.05,
            })
        elif d["correlation"] > 0.15 and d["p_value"] < cfg["significance_p_value"]:
            suggestions.append({
                "dimension": dim,
                "action": "increase_weight",
                "rationale": (f"corr={d['correlation']:.3f} p={d['p_value']:.4f} "
                               f"n={d['n']} acc={d['directional_accuracy']:.2%}"),
                "delta": +0.05,
            })
    return suggestions


def record_proposal(suggestion: dict, horizon: str = "short_medium") -> None:
    cfg = load_config()
    requires_approval = abs(suggestion["delta"]) > cfg["learning"]["max_weight_delta"]
    old = cfg["weights"][horizon].get(suggestion["dimension"], 0)
    new = max(0.0, min(1.0, old + suggestion["delta"]))
    with connect() as c:
        c.execute("""
            INSERT INTO learning_events
                (timestamp, event_type, target, old_value, new_value, rationale,
                 sample_size, p_value, requires_approval, approved)
            VALUES (?, 'weight_adjust', ?, ?, ?, ?, NULL, NULL, ?, ?)
        """, (
            datetime.utcnow(),
            f"{horizon}.{suggestion['dimension']}",
            old, new,
            suggestion["rationale"],
            int(requires_approval),
            None if requires_approval else 1,
        ))
    log.info(f"Learning proposal: {horizon}.{suggestion['dimension']} "
              f"{old:.2f}->{new:.2f} ({'NEEDS APPROVAL' if requires_approval else 'AUTO'})")
