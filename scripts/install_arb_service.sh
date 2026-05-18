#!/bin/bash
# Installa e avvia il servizio andreatrading-arb sul server
set -e

cat > /etc/systemd/system/andreatrading-arb.service << 'EOF'
[Unit]
Description=AndreaTrading Arbitrage Loop
After=network.target andreatrading-fast.service

[Service]
Type=simple
User=root
WorkingDirectory=/root/app-andreatrading
ExecStart=/root/app-andreatrading/.venv/bin/python -m src.orchestrator.arb_loop
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable andreatrading-arb
systemctl start andreatrading-arb
echo "Servizio andreatrading-arb installato e avviato"
systemctl status andreatrading-arb --no-pager
