"""Rilevamento opportunità di arbitraggio su titoli cross-listed."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import yfinance as yf


@dataclass
class ArbSignal:
    pair_name: str
    leg_buy: str        # ticker più economico → comprare
    leg_ref: str        # ticker più caro → riferimento
    spread_pct: float   # differenza % tra i due prezzi
    price_buy: float    # prezzo di acquisto del leg economico
    price_ref_eur: float


def _fetch_price(ticker: str) -> tuple[str, float | None]:
    try:
        df = yf.download(ticker, period="1d", interval="1m",
                         auto_adjust=True, progress=False, threads=False)
        if isinstance(df.columns, type(df.columns)) and hasattr(df.columns, 'get_level_values'):
            if df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)
        if df.empty:
            return ticker, None
        return ticker, float(df["Close"].iloc[-1])
    except Exception:
        return ticker, None


def detect_all(pairs: list[dict], min_spread_pct: float) -> list[ArbSignal]:
    """Scarica prezzi in parallelo e restituisce le coppie con spread > soglia."""
    # Raccogli tutti i ticker unici da scaricare
    tickers_needed: set[str] = set()
    for p in pairs:
        tickers_needed.add(p["leg_a"])
        tickers_needed.add(p["leg_b"])
        if p.get("fx_pair"):
            tickers_needed.add(p["fx_pair"])

    # Download parallelo
    prices: dict[str, float | None] = {}
    with ThreadPoolExecutor(max_workers=min(16, len(tickers_needed))) as pool:
        futures = {pool.submit(_fetch_price, t): t for t in tickers_needed}
        for future in as_completed(futures):
            try:
                ticker, price = future.result(timeout=10)
                prices[ticker] = price
            except Exception:
                pass

    signals: list[ArbSignal] = []

    for pair in pairs:
        price_a = prices.get(pair["leg_a"])
        price_b = prices.get(pair["leg_b"])
        if not price_a or not price_b:
            continue

        # Conversione valutaria → tutto in EUR
        fx = 1.0
        if pair.get("fx_pair"):
            fx = prices.get(pair["fx_pair"]) or 0.0
            if fx <= 0:
                continue

        cur_a = pair["currency_a"]
        cur_b = pair["currency_b"]

        if cur_a == "EUR":
            price_a_eur = price_a
        elif cur_a == "GBP":
            price_a_eur = price_a * fx          # fx = GBPEUR
        elif cur_a == "USD":
            price_a_eur = price_a / fx          # fx = EURUSD
        else:
            continue

        if cur_b == "EUR":
            price_b_eur = price_b
        elif cur_b == "GBP":
            price_b_eur = price_b * fx
        elif cur_b == "USD":
            price_b_eur = price_b / fx
        else:
            continue

        if price_b_eur <= 0:
            continue

        spread = (price_a_eur - price_b_eur) / price_b_eur  # positivo = A più caro

        if abs(spread) < min_spread_pct:
            continue

        # Il leg più economico è quello da comprare
        if spread > 0:                          # A più caro → B è l'occasione
            leg_buy, price_buy = pair["leg_b"], price_b
            leg_ref, price_ref_eur = pair["leg_a"], price_a_eur
        else:                                   # B più caro → A è l'occasione
            leg_buy, price_buy = pair["leg_a"], price_a
            leg_ref, price_ref_eur = pair["leg_b"], price_b_eur

        signals.append(ArbSignal(
            pair_name=pair["name"],
            leg_buy=leg_buy,
            leg_ref=leg_ref,
            spread_pct=abs(spread),
            price_buy=price_buy,
            price_ref_eur=price_ref_eur,
        ))

    # Ordina per spread più ampio → opportunità migliori prima
    signals.sort(key=lambda s: s.spread_pct, reverse=True)
    return signals
