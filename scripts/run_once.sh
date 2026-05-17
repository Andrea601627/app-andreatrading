#!/usr/bin/env bash
# Esegue un singolo ciclo di analisi+decisione+esecuzione (paper).
set -e
cd "$(dirname "$0")/.."
python -m src.orchestrator.main_loop --once
