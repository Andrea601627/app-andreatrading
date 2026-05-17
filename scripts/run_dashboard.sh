#!/usr/bin/env bash
# Avvia il dashboard Streamlit.
set -e
cd "$(dirname "$0")/.."
streamlit run dashboard/app.py
