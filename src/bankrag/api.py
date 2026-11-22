"""HTTP API for the assistant (FastAPI). Install with `pip install -e ".[api]"`.

    uvicorn bankrag.api:create_app --factory --host 0.0.0.0 --port 8000

Endpoints:
    GET  /health  liveness + the versions that produced the answers (for support and audit)
    POST /ask     answer one question through the guarded pipeline

Every response carries an X-Request-ID header (taken from the request if the caller sent one). The same
id is in the access log line, the decision log line and the trace record, so one customer interaction
can be followed end to end. Request bodies are never logged and the question is never echoed back.
"""
from __future__ import annotations

import logging
import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import load_config, resolve
from .eval.harness import source_fingerprint
from .logging_setup import configure_logging, request_id_var
from .pipeline import Pipeline
from .tracing import Tracer

log = logging.getLogger("bankrag.api")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000, description="Customer question. PII is masked before processing.")


class AskResponse(BaseModel):
    request_id: str
    answer: str
    outcome: str = Field(description="answered | abstained | blocked")
    reason: str = Field(description="ok | low_confidence | generator_abstained | citation_missing | pii_leak_blocked | injection_blocked")
    confidence: float
    citations: list[str]
    prompt_version: str
    model: str


class Health(BaseModel):
    status: str
    version: str
    source_fingerprint: str
    retrieval_mode: str
    provider: str
    prompt_version: str
    abstain_threshold: float


def create_app(cfg: dict | None = None, pipeline: Pipeline | None = None) -> FastAPI:
    configure_logging()
    cfg = cfg or load_config()
    pipe = pipeline or Pipeline(cfg, tracer=Tracer(resolve(cfg, "trace_path")))
    fingerprint = source_fingerprint(cfg["_root"])
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

    @app.get("/health", response_model=Health)
    def health() -> Health:
        return Health(status="ok", version=__version__, source_fingerprint=fingerprint, retrieval_mode=pipe.retriever.mode,
                      provider=pipe.generator.provider, prompt_version=pipe.prompt.version, abstain_threshold=pipe.threshold)

    @app.post("/ask", response_model=AskResponse)
    def ask(body: AskRequest) -> AskResponse:
        rid = request_id_var.get()
        resp = pipe.ask(body.question, request_id=rid)
        outcome = "blocked" if resp.blocked else "abstained" if resp.abstained else "answered"
        return AskResponse(request_id=rid, answer=resp.answer, outcome=outcome, reason=resp.reason,
                           confidence=round(resp.confidence, 4), citations=resp.citations,
                           prompt_version=pipe.prompt.version, model=resp.trace["model"])

    return app
