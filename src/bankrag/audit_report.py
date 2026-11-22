"""Reporting over the audit log (sql/*.sql) and an end-to-end demonstration that tampering is detected.

    bankrag audit report        run every query in sql/ against the audit database, print markdown
    bankrag audit demo          build a fresh audit log from the test document corpus and the golden test
                                questions, report on it, then attack COPIES of it and check each attack is
                                caught; writes reports/audit_report.{md,json}; exit 1 if any check fails
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .audit import AuditLog
from .config import resolve

DEMO_KEY = "demo-key-not-a-secret"      # the demo's key is public on purpose; production reads BANKRAG_AUDIT_KEY


_PARAM = re.compile(r"(?<![\w:]):[A-Za-z_]\w*")


def needs_params(sql: str) -> bool:
    return bool(_PARAM.search("\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))))


def sql_files(root: Path) -> list[Path]:
    """Report queries: every sql/*.sql that takes no parameters."""
    return sorted(p for p in (root / "sql").glob("*.sql") if not needs_params(p.read_text()))


def title(path: Path) -> str:
    first = path.read_text().splitlines()[0]
    return first.lstrip("- ").strip() if first.startswith("--") else path.stem


def markdown_table(cols: list[str], rows: list[tuple], limit: int = 15) -> str:
    if not rows:
        return "_no rows_"
    def cell(v):
        return "" if v is None else str(v).replace("|", "\\|")
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows[:limit]]
    if len(rows) > limit:
        out.append(f"\n_{len(rows) - limit} more rows_")
    return "\n".join(out)


def report(audit: AuditLog, root: Path) -> str:
    parts = []
    for path in sql_files(root):
        cols, rows = audit.query(path.read_text())
        parts.append(f"### {title(path)}\n\n`{path.relative_to(root)}`\n\n{markdown_table(cols, rows)}")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------------------------- demo
class _Clock:
    def __init__(self):
        self.value = "2026-01-01T00:00:00.000+00:00"

    def __call__(self) -> str:
        return self.value


def _copy_db(audit: AuditLog, dst: Path) -> sqlite3.Connection:
    audit.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy(audit.path, dst)
    conn = sqlite3.connect(dst, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _drop_triggers(conn) -> None:
    conn.execute("DROP TRIGGER audit_events_no_update")
    conn.execute("DROP TRIGGER audit_events_no_delete")


def attacks(audit: AuditLog, workdir: Path) -> list[dict]:
    """Each attack runs on its own copy. 'expected' is what a sound design must report."""
    head = audit.head()
    target = audit.query("SELECT seq, payload FROM audit_events WHERE event_type = 'document_decision' "
                         "AND json_extract(payload, '$.decision') = 'reject' ORDER BY seq LIMIT 1")[1][0]
    seq, payload = target
    forged = json.dumps({**json.loads(payload), "decision": "auto_accept", "rules": [], "codes": []}, sort_keys=True,
                        separators=(",", ":"))
    out = []

    def run(name, what, fn, anchor=None, expect_detected=True):
        path = workdir / f"{re.sub(r'[^a-z]+', '_', name.lower())}.db"
        conn = _copy_db(audit, path)
        blocked = None
        try:
            fn(conn)
        except sqlite3.DatabaseError as e:
            blocked = str(e)
        conn.close()
        log = AuditLog(path, key=DEMO_KEY)
        res = log.verify(anchor=anchor)
        log.close()
        detected = blocked is not None or not res.ok
        out.append({"attack": name, "what": what, "blocked_by_trigger": blocked, "verify_ok": res.ok,
                    "first_bad_seq": res.first_bad_seq, "problem": res.problem, "detected": detected,
                    "expected_detected": expect_detected, "as_expected": detected == expect_detected,
                    "with_anchor": anchor is not None})

    run("UPDATE through SQL", f"change event {seq} from reject to auto_accept with a plain UPDATE",
        lambda c: c.execute("UPDATE audit_events SET payload = ? WHERE seq = ?", (forged, seq)))
    run("Edit after dropping triggers", f"same change to event {seq}, triggers dropped first",
        lambda c: (_drop_triggers(c), c.execute("UPDATE audit_events SET payload = ? WHERE seq = ?", (forged, seq))))
    run("Delete an event", f"remove event {seq} entirely",
        lambda c: (_drop_triggers(c), c.execute("DELETE FROM audit_events WHERE seq = ?", (seq,))))

    def rechain(c, key=b"attacker-guess"):
        import hashlib
        import hmac as _hmac
        _drop_triggers(c)
        c.execute("UPDATE audit_events SET payload = ? WHERE seq = ?", (forged, seq))
        prev = c.execute("SELECT hash FROM audit_events WHERE seq = ?", (seq - 1,)).fetchone()[0] if seq > 1 else "0" * 64
        for r in c.execute("SELECT * FROM audit_events WHERE seq >= ? ORDER BY seq", (seq,)).fetchall():
            body = json.dumps([r["seq"], r["ts"], r["event_type"], r["request_id"], r["subject_id"], r["payload"]],
                              sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            h = _hmac.new(key, (prev + body).encode(), hashlib.sha256).hexdigest()
            c.execute("UPDATE audit_events SET prev_hash = ?, hash = ? WHERE seq = ?", (prev, h, r["seq"]))
            prev = h

    run("Edit and recompute the chain", f"change event {seq}, then recompute every later hash without the key", rechain)
    tail = lambda c: (_drop_triggers(c), c.execute("DELETE FROM audit_events WHERE seq > ?", (head[0] - 10,)))
    run("Truncate the last 10 events", "delete the newest events; the shorter chain is still internally valid",
        tail, expect_detected=False)
    run("Truncate the last 10 events, anchored", "same, verified against the exported head (seq, hash)",
        tail, anchor=head)
    return out


def demo(cfg: dict) -> dict:
    """Deterministic: fixed key and clock; RAG events carry random trace ids, which the report does not print."""
    from .docverify.evaluate import corpus
    from .docverify.verify import DocumentVerifier
    from .eval.harness import load_golden, source_fingerprint
    from .pipeline import Pipeline

    root = Path(cfg["_root"])
    with tempfile.TemporaryDirectory() as tmp:
        clock = _Clock()
        audit = AuditLog(Path(tmp) / "audit.db", key=DEMO_KEY, clock=clock)
        verifier = DocumentVerifier(cfg, audit=audit)
        docs = corpus(cfg, "test")
        for i, s in enumerate(docs):
            d = date.fromisoformat(s.application["application_date"])
            clock.value = datetime(d.year, d.month, d.day, 9, 0, tzinfo=timezone.utc).isoformat(timespec="milliseconds")
            verifier.verify(s.pdf, s.application, request_id=f"demo-doc-{i:04d}")
        pipe = Pipeline(cfg, audit=audit)
        items = load_golden(resolve(cfg, "golden_set"), "test")
        start = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
        for i, item in enumerate(items):
            clock.value = (start + timedelta(minutes=i)).isoformat(timespec="milliseconds")
            pipe.ask(item["question"], request_id=f"demo-ask-{i:04d}")
        clean = audit.verify()
        raw = Path(audit.path).read_bytes() + (Path(audit.path + "-wal").read_bytes() if Path(audit.path + "-wal").exists() else b"")
        pii_in_db = [v for s in docs for v in (s.application["nric"], s.application["applicant_name"]) if v and v.encode() in raw]
        tables = report(audit, root)
        results = attacks(audit, Path(tmp))
        audit.close()
    passed = clean.ok and all(r["as_expected"] for r in results) and not pii_in_db
    return {"source_fingerprint": source_fingerprint(root), "n_documents": len(docs), "n_questions": len(items),
            "clean_verify": {"ok": clean.ok, "n_events": clean.n_events, "problem": clean.problem},
            "pii_values_found_in_db": len(pii_in_db), "attacks": results, "passed": passed, "report_tables": tables}


def demo_markdown(d: dict) -> str:
    L = ["# Audit trail: verification and tamper test", "",
         f"Generated by `python -m bankrag audit demo` · source fingerprint `{d['source_fingerprint']}`.", "",
         f"A fresh audit log was built from the {d['n_documents']} test-split documents and the {d['n_questions']} golden "
         "test questions, using a fixed, public demo key and clock so the run is repeatable. Then each attack below was "
         "made on a separate copy of the database file, and the copy was verified.", "",
         f"- Clean log: **{'verifies' if d['clean_verify']['ok'] else 'FAILS'}** ({d['clean_verify']['n_events']} events).",
         f"- Applicant names or NRICs found anywhere in the database file: **{d['pii_values_found_in_db']}**.", "",
         "## Attacks", "", "| attack | what was done | caught by | result |", "|---|---|---|---|"]
    for r in d["attacks"]:
        if r["blocked_by_trigger"]:
            how = f"trigger: `{r['blocked_by_trigger']}`"
        elif not r["verify_ok"]:
            how = f"`verify`: {r['problem']} (event {r['first_bad_seq']})"
        else:
            how = "not detected"
        verdict = ("✅ detected" if r["detected"] else "⚠️ not detected") + ("" if r["as_expected"] else " (UNEXPECTED)")
        L.append(f"| {r['attack']} | {r['what']} | {how} | {verdict} |")
    L += ["", "Truncation without an anchor is undetectable **by design of any hash chain**: what is left is a valid, "
          "shorter chain. That is why `bankrag audit head` exists: export the latest (seq, hash) on a schedule to "
          "storage the database's operators cannot rewrite (for example an object-lock bucket), and verify against it.",
          "", "Recomputing the chain after an edit fails because the hashes are keyed (HMAC). With an unkeyed SHA-256 "
          "chain, that attack would succeed: the key must live in a secret store, not next to the database.",
          "", "## Reporting queries (sql/)", "", "Plain SQL over the same database: the payloads are JSON, read with "
          "SQLite's `json_extract` / `json_each`. Identities are salted hashes.", "", d["report_tables"],
          "", f"**Overall: {'PASSED' if d['passed'] else 'FAILED'}**"]
    return "\n".join(L) + "\n"
