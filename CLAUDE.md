# AndreaTrading — Contesto progetto

## Cos'è
Sistema di trading automatico paper (simulato) per mercati azionari globali.
Repository: https://github.com/Andrea601627/app-andreatrading
Branch attivo: `claude/check-system-status-m3VV5`

## Server
- **IP**: 46.224.219.183 (Hetzner, Ubuntu 24.04)
- **Accesso**: `ssh root@46.224.219.183`
- **Cartella codice**: `/root/app-andreatrading`
- **Capitale simulato**: 100.000 EUR (paper trading, nessun soldo reale)

## Servizi systemd
```bash
andreatrading-loop      # loop lento: analisi ogni 15 min (900s)
andreatrading-fast      # fast loop: momentum ogni 60s
andreatrading-dashboard # dashboard Streamlit
```
Aggiornare il server:
```bash
cd /root/app-andreatrading && git pull origin claude/check-system-status-m3VV5 && sudo systemctl restart andreatrading-loop andreatrading-fast andreatrading-dashboard
```

## Architettura
```
src/
  orchestrator/
    main_loop.py      # loop lento 15 min — analisi fondamentale + tecnica
    fast_loop.py      # fast loop 60s — momentum trading su candele 5m
  strategy/
    momentum.py       # rilevamento momentum (soglia 0.1%, volume opzionale)
  execution/
    paper_broker.py   # broker simulato (cash = 100k - acquisti + vendite)
  risk/
    profit_guard.py   # hard stop -1.5%, profit lock +1.2% + trailing 0.4%
    drawdown_guard.py # circuit breaker se drawdown > 15%
config/
  default.yaml        # tutti i parametri
dashboard/
  app.py              # Streamlit: Azioni consigliate, Portafoglio, Storico Trade...
```

## Parametri momentum (config/default.yaml)
- Candele: 5 minuti, lookback 3 candele (= 15 min reali)
- Soglia minima: 0.1% di movimento
- Soglia forte: 0.4%
- Volume: opzionale (se assente riduce sizing del 30%)
- Max posizioni fast: 5
- Sizing: 8% (forte), 5% (medio), 3% (debole) del capitale

## Fix importanti già applicati
1. **Timezone bug**: `datetime.now()` usava UTC invece di `Europe/Rome` → orari mercato sbagliati
2. **Mercati aperti**: la watchlist ora filtra solo titoli con mercato aperto (europei di giorno, USA dal pomeriggio)
3. **Candele 5m**: passato da 1m a 5m perché yfinance restituisce dati 1m stantii per mercati europei
4. **Volume opzionale**: il volume non blocca più i segnali, riduce solo il sizing
5. **Equity statica**: separato prezzo posizioni (usa ultimo disponibile anche di ieri) da filtro momentum (solo oggi)
6. **Equity aggiornata ogni 60s** dal fast loop invece che ogni 15 min

## Database SQLite
- Posizioni aperte: tabella `positions`
- Trade eseguiti: tabella `trades`
- Segnali: tabella `signals`
- Equity curve: tabella `equity_curve`

Reset equity curve (se necessario):
```bash
cd /root/app-andreatrading && .venv/bin/python3 -c "from src.utils.db import connect
with connect() as c: c.execute('DELETE FROM equity_curve')"
```

## Dashboard
Accessibile su: `http://46.224.219.183:8501`
Pagine: Azioni consigliate | Rendimento Mercati | Portafoglio | Storico Trade | Storico segnali | Backtest | Impostazioni

## Comando diagnostica rapida
```bash
ssh root@46.224.219.183 "journalctl -u andreatrading-fast -n 5 --no-pager -l | grep -E 'watchlist|BUY|SELL|momentum|Mercati'"
```
