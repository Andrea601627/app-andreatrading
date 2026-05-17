"""Learning semplificato: solo monitoraggio statistiche, nessun aggiustamento automatico.

Il nuovo sistema è basato su momentum puro — aggiustare i pesi dell'analisi
multi-fattore su eventi storici introduce overfitting e non migliora le
decisioni su eventi non visti. Il journal raccoglie i dati per revisione manuale.
"""
from __future__ import annotations

from ..utils.logger import get_logger

log = get_logger()


def propose_weight_adjustments() -> list[dict]:
    return []


def record_proposal(suggestion: dict, horizon: str = "short_medium") -> None:
    pass
