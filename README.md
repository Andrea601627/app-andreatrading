# AndreaTrading

Sistema di trading automatico per Borsa Italiana (FTSE MIB) — analisi
multi-fattoriale (tecnica + fondamentale + ML + sentiment), risk management
con circuit breaker, learning loop adattivo e dashboard interattiva.

**Default: paper trading.** Modalità live (Interactive Brokers) richiede
attivazione esplicita + credenziali.

---

## Garanzie sul rischio

Il sistema rispetta per costruzione il vincolo:

> *La perdita massima possibile = capitale investito (mai oltre).*

Garantito da:
1. **Universo cash-only** (azioni + ETF UCITS). Nessun derivato/leva/short.
2. **Position sizing** conservativo (max 10% capitale per titolo, max 30% per settore).
3. **Stop-loss ATR-based** + **trailing stop** automatici su ogni posizione.
4. **Circuit breaker globale** (drawdown >15% dal picco → chiude tutto e si blocca).

---

## Architettura

```
src/
├── data/          # fetch (yfinance) + validazione qualità + cache
├── analysis/      # technical, fundamental, ml_forecast, sentiment, regime
├── strategy/      # aggregator multi-fattore + decision + allocation
├── risk/          # position sizing, stop-loss, drawdown guard, concentration
├── execution/     # broker base + paper + IBKR + order manager
├── learning/      # journal + postmortem + adaptive weights
├── backtest/      # walk-forward engine
├── orchestrator/  # main loop: fetch → analyze → decide → execute → learn
└── utils/         # config, logger, sqlite db
dashboard/         # Streamlit UI
config/            # YAML config + universe CSV
```

---

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Configurazione: `config/default.yaml` (override locale: crea `config/local.yaml`).

---

## Uso

### Ciclo singolo (analisi + decisione + ordini paper)

```bash
python -m src.orchestrator.main_loop --once
```

### Loop continuo

```bash
python -m src.orchestrator.main_loop
```

### Dashboard

```bash
streamlit run dashboard/app.py
```

Sezioni del dashboard:
- **Azioni consigliate**: top BUY/SELL del momento, con drill-down sulle motivazioni
- **Portafoglio**: posizioni aperte, equity curve, drawdown
- **Storico segnali**: tutti i segnali generati
- **Trade chiusi & Learning**: P&L per trade + classificazione errori (postmortem)
- **Backtest**: test storico per singolo ticker
- **Impostazioni**: visualizza pesi, risk config, universo

---

## Logica decisionale

Per ogni titolo, ogni dimensione genera uno **score [-100, +100]**:
- Tecnica (EMA, RSI, MACD, Bollinger, ADX, OBV)
- Fondamentale (P/E, P/B, ROE, debt/equity, margins, growth)
- ML forecast (Holt-Winters + linear regression, ensemble 10 giorni)
- Sentiment (lessico finanziario IT/EN su news yfinance + Google RSS)

I 4 score vengono pesati in modo **differenziato per orizzonte**:

| Dimensione | Breve/medio (80% capitale) | Lungo (20% capitale) |
|---|---|---|
| Tecnica | 45% | 15% |
| Fondamentale | 25% | 55% |
| ML | 15% | 10% |
| Sentiment | 15% | 5% |

In **regime ad alta volatilità** i pesi tecnica/sentiment salgono, fondamentale scende.

Le confidence delle singole dimensioni modulano i pesi: se una dimensione ha
poca evidenza (es. pochi fondamentali) il suo peso effettivo scende e gli altri
si ridistribuiscono.

### Decisione finale

| Score finale | Azione |
|---|---|
| ≥ +70 | STRONG_BUY |
| ≥ +40 | BUY |
| da −25 a +40 | HOLD |
| ≤ −25 | SELL |
| ≤ −55 | STRONG_SELL |

**Override**: confidence < 0.30 → HOLD forzato.

---

## Learning engine (auto-correzione)

Ogni trade chiuso viene classificato in una di queste categorie:

| Classe | Significato | Azione |
|---|---|---|
| successful_trade | P&L positivo | nessuna |
| signal_error | segnale errato | calibra pesi/soglie |
| regime_mismatch | strategia inadatta al regime | gate per regime |
| black_swan | perdita estrema improvvisa | inevitabile |
| risk_sizing_error | stop colpito dal rumore | allarga ATR multiplier |
| bad_luck | perdita piccola con decisione ragionevole | nessuna correzione |

Periodicamente (`min_trades_before_adjust=30`) calcola la correlazione tra ogni
dimensione e i P&L; se una dimensione è significativamente predittiva al
contrario (p<0.05), propone di ridurne il peso. Modifiche >20% richiedono
**conferma manuale** dal dashboard (anti-overfitting).

---

## Modalità LIVE (Interactive Brokers)

⚠ **Solo dopo validazione su paper.**

1. Installa e avvia IB Gateway o TWS (paper o live).
2. Crea `config/local.yaml`:
   ```yaml
   mode: live
   broker:
     ibkr:
       port: 7497   # paper TWS; usa 7496 per live
   ```
3. Set env `ANDREATRADING_MODE=live` come safety extra.
4. Avvia `python -m src.orchestrator.main_loop`.

---

## Limiti onesti del sistema

- Dati gratis = **delay 15min** (yfinance). Per real-time vero serve abbonamento
  ai dati di Borsa Italiana via broker (~€5-50/mese).
- ML forecast su prezzi singoli ha potere predittivo limitato (peso 10-15%
  per questo).
- Sentiment basato su lessico semplice; per migliorarlo abilitare FinBERT
  (richiede ~500MB di modello).
- Backtest non include fundamentals/sentiment storici (solo technical + ML)
  perché non sono disponibili facilmente.
- Universo iniziale = FTSE MIB (~37 titoli). Estendibile aggiungendo file CSV
  in `config/universe/`.

---

## Disclaimer

Questo software è uno strumento personale di supporto alle decisioni di
investimento. **Non è consulenza finanziaria autorizzata**. Le decisioni —
anche quelle eseguite automaticamente — sono responsabilità dell'utente.
