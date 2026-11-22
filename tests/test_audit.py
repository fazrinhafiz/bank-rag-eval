"""Audit trail: hash chain, append-only triggers, keyed verification, anchors, persistence, privacy, SQL."""
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from bankrag.audit import AuditLog
from bankrag.audit_report import needs_params, sql_files
from bankrag.config import load_config, with_overrides
from bankrag.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[1]
KEY = "test-key"


@pytest.fixture()
def log(tmp_path):
    a = AuditLog(tmp_path / "a.db", key=KEY)
    for i in range(1, 6):
        a.append("document_decision", {"decision": "reject" if i == 3 else "auto_accept", "n": i},
                 request_id=f"r{i}", subject_id=f"APP-{i}")
    return a


def _raw(path) -> sqlite3.Connection:
    c = sqlite3.connect(path, isolation_level=None)
    c.execute("DROP TRIGGER audit_events_no_update")
    c.execute("DROP TRIGGER audit_events_no_delete")
    return c


def test_chain_verifies_and_links(log):
    res = log.verify()
    assert res.ok and res.n_events == 5 and log.head() == (5, res.head_hash)
    _, rows = log.query("SELECT seq, prev_hash, hash FROM audit_events ORDER BY seq")
    assert rows[0][1] == "0" * 64 and all(rows[i][1] == rows[i - 1][2] for i in range(1, 5))


def test_table_is_append_only(log):
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        log.conn.execute("UPDATE audit_events SET payload = '{}' WHERE seq = 3")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        log.conn.execute("DELETE FROM audit_events WHERE seq = 3")


def test_edit_and_delete_are_detected_at_the_right_event(log):
    log.close()
    c = _raw(log.path)
    c.execute("""UPDATE audit_events SET payload = '{"decision":"auto_accept","n":3}' WHERE seq = 3""")
    res = AuditLog(log.path, key=KEY).verify()
    assert not res.ok and res.first_bad_seq == 3 and "hash mismatch" in res.problem
    # Opening the log re-creates any dropped trigger, so the attacker has to drop them again.
    _raw(log.path).execute("DELETE FROM audit_events WHERE seq = 3")
    res = AuditLog(log.path, key=KEY).verify()
    assert not res.ok and res.first_bad_seq == 3 and "sequence gap" in res.problem


def test_the_key_is_what_stops_a_rewritten_chain(tmp_path):
    """Same chain, rewritten by someone who knows the algorithm: only the right key verifies it."""
    a = AuditLog(tmp_path / "a.db", key=KEY)
    a.append("x", {"v": 1})
    assert AuditLog(tmp_path / "a.db", key="wrong").verify().ok is False
    assert AuditLog(tmp_path / "a.db", key=KEY).verify().ok


def test_truncation_needs_an_anchor(log):
    anchor = log.head()
    log.close()
    _raw(log.path).execute("DELETE FROM audit_events WHERE seq > 3")
    reopened = AuditLog(log.path, key=KEY)
    assert reopened.verify().ok                       # a shorter chain is still a valid chain
    res = reopened.verify(anchor=anchor)
    assert not res.ok and "events removed" in res.problem


def test_concurrent_appends_keep_one_valid_chain(tmp_path):
    a = AuditLog(tmp_path / "a.db", key=KEY)
    threads = [threading.Thread(target=lambda t=t: [a.append("x", {"t": t, "i": i}) for i in range(50)]) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    res = a.verify()
    assert res.ok and res.n_events == 200


def test_rag_events_hold_no_question_text(tmp_path):
    cfg = load_config()
    a = AuditLog(tmp_path / "a.db", key=KEY)
    question = "My IC is 900101-14-5678, what is the late payment charge on my credit card?"
    Pipeline(cfg, audit=a).ask(question, request_id="req-7")
    a.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    raw = Path(a.path).read_bytes()
    assert b"900101" not in raw and b"late payment" not in raw
    _, rows = a.query("SELECT request_id, payload FROM audit_events")
    payload = json.loads(rows[0][1])
    assert rows[0][0] == "req-7" and payload["outcome"] == "answered" and payload["pii_types"] == ["nric"]


def test_document_events_and_registry_survive_a_restart(tmp_path):
    pytest.importorskip("pypdf")
    pytest.importorskip("fpdf")
    from bankrag.docverify.synth import build_corpus
    from bankrag.docverify.verify import DocumentVerifier

    cfg = load_config()
    subs = build_corpus("test", 23, n_genuine=3, n_per_kind=1)
    original = next(s for s in subs if s.label == "genuine" and s.doc_type == "payslip")
    path = tmp_path / "a.db"
    a = AuditLog(path, key=KEY)
    assert DocumentVerifier(cfg, audit=a).verify(original.pdf, original.application).decision == "auto_accept"
    a.close()
    # Service restarts; someone else submits the same file.
    b = AuditLog(path, key=KEY)
    thief = {"application_id": "APP-X", "applicant_name": original.application["applicant_name"],
             "nric": "910202-10-1234", "application_date": original.application["application_date"]}
    v = DocumentVerifier(cfg, audit=b).verify(original.pdf, thief)
    assert "duplicate_other_applicant" in v.codes and v.decision == "reject"
    b.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    raw = path.read_bytes()
    assert original.application["nric"].encode() not in raw and b"910202" not in raw
    assert original.application["applicant_name"].encode() not in raw
    assert b.verify().ok and b.head()[0] == 2


def test_every_report_query_runs(log):
    files = sql_files(ROOT)
    assert len(files) >= 6 and all(not needs_params(p.read_text()) for p in files)
    for p in files:
        log.query(p.read_text())
    cols, rows = log.query((ROOT / "sql/lookup_request.sql").read_text(), {"id": "APP-3"})
    assert needs_params((ROOT / "sql/lookup_request.sql").read_text()) and len(rows) == 1 and rows[0][4] == "APP-3"


def test_api_fails_closed_when_the_audit_write_fails(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from bankrag import audit as audit_mod
    from bankrag.api import create_app

    cfg = with_overrides(load_config(), audit_db=str(tmp_path / "a.db"))
    client = TestClient(create_app(cfg), raise_server_exceptions=False)
    before = client.get("/health").json()["audit_head_seq"]
    assert client.post("/ask", json={"question": "What is the annual fee for the credit card?"}).status_code == 200
    assert client.get("/health").json()["audit_head_seq"] == before + 1

    def broken(self, *a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(audit_mod.AuditLog, "append", broken)
    r = client.post("/ask", json={"question": "What is the annual fee for the credit card?"})
    assert r.status_code == 500 and "answer" not in r.json()


def test_audit_demo_catches_every_attack_it_should():
    from bankrag.audit_report import demo
    d = demo(load_config())
    assert d["passed"] and d["pii_values_found_in_db"] == 0
    assert [r["detected"] for r in d["attacks"]] == [True, True, True, True, False, True]
