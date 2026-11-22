"""Tamper-evident audit trail: an append-only SQLite table where every event is chained to the previous one.

    hash_n = HMAC(key, hash_{n-1} || canonical_json(seq, ts, event_type, request_id, subject_id, payload))

Three layers, each covering what the previous one cannot:
  1. Triggers make the table append-only: UPDATE and DELETE are refused by the database itself.
     Anyone with file access can drop a trigger, so...
  2. ...the hash chain makes any edit, deletion or reordering detectable by `bankrag audit verify`.
     Anyone who knows the algorithm could recompute every hash after an edit, so...
  3. ...the chain is keyed (BANKRAG_AUDIT_KEY, from a secret store). Without the key a rewritten chain does
     not verify. Cutting events off the END leaves a valid shorter chain, so `bankrag audit head` prints the
     latest (seq, hash) for export to separate, write-once storage, and `verify --anchor` checks against it.

Payloads never hold personal data: ids, decisions, reason codes, versions and salted hashes only. The
audit log answers "what did the system decide, why, and with which versions" for every customer
interaction, and can be queried with plain SQL (sql/*.sql).

Writes are serialised with BEGIN IMMEDIATE, so several threads or worker processes can share one file.
If an event cannot be written the caller gets an exception: a decision is not returned without its record.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

GENESIS = "0" * 64
DEV_KEY = "bankrag-dev-audit-key-change-me"

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    seq         INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    request_id  TEXT,
    subject_id  TEXT,
    payload     TEXT NOT NULL,
    prev_hash   TEXT NOT NULL,
    hash        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_events_type_ts ON audit_events (event_type, ts);
CREATE INDEX IF NOT EXISTS audit_events_subject ON audit_events (subject_id);
CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events
    BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events
    BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;

-- Duplicate-submission registry (bankrag.docverify.registry). An index, not evidence: every key is also
-- in the payload of the chained document_decision event, so the registry can be rebuilt from the log.
CREATE TABLE IF NOT EXISTS document_registry (
    key             TEXT NOT NULL,
    application_id  TEXT NOT NULL,
    identity_hash   TEXT NOT NULL,
    PRIMARY KEY (key, application_id)
);
"""


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class VerifyResult:
    ok: bool
    n_events: int
    head_seq: int
    head_hash: str
    first_bad_seq: int | None = None
    problem: str | None = None

    def to_json(self) -> dict:
        return self.__dict__.copy()


class AuditLog:
    def __init__(self, path: str | Path, key: str | None = None, clock: Callable[[], str] = _now):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._key = (key or os.environ.get("BANKRAG_AUDIT_KEY") or DEV_KEY).encode()
        self.keyed_with_dev_key = self._key == DEV_KEY.encode()
        self._clock = clock
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def _hash(self, prev: str, seq: int, ts: str, event_type: str, request_id, subject_id, payload: str) -> str:
        body = _canonical([seq, ts, event_type, request_id, subject_id, payload])
        return hmac.new(self._key, (prev + body).encode(), hashlib.sha256).hexdigest()

    def append(self, event_type: str, payload: dict, request_id: str | None = None, subject_id: str | None = None,
               ts: str | None = None) -> tuple[int, str]:
        body = _canonical(payload)
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                last = self.conn.execute("SELECT seq, hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
                seq, prev = (last["seq"] + 1, last["hash"]) if last else (1, GENESIS)
                ts = ts or self._clock()
                h = self._hash(prev, seq, ts, event_type, request_id, subject_id, body)
                self.conn.execute("INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                  (seq, ts, event_type, request_id, subject_id, body, prev, h))
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
        return seq, h

    def head(self) -> tuple[int, str]:
        with self._lock:
            row = self.conn.execute("SELECT seq, hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
        return (row["seq"], row["hash"]) if row else (0, GENESIS)

    def verify(self, anchor: tuple[int, str] | None = None) -> VerifyResult:
        """Walk the whole chain. With an anchor (seq, hash) exported earlier, also detect removed tail events."""
        prev, expected_seq, n = GENESIS, 1, 0
        for row in self.conn.execute("SELECT * FROM audit_events ORDER BY seq"):
            n += 1
            seq = row["seq"]
            if seq != expected_seq:
                return VerifyResult(False, n, seq, prev, expected_seq, f"sequence gap: expected {expected_seq}, found {seq}")
            if row["prev_hash"] != prev:
                return VerifyResult(False, n, seq, prev, seq, "prev_hash does not match the previous event")
            want = self._hash(prev, seq, row["ts"], row["event_type"], row["request_id"], row["subject_id"], row["payload"])
            if not hmac.compare_digest(want, row["hash"]):
                return VerifyResult(False, n, seq, prev, seq, "hash mismatch: event content changed or chain re-keyed")
            prev, expected_seq = row["hash"], seq + 1
        head_seq = expected_seq - 1
        if anchor is not None:
            a_seq, a_hash = anchor
            if head_seq < a_seq:
                return VerifyResult(False, n, head_seq, prev, head_seq + 1, f"log ends at {head_seq} but the anchor is at {a_seq}: events removed")
            row = self.conn.execute("SELECT hash FROM audit_events WHERE seq = ?", (a_seq,)).fetchone()
            if a_seq > 0 and (row is None or row["hash"] != a_hash):
                return VerifyResult(False, n, head_seq, prev, a_seq, "event at the anchor does not match the exported hash")
        return VerifyResult(True, n, head_seq, prev)

    def query(self, sql: str, params: tuple | dict = ()) -> tuple[list[str], list[tuple]]:
        with self._lock:                 # one connection is shared by the server's threads
            cur = self.conn.execute(sql, params)
            cols = [d[0] for d in cur.description] if cur.description else []
            return cols, [tuple(r) for r in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()


# ------------------------------------------------------------------------------------------- event payloads
def rag_event(resp, retrieval_mode: str) -> dict:
    """What to keep about one answered / abstained / blocked question. No question text, masked or not."""
    t = resp.trace
    return {"trace_id": t["trace_id"], "query_hash": t["query_hash"], "outcome": "blocked" if resp.blocked else
            "abstained" if resp.abstained else "answered", "reason": resp.reason, "confidence": t["top_score"],
            "abstain_threshold": t["abstain_threshold"], "citations": resp.citations, "pii_types": resp.pii_types,
            "injection_rules": resp.injection_rules, "retrieval_mode": retrieval_mode, "provider": t["provider"],
            "model": t["model"], "prompt_version": t["prompt_version"], "prompt_sha": t["prompt_sha"]}


def document_event(verdict, application_id: str, registry_keys: list[str]) -> dict:
    """What to keep about one document decision. No names, NRICs or amounts: codes, versions and hashes."""
    return {"application_id": application_id, "doc_type": verdict.doc_type, "decision": verdict.decision,
            "rules": [r["rule"] for r in verdict.reasons], "codes": verdict.codes, "rules_version": verdict.rules_version,
            "anomaly_score": verdict.anomaly_score, "extraction_method": verdict.extraction_method,
            "file_sha256": verdict.sha256, "registry_keys": registry_keys, "checks_run": verdict.checks_run}
