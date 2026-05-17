"""Caricamento e accesso alla configurazione YAML."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def load_config(path: str | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    local_path = cfg_path.parent / "local.yaml"
    if local_path.exists():
        with open(local_path, "r", encoding="utf-8") as f:
            local = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, local)

    # Env override per modalità live (sicurezza extra)
    if os.getenv("ANDREATRADING_MODE"):
        cfg["mode"] = os.getenv("ANDREATRADING_MODE")
    return cfg


def project_path(*parts: str) -> Path:
    return PROJECT_ROOT.joinpath(*parts)
