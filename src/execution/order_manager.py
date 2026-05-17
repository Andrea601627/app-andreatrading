"""Factory broker + validazione mode live vs paper."""
from __future__ import annotations

from ..utils.config import load_config
from ..utils.logger import get_logger
from .broker_base import Broker
from .paper_broker import PaperBroker

log = get_logger()


def get_broker() -> Broker:
    cfg = load_config()
    mode = cfg.get("mode", "paper")
    if mode == "live":
        log.warning("MODE=LIVE — operazioni con denaro reale")
        from .ibkr_broker import IBKRBroker
        return IBKRBroker()
    log.info(f"Broker: PAPER (capitale simulato {cfg['capital']['initial']} EUR)")
    return PaperBroker()
