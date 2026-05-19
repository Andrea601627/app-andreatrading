"""Dashboard Streamlit - vista principale "VENDI X / COMPRA Y" + drill-down."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# permette di lanciare con `streamlit run dashboard/app.py` da qualunque cwd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from scipy import stats
from streamlit_autorefresh import st_autorefresh

from src.risk.drawdown_guard import is_blocked, resolve
from src.utils.config import load_config
from src.utils.db import connect, init_db

st.set_page_config(
    page_title="AndreaTrading",
    page_icon=":chart_with_upwards_trend:",
    layout="wide",
)

# Auto-refresh ogni 60 secondi (allineato al ciclo fast loop)
_refresh_count = st_autorefresh(interval=60_000, key="autorefresh")


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
st.sidebar.caption(f"Aggiornato: {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")
page = st.sidebar.radio(
    "Sezione",
    ["Azioni consigliate", "Rendimento Mercati", "Portafoglio", "Storico Trade",
     "Storico segnali", "Statistiche Avanzate", "Backtest", "Impostazioni"],
)

if is_blocked():
    st.sidebar.error("CIRCUIT BREAKER ATTIVO")
    if st.sidebar.button("Sblocca (mia responsabilità)"):
        resolve(resolved_by="user_dashboard")
        st.rerun()

# ============================================================
# Pagina 1: Azioni consigliate (Trend Following)
# ============================================================
if page == "Azioni consigliate":
    st.title("Titoli in trend ora")
    st.caption("Top titoli per momentum di prezzo — aggiornato ad ogni ciclo (60s)")

    from src.data.screener import run as screener_run
    from src.data.universe import load_universe

    universe_map = {a.ticker: a for a in load_universe()}

    with st.spinner("Calcolo trend in corso..."):
        screened = screener_run(top_n=200)

    # Filtra solo titoli con dati e calcola score composito
    rows_trend = []
    for s in screened:
        if not s.cached:
            continue
        score = round((s.return_5d * 0.70 + s.return_1d * 0.30) * 100, 2)
        asset = universe_map.get(s.ticker)
        rows_trend.append({
            "ticker": s.ticker,
            "nome": asset.name if asset else "",
            "mercato": asset.market if asset else "",
            "settore": asset.sector if asset else "",
            "trend_5gg_%": round(s.return_5d * 100, 2),
            "slancio_1gg_%": round(s.return_1d * 100, 2),
            "score_trend": score,
        })

    rows_trend.sort(key=lambda x: x["score_trend"], reverse=True)
    df_trend = pd.DataFrame(rows_trend)

    if df_trend.empty:
        st.info("Nessun dato in cache. Attendi il primo ciclo del loop.")
    else:
        # Posizioni già aperte
        open_tickers = set(_q("SELECT ticker FROM positions")["ticker"].tolist()) if not _q("SELECT ticker FROM positions").empty else set()

        in_trend    = df_trend[df_trend["score_trend"] > 0].head(30)
        contro_trend = df_trend[df_trend["score_trend"] < 0].tail(15).iloc[::-1]

        col1, col2 = st.columns(2)
        with col1:
            st.subheader(f":green[IN TREND — possibili acquisti] ({len(in_trend)})")
            def _color_trend(val):
                if pd.isna(val): return ""
                return "color: #00c853" if val > 0 else "color: #d32f2f"
            try:
                styled = in_trend.style.map(_color_trend, subset=["trend_5gg_%", "slancio_1gg_%", "score_trend"])
            except AttributeError:
                styled = in_trend.style.applymap(_color_trend, subset=["trend_5gg_%", "slancio_1gg_%", "score_trend"])
            st.dataframe(styled, use_container_width=True, height=500)

        with col2:
            st.subheader(f":red[CONTRO TREND — da evitare/vendere] ({len(contro_trend)})")
            try:
                styled2 = contro_trend.style.map(_color_trend, subset=["trend_5gg_%", "slancio_1gg_%", "score_trend"])
            except AttributeError:
                styled2 = contro_trend.style.applymap(_color_trend, subset=["trend_5gg_%", "slancio_1gg_%", "score_trend"])
            st.dataframe(styled2, use_container_width=True, height=500)

        if open_tickers:
            st.subheader("Posizioni aperte vs trend attuale")
            pos_trend = df_trend[df_trend["ticker"].isin(open_tickers)]
            if not pos_trend.empty:
                try:
                    styled3 = pos_trend.style.map(_color_trend, subset=["trend_5gg_%", "slancio_1gg_%", "score_trend"])
                except AttributeError:
                    styled3 = pos_trend.style.applymap(_color_trend, subset=["trend_5gg_%", "slancio_1gg_%", "score_trend"])
                st.dataframe(styled3, use_container_width=True)

# ============================================================
# Pagina 2: Rendimento Mercati
# ============================================================
elif page == "Rendimento Mercati":
    st.title("Rendimento per mercato")
    st.caption("Basato sui prezzi in cache locale — aggiornato ad ogni ciclo del loop lento")

    from src.data.screener import run as screener_run
    from src.data.universe import load_universe

    universe_map = {a.ticker: a for a in load_universe()}

    with st.spinner("Calcolo rendimenti in corso..."):
        results = screener_run(top_n=len(universe_map))

    if not results:
        st.info("Nessun dato in cache. Attendi il primo ciclo del loop lento.")
    else:
        rows = []
        for r in results:
            asset = universe_map.get(r.ticker)
            if asset and r.cached:
                rows.append({
                    "ticker": r.ticker,
                    "name": asset.name,
                    "mercato": asset.market,
                    "borsa": asset.exchange,
                    "settore": asset.sector,
                    "affidabilita": asset.reliability,
                    "rendimento_5gg_%": round(r.return_5d * 100, 2),
                    "rendimento_1gg_%": round(r.return_1d * 100, 2),
                })

        df = pd.DataFrame(rows)

        if df.empty:
            st.info("Nessun dato disponibile ancora in cache.")
        else:
            # --- Riepilogo per mercato ---
            st.subheader("Riepilogo per mercato")
            mkt = (df.groupby("mercato")
                     .agg(
                         rendimento_medio_5gg=("rendimento_5gg_%", "mean"),
                         rendimento_max_5gg=("rendimento_5gg_%", "max"),
                         titoli_positivi=("rendimento_5gg_%", lambda x: (x > 0).sum()),
                         totale_titoli=("rendimento_5gg_%", "count"),
                     )
                     .reset_index()
                     .sort_values("rendimento_medio_5gg", ascending=False))
            mkt["rendimento_medio_5gg"] = mkt["rendimento_medio_5gg"].round(2)
            mkt["rendimento_max_5gg"] = mkt["rendimento_max_5gg"].round(2)
            mkt["% positivi"] = (mkt["titoli_positivi"] / mkt["totale_titoli"] * 100).round(0).astype(int)

            st.dataframe(mkt, use_container_width=True)

            fig_mkt = px.bar(
                mkt, x="mercato", y="rendimento_medio_5gg",
                color="rendimento_medio_5gg",
                color_continuous_scale=["red", "gray", "green"],
                color_continuous_midpoint=0,
                title="Rendimento medio 5 giorni per mercato (%)",
                labels={"rendimento_medio_5gg": "Rend. medio 5gg (%)"},
            )
            st.plotly_chart(fig_mkt, use_container_width=True)

            # --- Riepilogo per settore ---
            st.subheader("Riepilogo per settore")
            sec = (df.groupby("settore")
                     .agg(
                         rendimento_medio_5gg=("rendimento_5gg_%", "mean"),
                         totale_titoli=("rendimento_5gg_%", "count"),
                     )
                     .reset_index()
                     .sort_values("rendimento_medio_5gg", ascending=False))
            sec["rendimento_medio_5gg"] = sec["rendimento_medio_5gg"].round(2)

            fig_sec = px.bar(
                sec, x="settore", y="rendimento_medio_5gg",
                color="rendimento_medio_5gg",
                color_continuous_scale=["red", "gray", "green"],
                color_continuous_midpoint=0,
                title="Rendimento medio 5 giorni per settore (%)",
            )
            st.plotly_chart(fig_sec, use_container_width=True)

            # --- Top 20 titoli ---
            st.subheader("Top 20 titoli (rendimento 5 giorni)")
            top20 = df.nlargest(20, "rendimento_5gg_%")[
                ["ticker", "name", "mercato", "settore",
                 "rendimento_5gg_%", "rendimento_1gg_%", "affidabilita"]
            ]
            st.dataframe(top20, use_container_width=True)

            # --- Peggiori 20 titoli ---
            st.subheader("Peggiori 20 titoli (rendimento 5 giorni)")
            bot20 = df.nsmallest(20, "rendimento_5gg_%")[
                ["ticker", "name", "mercato", "settore",
                 "rendimento_5gg_%", "rendimento_1gg_%", "affidabilita"]
            ]
            st.dataframe(bot20, use_container_width=True)

# ============================================================
# Pagina 3: Portafoglio
# ============================================================
elif page == "Portafoglio":
    st.title("Portafoglio attuale")
    pos = _q("SELECT ticker, quantity, avg_price, horizon, opened_at, stop_loss, take_profit, high_water_mark FROM positions")
    if pos.empty:
        st.info("Nessuna posizione aperta.")
    else:
        # Arricchisce con P&L non realizzato usando l'ultimo prezzo registrato nell'equity curve
        last_eq = _q("SELECT cash, positions_value, total_equity FROM equity_curve ORDER BY timestamp DESC LIMIT 1")
        pos["valore_posizione"] = pos["quantity"] * pos["avg_price"]
        pos["pnl_unreal_pct"] = 0.0
        # Calcola P&L non realizzato da high_water_mark se disponibile
        mask = pos["high_water_mark"].notna() & (pos["avg_price"] > 0)
        pos.loc[mask, "pnl_unreal_pct"] = (
            (pos.loc[mask, "high_water_mark"] - pos.loc[mask, "avg_price"])
            / pos.loc[mask, "avg_price"] * 100
        ).round(2)

        def _color_pnl_pos(val):
            if pd.isna(val) or val == 0:
                return ""
            return "color: #00c853" if val > 0 else "color: #d32f2f"

        try:
            styled_pos = pos.style.map(_color_pnl_pos, subset=["pnl_unreal_pct"])
        except AttributeError:
            styled_pos = pos.style.applymap(_color_pnl_pos, subset=["pnl_unreal_pct"])
        st.dataframe(styled_pos, use_container_width=True)

    st.subheader("Rendimento in tempo reale")
    eq = _q("SELECT * FROM equity_curve ORDER BY timestamp")
    if not eq.empty:
        eq["timestamp"] = pd.to_datetime(eq["timestamp"])
        # Usa il primo punto registrato come base zero
        initial = eq.iloc[0]["total_equity"]
        eq["rendimento_eur"] = eq["total_equity"] - initial

        last = eq.iloc[-1]
        rend_eur = last["rendimento_eur"]
        rend_pct = (rend_eur / initial * 100) if initial > 0 else 0

        # Asse Y centrato sullo zero (simmetrico)
        max_abs = max(abs(eq["rendimento_eur"].max()), abs(eq["rendimento_eur"].min()), 1.0)
        y_range = [-max_abs * 1.3, max_abs * 1.3]

        # Asse X: presente al centro — metà storia a sinistra, metà futuro a destra
        now = pd.Timestamp.utcnow().tz_localize(None)
        t_start = eq["timestamp"].min()
        half_span = now - t_start          # durata della storia registrata
        x_range = [t_start, now + half_span]   # presente al centro

        # Colore linea: verde se positivo, rosso se negativo
        line_color = "#00c853" if rend_eur >= 0 else "#d32f2f"

        fig = px.line(
            eq, x="timestamp", y="rendimento_eur",
            title=f"Rendimento: {'+' if rend_eur >= 0 else ''}{rend_eur:.2f} € ({'+' if rend_pct >= 0 else ''}{rend_pct:.2f}%)",
            labels={"rendimento_eur": "Guadagno / Perdita (€)", "timestamp": ""},
        )
        fig.update_traces(line_color=line_color, line_width=2)
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
        # Linea verticale sul presente
        fig.add_vline(x=now, line_dash="dot", line_color="white", opacity=0.3)
        fig.update_layout(
            yaxis_ticksuffix=" €",
            yaxis_range=y_range,
            xaxis_range=x_range,
        )
        st.plotly_chart(fig, use_container_width=True)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Equity totale", f"{last['total_equity']:.2f} €")
        c2.metric("Cash", f"{last['cash']:.2f} €")
        c3.metric("Posizioni", f"{last['positions_value']:.2f} €")
        c4.metric("Drawdown", f"{last['drawdown_pct']:.2%}")

# ============================================================
# Pagina 4: Storico Trade
# ============================================================
elif page == "Storico Trade":
    st.title("Storico Trade")
    st.caption("Ultimi 500 trade eseguiti dal sistema (acquisti e vendite)")

    trades = _q("""
        SELECT
            executed_at        AS "Data/Ora",
            ticker             AS "Ticker",
            side               AS "Operazione",
            quantity           AS "Quantità",
            price              AS "Prezzo (€)",
            commission         AS "Comm. (€)",
            ROUND(quantity * price, 2) AS "Controvalore (€)",
            pnl                AS "P&L (€)",
            ROUND(pnl_pct * 100, 2)   AS "P&L (%)",
            close_reason       AS "Motivo chiusura",
            horizon            AS "Orizzonte"
        FROM trades
        ORDER BY executed_at DESC
        LIMIT 500
    """)

    if trades.empty:
        st.info("Nessun trade ancora eseguito.")
    else:
        # Metriche riepilogative
        tot_trades = len(trades)
        buys  = trades[trades["Operazione"] == "BUY"]
        sells = trades[trades["Operazione"] == "SELL"]
        pnl_tot = sells["P&L (€)"].sum() if not sells.empty else 0
        win_rate = (sells["P&L (€)"] > 0).mean() * 100 if not sells.empty else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Trade totali", tot_trades)
        c2.metric("Acquisti", len(buys))
        c3.metric("Vendite", len(sells))
        c4.metric("P&L realizzato", f"{'+' if pnl_tot >= 0 else ''}{pnl_tot:.2f} €")

        if not sells.empty:
            c5, c6 = st.columns(2)
            c5.metric("Win rate", f"{win_rate:.1f}%")
            c6.metric("Trade vincenti", int((sells['P&L (€)'] > 0).sum()))

        st.divider()

        # Tabella con colori P&L
        def _color_pnl(val):
            if pd.isna(val) or val == 0:
                return ""
            return "color: #00c853" if val > 0 else "color: #d32f2f"

        try:
            styled = trades.style.map(_color_pnl, subset=["P&L (€)", "P&L (%)"])
        except AttributeError:
            styled = trades.style.applymap(_color_pnl, subset=["P&L (€)", "P&L (%)"])
        st.dataframe(styled, use_container_width=True, height=600)

# ============================================================
# Pagina 5: Storico segnali
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
# Pagina: Statistiche Avanzate
# ============================================================
elif page == "Statistiche Avanzate":
    st.title("Statistiche Avanzate")
    st.caption("Analisi statistica completa del comportamento del sistema di trading")

    # Carica tutti i trade chiusi
    df_all = _q("""
        SELECT ticker, side, quantity, price, commission,
               executed_at, closed_at, close_price, pnl, pnl_pct,
               close_reason, horizon, score
        FROM trades
        WHERE side = 'SELL' AND pnl IS NOT NULL
        ORDER BY executed_at
    """)
    df_buys = _q("""
        SELECT ticker, executed_at, closed_at, price AS buy_price, score
        FROM trades WHERE side = 'BUY' AND closed_at IS NOT NULL
    """)

    if df_all.empty:
        st.info("Nessun trade chiuso ancora. Le statistiche appariranno dopo le prime vendite.")
    else:
        df_all["pnl"] = pd.to_numeric(df_all["pnl"], errors="coerce")
        df_all["pnl_pct"] = pd.to_numeric(df_all["pnl_pct"], errors="coerce") * 100
        df_all["executed_at"] = pd.to_datetime(df_all["executed_at"])
        df_all["closed_at"] = pd.to_datetime(df_all["closed_at"])

        wins = df_all[df_all["pnl"] > 0]
        losses = df_all[df_all["pnl"] <= 0]
        n = len(df_all)

        # ── SEZIONE 1: KPI Riepilogo ─────────────────────────────────
        st.subheader("Riepilogo generale")
        c1,c2,c3,c4,c5,c6 = st.columns(6)
        c1.metric("Trade totali", n)
        c2.metric("Win rate", f"{len(wins)/n*100:.1f}%")
        c3.metric("P&L totale", f"{df_all['pnl'].sum():.2f} €")
        avg_win  = wins["pnl"].mean()  if not wins.empty  else 0
        avg_loss = losses["pnl"].mean() if not losses.empty else 0
        profit_factor = abs(wins["pnl"].sum() / losses["pnl"].sum()) if not losses.empty and losses["pnl"].sum() != 0 else float("inf")
        c4.metric("Profit factor", f"{profit_factor:.2f}")
        c5.metric("Guadagno medio", f"{avg_win:.2f} €")
        c6.metric("Perdita media",  f"{avg_loss:.2f} €")

        c7,c8,c9,c10 = st.columns(4)
        best  = df_all.loc[df_all["pnl"].idxmax()]
        worst = df_all.loc[df_all["pnl"].idxmin()]
        c7.metric("Trade migliore",  f"{best['pnl']:.2f} € ({best['ticker']})")
        c8.metric("Trade peggiore",  f"{worst['pnl']:.2f} € ({worst['ticker']})")
        # Sharpe ratio (approssimato su serie P&L %)
        if df_all["pnl_pct"].std() > 0:
            sharpe = (df_all["pnl_pct"].mean() / df_all["pnl_pct"].std()) * (252 ** 0.5)
        else:
            sharpe = 0.0
        # Sortino (solo deviazione downside)
        downside = df_all[df_all["pnl_pct"] < 0]["pnl_pct"]
        sortino = (df_all["pnl_pct"].mean() / downside.std() * (252**0.5)) if len(downside) > 1 and downside.std() > 0 else 0.0
        c9.metric("Sharpe ratio",  f"{sharpe:.2f}")
        c10.metric("Sortino ratio", f"{sortino:.2f}")

        st.divider()

        # ── SEZIONE 2: Tempi di detenzione ───────────────────────────
        st.subheader("Analisi tempi di detenzione (holding time)")

        if not df_buys.empty:
            df_buys["executed_at"] = pd.to_datetime(df_buys["executed_at"])
            df_buys["closed_at"]   = pd.to_datetime(df_buys["closed_at"])
            df_buys["holding_min"] = (df_buys["closed_at"] - df_buys["executed_at"]).dt.total_seconds() / 60
            df_buys = df_buys[df_buys["holding_min"] > 0]

        if not df_buys.empty and len(df_buys) > 0:
            h = df_buys["holding_min"]
            media   = h.mean()
            mediana = h.median()
            try:
                moda_val = float(stats.mode(h.round(0), keepdims=True).mode[0])
            except Exception:
                moda_val = float(h.round(0).value_counts().idxmax())

            c1,c2,c3 = st.columns(3)
            c1.metric("Media",   f"{media:.1f} min  ({media/60:.1f}h)")
            c2.metric("Mediana", f"{mediana:.1f} min ({mediana/60:.1f}h)")
            c3.metric("Moda",    f"{moda_val:.0f} min ({moda_val/60:.1f}h)")

            # Unisci con esito per colorare il boxplot
            df_buys_ext = df_buys.copy()
            df_buys_ext = df_buys_ext.merge(
                df_all[["ticker","executed_at","pnl"]].rename(columns={"executed_at":"sell_at"}),
                left_on=["ticker","closed_at"], right_on=["ticker","sell_at"], how="left"
            )
            df_buys_ext["esito"] = df_buys_ext["pnl"].apply(
                lambda x: "Vincente" if (pd.notna(x) and x > 0) else "Perdente"
            )

            fig_box = px.box(
                df_buys_ext, x="esito", y="holding_min",
                color="esito",
                color_discrete_map={"Vincente": "#00c853", "Perdente": "#d32f2f"},
                title="Boxplot: tempo di detenzione per esito (minuti)",
                labels={"holding_min": "Minuti", "esito": ""},
                points="all",
            )
            fig_box.update_layout(showlegend=False)
            st.plotly_chart(fig_box, use_container_width=True)

            # Istogramma tempi
            fig_hist_t = px.histogram(
                df_buys_ext, x="holding_min", color="esito",
                color_discrete_map={"Vincente": "#00c853", "Perdente": "#d32f2f"},
                nbins=30, barmode="overlay", opacity=0.7,
                title="Distribuzione tempi di detenzione",
                labels={"holding_min": "Minuti"},
            )
            st.plotly_chart(fig_hist_t, use_container_width=True)
        else:
            st.info("Dati holding time non ancora disponibili.")

        st.divider()

        # ── SEZIONE 3: Distribuzione P&L ─────────────────────────────
        st.subheader("Distribuzione P&L e valori anomali")

        col1, col2 = st.columns(2)
        with col1:
            # Istogramma P&L con curva normale sovrapposta
            mu, sigma = df_all["pnl_pct"].mean(), df_all["pnl_pct"].std()
            x_range = np.linspace(df_all["pnl_pct"].min(), df_all["pnl_pct"].max(), 200)
            normal_curve = stats.norm.pdf(x_range, mu, sigma)

            fig_dist = go.Figure()
            fig_dist.add_trace(go.Histogram(
                x=df_all["pnl_pct"], histnorm="probability density",
                name="P&L reale", marker_color="#1976d2", opacity=0.6,
            ))
            fig_dist.add_trace(go.Scatter(
                x=x_range, y=normal_curve,
                mode="lines", name="Distribuzione normale attesa",
                line=dict(color="#ff6f00", width=2),
            ))
            fig_dist.add_vline(x=0, line_dash="dash", line_color="gray")
            fig_dist.update_layout(title="Distribuzione P&L % vs curva normale",
                                    xaxis_title="P&L %", yaxis_title="Densità")
            st.plotly_chart(fig_dist, use_container_width=True)

        with col2:
            # Valori anomali (Z-score > 2)
            z_scores = np.abs(stats.zscore(df_all["pnl_pct"].dropna()))
            outliers = df_all[z_scores > 2][["ticker","executed_at","pnl","pnl_pct","close_reason"]]
            st.markdown("**Valori anomali (Z-score > 2)**")
            if outliers.empty:
                st.success("Nessun valore anomalo rilevato.")
            else:
                def _color_pnl_out(val):
                    if pd.isna(val): return ""
                    return "color: #00c853" if val > 0 else "color: #d32f2f"
                try:
                    st.dataframe(outliers.style.map(_color_pnl_out, subset=["pnl","pnl_pct"]),
                                  use_container_width=True)
                except Exception:
                    st.dataframe(outliers, use_container_width=True)

            # Statistiche descrittive complete
            st.markdown("**Statistiche descrittive P&L %**")
            desc = df_all["pnl_pct"].describe(percentiles=[.1,.25,.5,.75,.9])
            desc.index = ["Conteggio","Media","Dev. std","Min","10°%","25°%","50°%","75°%","90°%","Max"]
            st.dataframe(desc.round(3).to_frame("Valore"), use_container_width=True)

        st.divider()

        # ── SEZIONE 4: Regressione lineare ───────────────────────────
        st.subheader("Analisi di regressione: score di ingresso → P&L")
        st.caption("Studia se esiste una relazione tra la forza del segnale al momento dell'acquisto e il profitto ottenuto")

        df_reg = df_all[df_all["score"].notna() & df_all["pnl_pct"].notna()].copy()
        df_reg["score"] = pd.to_numeric(df_reg["score"], errors="coerce")
        df_reg = df_reg.dropna(subset=["score","pnl_pct"])

        if len(df_reg) >= 5:
            slope, intercept, r_value, p_value, std_err = stats.linregress(
                df_reg["score"], df_reg["pnl_pct"]
            )
            x_fit = np.linspace(df_reg["score"].min(), df_reg["score"].max(), 100)
            y_fit = slope * x_fit + intercept
            # Intervallo di confidenza 95%
            n_reg = len(df_reg)
            t_crit = stats.t.ppf(0.975, df=n_reg - 2)
            se_fit = std_err * np.sqrt(1/n_reg + (x_fit - df_reg["score"].mean())**2 /
                                        ((df_reg["score"] - df_reg["score"].mean())**2).sum())
            y_upper = y_fit + t_crit * se_fit
            y_lower = y_fit - t_crit * se_fit

            fig_reg = go.Figure()
            fig_reg.add_trace(go.Scatter(
                x=df_reg["score"], y=df_reg["pnl_pct"],
                mode="markers",
                marker=dict(
                    color=df_reg["pnl_pct"],
                    colorscale=[[0,"#d32f2f"],[0.5,"#888"],[1,"#00c853"]],
                    size=7, opacity=0.7,
                    colorbar=dict(title="P&L %"),
                ),
                name="Trade reali",
                text=df_reg["ticker"],
            ))
            fig_reg.add_trace(go.Scatter(
                x=x_fit, y=y_fit,
                mode="lines", name="Retta di regressione",
                line=dict(color="#ff6f00", width=2),
            ))
            fig_reg.add_trace(go.Scatter(
                x=np.concatenate([x_fit, x_fit[::-1]]),
                y=np.concatenate([y_upper, y_lower[::-1]]),
                fill="toself", fillcolor="rgba(255,111,0,0.1)",
                line=dict(color="rgba(0,0,0,0)"),
                name="Intervallo confidenza 95%",
            ))
            fig_reg.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
            fig_reg.update_layout(
                title="Grafico di dispersione: score ingresso vs P&L %",
                xaxis_title="Score al momento dell'acquisto",
                yaxis_title="P&L %",
            )
            st.plotly_chart(fig_reg, use_container_width=True)

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("R² (bontà fit)", f"{r_value**2:.3f}")
            c2.metric("p-value", f"{p_value:.4f}")
            c3.metric("Pendenza", f"{slope:.4f}")
            c4.metric("Intercetta", f"{intercept:.4f}")

            if p_value < 0.05:
                st.success(f"Relazione **statisticamente significativa** (p={p_value:.4f} < 0.05). "
                            f"{'Score più alto → miglior P&L' if slope > 0 else 'Score più alto → P&L peggiore'}.")
            else:
                st.warning(f"Relazione **non significativa** (p={p_value:.4f} > 0.05). "
                            "Lo score di ingresso non predice bene il P&L con i dati attuali.")

            # Previsione interattiva
            st.markdown("**Simulatore di previsione**")
            score_input = st.slider("Score ipotetico al momento dell'acquisto", -100, 100, 50)
            predicted = slope * score_input + intercept
            st.info(f"Con score = {score_input}, il modello stima un P&L di circa **{predicted:.2f}%**")
        else:
            st.info("Servono almeno 5 trade chiusi con score per la regressione.")

        st.divider()

        # ── SEZIONE 5: P&L per ora del giorno e motivo chiusura ──────
        st.subheader("Quando e perché il sistema guadagna o perde")
        col1, col2 = st.columns(2)

        with col1:
            df_all["ora"] = df_all["executed_at"].dt.hour
            by_hour = df_all.groupby("ora").agg(
                pnl_medio=("pnl", "mean"),
                n_trade=("pnl", "count"),
                win_rate=("pnl", lambda x: (x > 0).mean() * 100),
            ).reset_index()
            fig_h = px.bar(by_hour, x="ora", y="pnl_medio",
                           color="pnl_medio",
                           color_continuous_scale=["#d32f2f","#888","#00c853"],
                           color_continuous_midpoint=0,
                           title="P&L medio per ora del giorno",
                           labels={"ora":"Ora","pnl_medio":"P&L medio €"})
            st.plotly_chart(fig_h, use_container_width=True)

        with col2:
            by_reason = df_all.groupby("close_reason").agg(
                n=("pnl","count"),
                pnl_medio=("pnl","mean"),
                win_rate=("pnl", lambda x: (x>0).mean()*100),
            ).reset_index().sort_values("pnl_medio", ascending=False)
            fig_r = px.bar(by_reason, x="close_reason", y="pnl_medio",
                           color="pnl_medio",
                           color_continuous_scale=["#d32f2f","#888","#00c853"],
                           color_continuous_midpoint=0,
                           title="P&L medio per motivo di chiusura",
                           labels={"close_reason":"Motivo","pnl_medio":"P&L medio €"},
                           text="n")
            st.plotly_chart(fig_r, use_container_width=True)

        st.divider()

        # ── SEZIONE 6: Serie temporale e win rate rolling ─────────────
        st.subheader("Evoluzione nel tempo")
        col1, col2 = st.columns(2)

        with col1:
            df_all["pnl_cum"] = df_all["pnl"].cumsum()
            fig_cum = px.line(df_all, x="executed_at", y="pnl_cum",
                               title="P&L cumulato realizzato (€)",
                               labels={"executed_at":"Data","pnl_cum":"P&L cumulato €"})
            fig_cum.update_traces(line_color="#1976d2")
            fig_cum.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
            st.plotly_chart(fig_cum, use_container_width=True)

        with col2:
            window = max(5, n // 5)
            df_all["win_rolling"] = (df_all["pnl"] > 0).rolling(window).mean() * 100
            fig_wr = px.line(df_all, x="executed_at", y="win_rolling",
                              title=f"Win rate rolling (finestra {window} trade)",
                              labels={"executed_at":"Data","win_rolling":"Win rate %"})
            fig_wr.add_hline(y=50, line_dash="dash", line_color="gray", opacity=0.5)
            fig_wr.update_traces(line_color="#ff6f00")
            st.plotly_chart(fig_wr, use_container_width=True)

        st.divider()

        # ── SEZIONE 7: Serie consecutive ─────────────────────────────
        st.subheader("Serie vincenti e perdenti consecutive")
        outcomes = (df_all["pnl"] > 0).astype(int).tolist()
        max_win_streak = max_loss_streak = cur_w = cur_l = 0
        for o in outcomes:
            if o == 1:
                cur_w += 1; cur_l = 0
            else:
                cur_l += 1; cur_w = 0
            max_win_streak  = max(max_win_streak, cur_w)
            max_loss_streak = max(max_loss_streak, cur_l)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Max serie vincente",  f"{max_win_streak} trade")
        c2.metric("Max serie perdente",  f"{max_loss_streak} trade")
        c3.metric("Trade vincenti",  f"{len(wins)} ({len(wins)/n*100:.1f}%)")
        c4.metric("Trade perdenti", f"{len(losses)} ({len(losses)/n*100:.1f}%)")

        # Heatmap esito per ticker
        st.markdown("**P&L medio per ticker**")
        by_ticker = df_all.groupby("ticker").agg(
            n=("pnl","count"),
            pnl_totale=("pnl","sum"),
            pnl_medio=("pnl","mean"),
            win_rate=("pnl", lambda x: round((x>0).mean()*100,1)),
        ).reset_index().sort_values("pnl_totale", ascending=False)
        def _col(v):
            if pd.isna(v): return ""
            return "color: #00c853" if v > 0 else "color: #d32f2f"
        try:
            st.dataframe(by_ticker.style.map(_col, subset=["pnl_totale","pnl_medio"]),
                          use_container_width=True)
        except Exception:
            st.dataframe(by_ticker, use_container_width=True)

        st.divider()

        # ── SEZIONE 8: Analisi per mercato ───────────────────────────
        st.subheader("Come il software si muove tra i mercati")

        _SUFFIX_TO_MARKET = {
            ".MI": "Italia (MIL)", ".PA": "Francia (EPA)", ".DE": "Germania (XETRA)",
            ".L": "UK (LSE)", ".AS": "Olanda (AMS)", ".BR": "Belgio (EBR)",
            ".MC": "Spagna (BME)", ".T": "Giappone (TSE)", ".HK": "Hong Kong (HKEX)",
        }
        def _ticker_to_market(t: str) -> str:
            t_up = t.upper()
            for sfx, name in _SUFFIX_TO_MARKET.items():
                if t_up.endswith(sfx.upper()):
                    return name
            return "USA (NYSE/NASDAQ)"

        df_all["mercato"] = df_all["ticker"].apply(_ticker_to_market)

        by_mkt = df_all.groupby("mercato").agg(
            n_trade=("pnl", "count"),
            pnl_totale=("pnl", "sum"),
            pnl_medio=("pnl", "mean"),
            win_rate=("pnl", lambda x: round((x > 0).mean() * 100, 1)),
        ).reset_index().sort_values("n_trade", ascending=False)
        by_mkt["pnl_totale"] = by_mkt["pnl_totale"].round(2)
        by_mkt["pnl_medio"] = by_mkt["pnl_medio"].round(2)

        col1, col2 = st.columns(2)
        with col1:
            fig_mkt_n = px.bar(
                by_mkt, x="mercato", y="n_trade",
                title="Trade per mercato",
                labels={"n_trade": "N° trade", "mercato": ""},
                color="n_trade", color_continuous_scale="Blues",
            )
            fig_mkt_n.update_layout(showlegend=False)
            st.plotly_chart(fig_mkt_n, use_container_width=True)

        with col2:
            fig_mkt_pnl = px.bar(
                by_mkt, x="mercato", y="pnl_totale",
                title="P&L totale per mercato (€)",
                labels={"pnl_totale": "P&L €", "mercato": ""},
                color="pnl_totale",
                color_continuous_scale=["#d32f2f", "#888", "#00c853"],
                color_continuous_midpoint=0,
            )
            st.plotly_chart(fig_mkt_pnl, use_container_width=True)

        col3, col4 = st.columns(2)
        with col3:
            fig_mkt_wr = px.bar(
                by_mkt, x="mercato", y="win_rate",
                title="Win rate per mercato (%)",
                labels={"win_rate": "Win rate %", "mercato": ""},
                color="win_rate",
                color_continuous_scale=["#d32f2f", "#888", "#00c853"],
                color_continuous_midpoint=50,
            )
            fig_mkt_wr.add_hline(y=50, line_dash="dash", line_color="gray", opacity=0.5)
            st.plotly_chart(fig_mkt_wr, use_container_width=True)

        with col4:
            fig_pie = px.pie(
                by_mkt, names="mercato", values="n_trade",
                title="Distribuzione trade per mercato",
            )
            fig_pie.update_traces(textposition="inside", textinfo="percent+label")
            st.plotly_chart(fig_pie, use_container_width=True)

        try:
            st.dataframe(
                by_mkt.style.map(_col, subset=["pnl_totale", "pnl_medio"]),
                use_container_width=True,
            )
        except Exception:
            st.dataframe(by_mkt, use_container_width=True)

        st.divider()

        # ── SEZIONE 9: Performance per giorno della settimana ────────
        st.subheader("Quale giorno rende di più")

        _GIORNI = {0: "Lunedì", 1: "Martedì", 2: "Mercoledì",
                   3: "Giovedì", 4: "Venerdì", 5: "Sabato", 6: "Domenica"}
        df_all["giorno_n"] = df_all["executed_at"].dt.weekday
        df_all["giorno"] = df_all["giorno_n"].map(_GIORNI)
        by_day = df_all.groupby(["giorno_n","giorno"]).agg(
            n_trade=("pnl","count"),
            pnl_medio=("pnl","mean"),
            win_rate=("pnl", lambda x: round((x>0).mean()*100,1)),
        ).reset_index().sort_values("giorno_n")

        col1, col2 = st.columns(2)
        with col1:
            fig_day = px.bar(
                by_day, x="giorno", y="pnl_medio",
                title="P&L medio per giorno della settimana",
                labels={"pnl_medio": "P&L medio €", "giorno": ""},
                color="pnl_medio",
                color_continuous_scale=["#d32f2f", "#888", "#00c853"],
                color_continuous_midpoint=0,
                text=by_day["n_trade"].apply(lambda x: f"{x} tr"),
            )
            fig_day.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
            st.plotly_chart(fig_day, use_container_width=True)

        with col2:
            fig_day_wr = px.bar(
                by_day, x="giorno", y="win_rate",
                title="Win rate per giorno (%)",
                labels={"win_rate": "Win rate %", "giorno": ""},
                color="win_rate",
                color_continuous_scale=["#d32f2f", "#888", "#00c853"],
                color_continuous_midpoint=50,
            )
            fig_day_wr.add_hline(y=50, line_dash="dash", line_color="gray", opacity=0.5)
            st.plotly_chart(fig_day_wr, use_container_width=True)

        st.divider()

        # ── SEZIONE 10: Blacklist giornaliera attiva ─────────────────
        st.subheader("Blacklist giornaliera (ticker bloccati oggi)")
        st.caption("Ticker con 2+ trade in perdita oggi — il fast loop non li riaprirà fino a domani")

        df_bl = _q("""
            SELECT ticker, COUNT(*) AS perdite_oggi,
                   ROUND(SUM(pnl), 2) AS pnl_totale_oggi
            FROM trades
            WHERE side='SELL' AND pnl < 0 AND horizon='fast'
              AND date(closed_at) = date('now', 'localtime')
            GROUP BY ticker
            HAVING perdite_oggi >= 2
            ORDER BY pnl_totale_oggi
        """)
        if df_bl.empty:
            st.success("Nessun ticker in blacklist oggi.")
        else:
            st.warning(f"{len(df_bl)} ticker bloccati oggi per 2+ perdite consecutive")
            st.dataframe(df_bl, use_container_width=True)

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
