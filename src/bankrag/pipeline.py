"""input guardrail -> retrieve -> abstain if low confidence -> generate -> output guardrail -> trace"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import load_config, resolve
from .generators import make_generator
from .guardrails import check_input, check_output
from .ingest import load_corpus
from .prompts import Prompt
from .retrieval import RetrievalResult, Retriever
from .tracing import StageTimer, Tracer, query_hash

INJECTION_REFUSAL = ("I can only help with questions about Bank Seri Andalas products and policies, "
                     "and I can't change how I operate. Please ask your question again.")


@dataclass
class Response:
    answer: str
    abstained: bool
    blocked: bool
    reason: str               # ok | injection_blocked | low_confidence | generator_abstained | citation_missing | pii_leak_blocked
    confidence: float
    masked_query: str
    retrieved: list[str] = field(default_factory=list)   # chunk ids, best first
    citations: list[str] = field(default_factory=list)
    pii_types: list[str] = field(default_factory=list)
    injection_rules: list[str] = field(default_factory=list)
    output_pii_leak: bool = False
    trace: dict = field(default_factory=dict)

    @property
    def retrieved_docs(self) -> list[str]:
        return [cid.split("#", 1)[0] for cid in self.retrieved]

    @property
    def cited_docs(self) -> list[str]:
        return sorted({c.split("#", 1)[0] for c in self.citations})


class Pipeline:
    def __init__(self, cfg: dict | None = None, tracer: Tracer | None = None, generator=None):
        self.cfg = cfg or load_config()
        ch, rcfg = self.cfg["chunking"], self.cfg["retrieval"]
        self.chunks = load_corpus(resolve(self.cfg, "docs_dir"), ch["max_words"], ch["overlap_words"])
        self.retriever = Retriever(self.chunks, rcfg["mode"], rcfg["bm25_k1"], rcfg["bm25_b"], rcfg["rrf_k"])
        self.prompt = Prompt.load(resolve(self.cfg, self.cfg["generation"]["prompt"]))
        self.generator = generator or make_generator(self.cfg, self.prompt)
        self.tracer = tracer if tracer is not None else Tracer(None)
        self.threshold = float(self.cfg["abstain_threshold"])
        self.top_k = int(rcfg["top_k"])

    def retrieve(self, masked_query: str) -> RetrievalResult:
        return self.retriever.search(masked_query, self.top_k)

    def ask(self, question: str) -> Response:
        timer = StageTimer()
        with timer.stage("input_guardrail"):
            inp = check_input(question)
        resp = Response("", False, False, "ok", 0.0, inp.masked_text,
                        pii_types=sorted({s.type for s in inp.pii}), injection_rules=inp.injection_rules)
        gen = None

        if inp.blocked:
            resp.answer, resp.blocked, resp.reason = INJECTION_REFUSAL, True, "injection_blocked"
        else:
            with timer.stage("retrieval"):
                result = self.retrieve(inp.masked_text)
            resp.retrieved, resp.confidence = [h.chunk.id for h in result.hits], result.confidence
            if result.confidence < self.threshold:
                resp.answer, resp.abstained, resp.reason = self.prompt.abstain_text, True, "low_confidence"
            else:
                with timer.stage("generation"):
                    gen = self.generator.generate(inp.masked_text, [h.chunk for h in result.hits])
                with timer.stage("output_guardrail"):
                    out = check_output(gen.text, resp.retrieved, inp.pii)
                resp.citations = out.citations
                if out.leaked:
                    resp.answer, resp.abstained, resp.reason, resp.output_pii_leak = self.prompt.abstain_text, True, "pii_leak_blocked", True
                elif gen.abstained:
                    resp.answer, resp.abstained, resp.reason = gen.text, True, "generator_abstained"
                elif not out.citation_ok:
                    resp.answer, resp.abstained, resp.reason = self.prompt.abstain_text, True, "citation_missing"
                else:
                    resp.answer = gen.text

        resp.trace = self._trace(question, resp, timer, gen)
        self.tracer.write(resp.trace)
        return resp

    def _trace(self, question: str, resp: Response, timer: StageTimer, gen) -> dict:
        provider = self.generator.provider
        price = self.cfg["cost_per_1k_tokens"].get(provider, {"input": 0.0, "output": 0.0})
        p_tok = gen.prompt_tokens if gen else 0
        c_tok = gen.completion_tokens if gen else 0
        return {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "trace_id": uuid.uuid4().hex,
            "query_hash": query_hash(question),
            "masked_query": resp.masked_query,
            "query_len": len(resp.masked_query.split()),
            "pii_types": resp.pii_types,
            "injection_rules": resp.injection_rules,
            "retrieval_mode": self.retriever.mode,
            "retrieved": resp.retrieved,
            "top_doc": resp.retrieved_docs[0] if resp.retrieved else None,
            "top_score": round(resp.confidence, 4),
            "abstain_threshold": self.threshold,
            "abstained": resp.abstained,
            "blocked": resp.blocked,
            "reason": resp.reason,
            "citations": resp.citations,
            "output_pii_leak": resp.output_pii_leak,
            "provider": provider,
            "model": gen.model if gen else self.generator.model,
            "prompt_version": self.prompt.version,
            "prompt_sha": self.prompt.sha,
            "prompt_tokens": p_tok,
            "completion_tokens": c_tok,
            "tokens_estimated": gen.tokens_estimated if gen else False,
            "cost_usd": round((p_tok * price["input"] + c_tok * price["output"]) / 1000, 8),
            "latency_ms": {**timer.ms, "total": timer.total},
        }
