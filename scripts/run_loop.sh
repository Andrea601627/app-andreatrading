#!/usr/bin/env bash
# Esegue il loop continuo (rispetta cycle_interval_seconds in config).
set -e
cd "$(dirname "$0")/.."
python -m src.orchestrator.main_loop
