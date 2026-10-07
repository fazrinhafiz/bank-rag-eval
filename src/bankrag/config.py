"""Config loading. Paths in config.yaml are resolved relative to the config file's directory."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def config_path() -> Path:
    return Path(os.environ.get("BANKRAG_CONFIG", PROJECT_ROOT / "config.yaml")).resolve()


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path) if path else config_path()
    cfg = yaml.safe_load(path.read_text())
    cfg["_root"] = str(path.parent)
    cfg["_path"] = str(path)
    provider = os.environ.get("BANKRAG_PROVIDER")
    if provider:
        cfg["generation"]["provider"] = provider
    if os.environ.get("BANKRAG_MODEL"):
        cfg["generation"]["model"] = os.environ["BANKRAG_MODEL"]
    return cfg


def resolve(cfg: dict, key_or_path: str) -> Path:
    value = cfg.get(key_or_path, key_or_path)
    p = Path(value)
    return p if p.is_absolute() else Path(cfg["_root"]) / p


def with_overrides(cfg: dict, **overrides) -> dict:
    """Deep copy with dotted-key overrides, e.g. with_overrides(cfg, **{"retrieval.mode": "bm25"})."""
    out = copy.deepcopy(cfg)
    for dotted, value in overrides.items():
        node = out
        *parents, leaf = dotted.split(".")
        for p in parents:
            node = node[p]
        node[leaf] = value
    return out


def config_fingerprint(cfg: dict) -> str:
    public = {k: v for k, v in cfg.items() if not k.startswith("_")}
    return hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()[:12]


def write_abstain_threshold(path: str | Path, value: float) -> None:
    """Update one line in place so the comments in config.yaml survive."""
    path = Path(path)
    text = path.read_text()
    new, n = re.subn(r"(?m)^abstain_threshold:.*$",
                     f"abstain_threshold: {value:.4f}  # written by `bankrag calibrate` (dev split)", text)
    if n != 1:
        raise ValueError(f"expected exactly one abstain_threshold line in {path}")
    path.write_text(new)
