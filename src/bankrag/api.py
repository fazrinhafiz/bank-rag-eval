"""HTTP API for the assistant (FastAPI). Install with `pip install -e ".[api]"`.

    uvicorn bankrag.api:create_app --factory --host 0.0.0.0 --port 8000

Endpoints:
    GET  /health            liveness + the versions that produced the answers (for support and audit)
    POST /ask               answer one question through the guarded pipeline
    POST /documents/verify  check a payslip or bank statement against its loan application (needs the
                            `docs` extra); returns auto_accept | manual_review | reject with every reason

Every response carries an X-Request-ID header (taken from the request if the caller sent one). The same
id is in the access log line, the decision log line and the trace record, so one customer interaction
can be followed end to end. Request bodies are never logged and the question is never echoed back.
"""
from __future__ import annotations

import base64
import binascii
import logging
import os
import re
import time
import uuid
from datetime import date

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .audit import AuditLog
from .config import load_config, resolve
from .eval.harness import source_fingerprint
from .logging_setup import configure_logging, request_id_var
from .pipeline import Pipeline
from .tracing import DEV_SALT, Tracer

log = logging.getLogger("bankrag.api")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000, description="Customer question. PII is masked before processing.")


class AskResponse(BaseModel):
    request_id: str = Field(description="Your X-Request-ID, or the one generated for this call.")
    answer: str = Field(description="The answer with [doc_id#section] citations, or the abstention / refusal text to show instead.")
    outcome: str = Field(description="answered | abstained | blocked")
    reason: str = Field(description="ok | low_confidence | generator_abstained | citation_missing | pii_leak_blocked | injection_blocked")
    confidence: float = Field(description="Retrieval confidence (normalised score, not a probability). Below the calibrated threshold the service abstains.")
    citations: list[str] = Field(description="Policy sections the answer is drawn from, as doc_id#section.")
    prompt_version: str = Field(description="Version of the prompt that produced the answer.")
    model: str = Field(description="Generator that produced the answer (offline-extractive-v1 for the baseline).")


class Application(BaseModel):
    application_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$",
                                description="Your loan application id. Duplicate detection compares documents across applications.")
    applicant_name: str = Field(min_length=1, max_length=200, description="As on the application; compared with the name on the document.")
    nric: str | None = Field(default=None, max_length=20,
                             description="Optional MyKad number (with or without dashes). Without it, identity is checked by name only.")
    application_date: date = Field(description="Dates on the document are checked against this date (stale or future-dated documents).")


class VerifyRequest(BaseModel):
    application: Application
    document_base64: str = Field(min_length=1, description="The PDF, base64-encoded. Max size: docverify.max_upload_mb.")


class Reason(BaseModel):
    rule: str = Field(description="Rule id in rules/document_rules.yaml, e.g. R01.")
    decision: str = Field(description="What this rule decides: manual_review | reject.")
    reason: str = Field(description="Plain-language reason, suitable for a reviewer.")
    codes: list[str] = Field(description="The finding codes that triggered the rule.")


class FindingOut(BaseModel):
    code: str = Field(description="Stable finding code, e.g. arithmetic_mismatch.")
    source: str = Field(description="Check group that produced it: consistency | extraction | forensics | anomaly | registry | system.")
    severity: str = Field(description="high | medium | info. The decision comes from the rules, not from severity.")
    detail: str = Field(description="What was found. Never the full NRIC; may quote the invalid part, such as an impossible birth date.")


class VerifyResponse(BaseModel):
    request_id: str = Field(description="Your X-Request-ID, or the one generated for this call.")
    decision: str = Field(description="auto_accept | manual_review | reject")
    reasons: list[Reason] = Field(description="Every rule that matched; empty when auto-accepted.")
    findings: list[FindingOut] = Field(description="Everything the checks found, including findings no rule acts on.")
    doc_type: str | None = Field(description="payslip | bank_statement, or null if not recognised.")
    fields: dict = Field(description="Extracted fields; NRIC masked to the last 4 digits, transactions summarised.")
    missing_fields: list[str] = Field(description="Required fields that could not be extracted (the document then goes to review).")
    anomaly_score: float | None = Field(description="How unusual the figures are compared with genuine documents; null if not scored.")
    anomaly_threshold: float | None = Field(description="Scores above this raise anomaly_score_high (manual review).")
    sha256: str = Field(description="SHA-256 of the file as received. Also in the audit log.")
    rules_version: str = Field(description="Version of rules/document_rules.yaml that made the decision.")
    extraction_method: str = Field(description="How fields were read: text_layer, or llm when the optional LLM extractor is used.")


class ErrorDetail(BaseModel):
    detail: str


class InternalError(BaseModel):
    error: str = Field(description="Always internal_error. Details are in the service log under request_id.")
    request_id: str


_500 = {500: {"model": InternalError, "description": "Unhandled error, or the audit record could not be written. "
                                                     "No decision is returned without its audit record."}}


class Health(BaseModel):
    status: str = Field(description="ok when the service is up.")
    version: str = Field(description="Package version.")
    source_fingerprint: str = Field(description="Hash of the code, config, prompts, rules, documents and gates; matches the eval reports it was gated on.")
    retrieval_mode: str = Field(description="bm25 | tfidf | hybrid.")
    provider: str = Field(description="offline | openai | azure. OpenAI-compatible servers such as Ollama use openai.")
    prompt_version: str = Field(description="Prompt in use.")
    abstain_threshold: float = Field(description="Calibrated confidence below which /ask abstains.")
    audit_head_seq: int | None = Field(default=None, description="Events in the audit log (None if disabled).")


def create_app(cfg: dict | None = None, pipeline: Pipeline | None = None) -> FastAPI:
    configure_logging()
    cfg = cfg or load_config()
    audit = AuditLog(resolve(cfg, "audit_db")) if cfg.get("audit_db") else None
    if audit is not None and audit.keyed_with_dev_key:
        log.warning("audit log uses the development key", extra={"event": "config_warning", "setting": "BANKRAG_AUDIT_KEY"})
    if os.environ.get("BANKRAG_TRACE_SALT", DEV_SALT) == DEV_SALT:
        log.warning("trace and identity hashes use the development salt",
                    extra={"event": "config_warning", "setting": "BANKRAG_TRACE_SALT"})
    pipe = pipeline or Pipeline(cfg, tracer=Tracer(resolve(cfg, "trace_path")), audit=audit)
    fingerprint = source_fingerprint(cfg["_root"])
    verifier = _document_verifier(cfg, audit)
    max_bytes = int(cfg.get("docverify", {}).get("max_upload_mb", 5) * 1024 * 1024)
    app = FastAPI(title="bankrag", version=__version__,
                  description="Guarded policy Q&A for the fictional Bank Seri Andalas Berhad.")

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"event": "error", "path": request.url.path})
            response = JSONResponse({"error": "internal_error", "request_id": rid}, status_code=500)
        response.headers["X-Request-ID"] = rid
        log.info("request", extra={"event": "access", "method": request.method, "path": request.url.path,
                                   "status": response.status_code,
                                   "latency_ms": round((time.perf_counter() - start) * 1000, 2)})
        request_id_var.reset(token)
        return response

    @app.get("/health", response_model=Health, summary="Liveness and the versions behind every answer")
    def health() -> Health:
        return Health(status="ok", version=__version__, source_fingerprint=fingerprint, retrieval_mode=pipe.retriever.mode,
                      provider=pipe.generator.provider, prompt_version=pipe.prompt.version, abstain_threshold=pipe.threshold,
                      audit_head_seq=None if audit is None else audit.head()[0])

    @app.post("/ask", response_model=AskResponse, summary="Answer one policy question", responses=_500)
    def ask(body: AskRequest) -> AskResponse:
        """Runs the guarded pipeline: mask PII, block injections, retrieve, abstain below the calibrated
        threshold, generate with citations, check the output. The question is never echoed back or logged."""
        rid = request_id_var.get()
        resp = pipe.ask(body.question, request_id=rid)
        outcome = "blocked" if resp.blocked else "abstained" if resp.abstained else "answered"
        return AskResponse(request_id=rid, answer=resp.answer, outcome=outcome, reason=resp.reason,
                           confidence=round(resp.confidence, 4), citations=resp.citations,
                           prompt_version=pipe.prompt.version, model=resp.trace["model"])

    @app.post("/documents/verify", response_model=VerifyResponse, summary="Check a payslip or bank statement",
              responses={413: {"model": ErrorDetail, "description": "Document larger than docverify.max_upload_mb."},
                         503: {"model": ErrorDetail, "description": "Document verification is not installed in this image."},
                         **_500})
    def verify_document(body: VerifyRequest) -> VerifyResponse:
        """Extracts the fields, checks them against each other, the application, earlier submissions and the
        file's own history, then applies the rule policy. Unreadable or unsupported documents come back as
        manual_review (fail closed), not as an error. Re-sending the same document for the same application
        is safe: it does not count as a duplicate."""
        if verifier is None:
            raise HTTPException(503, "document verification is not installed (pip install '.[docs]')")
        if len(body.document_base64) > max_bytes * 4 // 3 + 4:
            raise HTTPException(413, f"document larger than {max_bytes // (1024 * 1024)} MB")
        try:
            pdf = base64.b64decode(body.document_base64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(422, "document_base64 is not valid base64")
        app_data = body.application.model_dump()
        app_data["application_date"] = body.application.application_date.isoformat()
        verdict = verifier.verify(pdf, app_data, request_id=request_id_var.get())
        body_out = {k: v for k, v in verdict.to_json().items() if k != "checks_run"}
        return VerifyResponse(request_id=request_id_var.get(), **body_out)

    return app


def _document_verifier(cfg: dict, audit=None):
    """None when the optional `docs` extra (pypdf) or its config is missing; /documents/verify then answers 503."""
    if "docverify" not in cfg:
        return None
    try:
        import pypdf  # noqa: F401
    except ImportError:
        return None
    from .docverify.verify import DocumentVerifier
    return DocumentVerifier(cfg, audit=audit)
