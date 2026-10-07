"""JSONL traces for every request. Privacy by design: a trace holds a salted HASH of the raw query and the
PII-MASKED query - never the raw text. The salt (BANKRAG_TRACE_SALT) lets you correlate repeats of the
same query without being able to brute-force short identifiers such as NRICs from an unsalted hash.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

DEV_SALT = "bankrag-dev-salt-change-me"


def query_hash(raw_query: str) -> str:
    salt = os.environ.get("BANKRAG_TRACE_SALT", DEV_SALT).encode()
    return hmac.new(salt, raw_query.encode(), hashlib.sha256).hexdigest()[:16]


class StageTimer:
    def __init__(self):
        self.ms: dict[str, float] = {}

    @contextmanager
    def stage(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.ms[name] = round((time.perf_counter() - start) * 1000, 3)

    @property
    def total(self) -> float:
        return round(sum(self.ms.values()), 3)


class Tracer:
    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict) -> None:
        if self.path:
            with self.path.open("a") as f:
                f.write(json.dumps(record, sort_keys=True) + "\n")


def read_traces(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
