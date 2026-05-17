"""Dashboard Streamlit - vista principale "VENDI X / COMPRA Y" + drill-down."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

# permette di lanciare con `streamlit run dashboard/app.py` da qualunque cwd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import plotly.express as px
import streamlit as st

from src.learning.journal import get_closed_trades, get_open_trades, get_recent_signals
from src.learning.postmortem import error_class_summary
from src.risk.drawdown_guard import is_blocked, resolve
from src.utils.config import load_config
from src.utils.db import connect, init_db

st.set_page_config(
    page_title="AndreaTrading",
    page_icon=":chart_with_upwards_trend:",
    layout="wide",
)


def _q(sql: str, params: tuple = ()) -> pd.DataFrame:
    with connect() as c:
        cur = c.execute(sql, params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
    return pd.DataFrame(rows, columns=cols)


init_db()
cfg = load_config()


def _render_drilldown(row):
    """Spiega le motivazioni di un segnale."""
    st.write(f"**Generato**: {row['generated_at']}")
    st.write(f"**Orizzonte**: {row['horizon']}")
    st.write(f"**Regime**: {row['regime']}")
    st.write(f"**Qualita' dati OK**: {bool(row['quality_ok'])}")
    if row.get("notes"):
        st.warning(f"Note: {row['notes']}")

    try:
        comp = json.loads(row["components_json"] or "{}")
        weights = json.loads(row["weights_json"] or "{}")
    except Exception:
        comp = {}
        weights = {}

    if comp:
        df_comp = pd.DataFrame([
            {"dimension": k, "score": v["score"], "confidence": v["confidence"],
             "weight": weights.get(k, 0)}
            for k, v in comp.items()
        ])
        st.dataframe(df_comp, use_container_width=True)

# ============================================================
# Sidebar
# ============================================================
st.sidebar.title("AndreaTrading")
st.sidebar.caption(f"Mode: **{cfg['mode'].upper()}**")
page = st.sidebar.radio(
    "Sezione",
    ["Azioni consigliate", "Portafoglio", "Storico segnali",
     "Trade chiusi & Learning", "Backtest", "Impostazioni"],
)

if is_blocked():
    st.sidebar.error("CIRCUIT BREAKER ATTIVO")
    if st.sidebar.button("Sblocca (mia responsabilità)"):
        resolve(resolved_by="user_dashboard")
        st.rerun()

# ============================================================
# Pagina 1: Azioni consigliate
# ============================================================
if page == "Azioni consigliate":
    st.title("Azioni consigliate ora")
    st.caption("Top segnali generati dall'ultimo ciclo di analisi")

    df = _q("""
        SELECT s.* FROM signals s
        INNER JOIN (
            SELECT ticker, MAX(generated_at) AS mx FROM signals GROUP BY ticker
        ) latest ON s.ticker = latest.ticker AND s.generated_at = latest.mx
        WHERE s.horizon = 'short_medium'
        ORDER BY s.generated_at DESC
    """)
    if df.empty:
        st.info("Nessun segnale ancora generato. Lancia il primo ciclo:\n\n"
                "`python -m src.orchestrator.main_loop --once`")
    else:
        buys = df[df["decision"].isin(["BUY", "STRONG_BUY"])].sort_values("score", ascending=False)
        sells = df[df["decision"].isin(["SELL", "STRONG_SELL"])].sort_values("score")

        col1, col2 = st.columns(2)
        with col1:
            st.subheader(f":green[COMPRA] ({len(buys)})")
            if buys.empty:
                st.write("Nessun segnale BUY attivo.")
            for _, row in buys.head(15).iterrows():
                with st.expander(f"**{row['ticker']}** — {row['decision']} (score {row['score']:.1f})"):
                    _render_drilldown(row)
        with col2:
            st.subheader(f":red[VENDI] ({len(sells)})")
            if sells.empty:
                st.write("Nessun segnale SELL attivo.")
            for _, row in sells.head(15).iterrows():
                with st.expander(f"**{row['ticker']}** — {row['decision']} (score {row['score']:.1f})"):
                    _render_drilldown(row)

# ============================================================
# Pagina 2: Portafoglio
# ============================================================
elif page == "Portafoglio":
    st.title("Portafoglio attuale")
    pos = _q("SELECT * FROM positions")
    if pos.empty:
        st.info("Nessuna posizione aperta.")
    else:
        st.dataframe(pos, use_container_width=True)

    st.subheader("Equity curve")
    eq = _q("SELECT * FROM equity_curve ORDER BY timestamp")
    if not eq.empty:
        eq["timestamp"] = pd.to_datetime(eq["timestamp"])
        fig = px.line(eq, x="timestamp", y="total_equity", title="Equity")
        st.plotly_chart(fig, use_container_width=True)

        last = eq.iloc[-1]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Equity totale", f"{last['total_equity']:.2f} €")
        c2.metric("Cash", f"{last['cash']:.2f} €")
        c3.metric("Posizioni", f"{last['positions_value']:.2f} €")
        c4.metric("Drawdown", f"{last['drawdown_pct']:.2%}")

# ============================================================
# Pagina 3: Storico segnali
# ============================================================
elif page == "Storico segnali":
    st.title("Storico segnali")
    sig = _q("SELECT * FROM signals ORDER BY generated_at DESC LIMIT 500")
    if sig.empty:
        st.info("Nessun segnale storico.")
    else:
        st.dataframe(
            sig[["generated_at", "ticker", "horizon", "decision", "score",
                  "regime", "quality_ok"]],
            use_container_width=True,
        )

# ============================================================
# Pagina 4: Trade chiusi & Learning
# ============================================================
elif page == "Trade chiusi & Learning":
    st.title("Trade chiusi & lezioni apprese")

    closed = pd.DataFrame(get_closed_trades(500))
    if closed.empty:
        st.info("Nessun trade chiuso.")
    else:
        st.subheader("Trade chiusi")
        st.dataframe(
            closed[["closed_at", "ticker", "quantity", "price", "close_price",
                     "pnl", "pnl_pct", "close_reason", "error_class"]],
            use_container_width=True,
        )

        st.subheader("Classificazione errori")
        summary = error_class_summary()
        if summary:
            df_sum = pd.DataFrame(
                [(k, v) for k, v in summary.items()],
                columns=["error_class", "count"],
            )
            fig = px.bar(df_sum, x="error_class", y="count",
                          color="error_class", title="Distribuzione errori")
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("Proposte di adattamento pesi")
    le = _q("SELECT * FROM learning_events ORDER BY timestamp DESC LIMIT 50")
    if le.empty:
        st.write("Nessuna proposta di adattamento ancora.")
    else:
        st.dataframe(le, use_container_width=True)

# ============================================================
# Pagina 5: Backtest
# ============================================================
elif page == "Backtest":
    st.title("Backtest")
    st.caption("Test storico della strategia su un singolo ticker (technical + ML).")
    from src.data.universe import tickers as universe_tickers
    from src.backtest.engine import backtest_ticker

    t = st.selectbox("Ticker", universe_tickers())
    start = st.date_input("Inizio", value=datetime(2021, 1, 1))
    if st.button("Esegui backtest"):
        with st.spinner("Backtest in corso…"):
            res = backtest_ticker(t, start=str(start))
        if res is None:
            st.error("Backtest fallito (dati insufficienti).")
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total return", f"{res.total_return:.2%}")
            c2.metric("Sharpe", f"{res.sharpe:.2f}")
            c3.metric("Max DD", f"{res.max_drawdown:.2%}")
            c4.metric("Win rate", f"{res.win_rate:.2%}")
            st.line_chart(res.equity_curve)

# ============================================================
# Pagina 6: Impostazioni
# ============================================================
elif page == "Impostazioni":
    st.title("Impostazioni")
    st.subheader("Capitale")
    st.write(f"Iniziale: **{cfg['capital']['initial']} EUR**")
    st.write(f"Riserva liquida: {cfg['capital']['cash_reserve_pct']:.0%}")
    st.write(f"Allocazione: S/M {cfg['capital']['allocation']['short_medium']:.0%}, "
              f"Long {cfg['capital']['allocation']['long_term']:.0%}")

    st.subheader("Pesi multi-fattore")
    st.json(cfg["weights"])

    st.subheader("Risk management")
    st.json(cfg["risk"])

    st.subheader("Universo titoli")
    from src.data.universe import load_universe
    u = pd.DataFrame([{
        "ticker": a.ticker, "name": a.name, "sector": a.sector,
        "mercato": a.market, "borsa": a.exchange,
        "affidabilita": a.reliability, "size_mult": f"{a.size_multiplier():.0%}",
    } for a in load_universe()])
    st.caption(f"Totale: {len(u)} titoli su {u['borsa'].nunique()} borse")
    st.dataframe(u, use_container_width=True)
