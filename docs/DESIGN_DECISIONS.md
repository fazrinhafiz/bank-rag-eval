# Design decisions

Short architecture decision records. Each one states the context, the decision, and what it costs.

---

## ADR-001: Hybrid retrieval: implemented, benchmarked, not the default

**Context.** Hybrid retrieval (lexical BM25 fused with a semantic retriever via Reciprocal Rank Fusion)
is the common default for RAG, because the two retrievers fail differently: BM25 nails exact terms
("RM250", "Kill Switch"), dense retrievers handle paraphrase. Offline, with no embedding model, the
semantic stand-in is TF-IDF over character 3–5-grams, which gives fuzzy sub-word matching
("withdrawing" ≈ "withdrawal").

**Decision.** Implement all three (`bm25`, `tfidf`, `hybrid`) behind one interface and let the evidence
pick the default. `bankrag benchmark` calibrates each mode's threshold on dev, then compares. The
selection rule uses **dev metrics only**: best dev abstention F1, then dev key-fact recall, then the
simpler mode. Result: **`tfidf` was selected**. The hybrid matches it on ranking (same hit@1 and MRR)
but its confidence score (an average of two differently scaled scores) separates answerable from
unanswerable questions less well.

**Consequences.**
- Simpler default, with one fewer component to monitor and explain.
- Hybrid is one config line away and is re-tested by `benchmark` whenever the corpus grows or real embeddings arrive. RRF should matter more on a corpus of thousands of chunks than on 50.
- The dev split made this decision and the threshold decision, so dev metrics are optimistic. Test stays clean.

## ADR-002: An offline extractive baseline generator, not an LLM, by default

**Context.** The deliverable is an *evaluation harness*. It has to run in CI on every push, for free,
with identical results each time, and without sending anything to a third party. It also needs a floor
that any LLM must beat to justify its cost and risk.

**Decision.** `OfflineExtractive` picks the two retrieved sentences with the best query overlap and cites
them. It's clearly labelled as a baseline, not an LLM. `OpenAICompatible` (OpenAI, Azure OpenAI, Ollama,
vLLM) plugs in via env vars at temperature 0 with the same prompt, guardrails and evaluation.

**Consequences.**
- Reproducible numbers (only latency varies), zero cost, no secrets in CI.
- The baseline can't synthesise or paraphrase. It fails ans-05/17/34 on sentence selection, and its faithfulness is trivially 100%.
- The harness is evaluated against a system it can measure deterministically. LLM-specific concerns (judge reliability, paraphrase-robust scoring) are handled in code (`eval --judge`) but **not yet exercised against a live model**.

## ADR-003: Calibrate the abstain threshold on dev; gate on test

**Context.** The abstain threshold is the main knob trading wrong answers against unnecessary refusals.
If it's tuned on the same items used to report quality, the reported quality is inflated.

**Decision.** `calibrate` sweeps candidate thresholds (midpoints between consecutive dev confidences),
maximises abstention F1 on **dev only**, and breaks ties by taking the middle of the tied range. It
writes the value to `config.yaml`. `calibrate()` raises an error if given any non-dev item. `eval` gates
on the test split only.

**Consequences.**
- The test score is an honest estimate for this threshold.
- Dev is small: 21 non-injection items, 4 of which should abstain. The chosen threshold has high variance, which shows in the dev/test gap (dev abstention recall 75%, test 50%). More unanswerable dev items is the cheapest improvement available.
- F1 weights false answers and false refusals symmetrically. A bank may prefer a cost-weighted objective, since a wrong fee quote probably costs more than a refusal. That's a business decision to record, not a default to hide.

## ADR-004: Privacy-safe tracing (salted hash + masked query, never raw text)

**Context.** Traces are needed for debugging, cost tracking, drift monitoring and audit. Customer
queries contain personal data. Logs are copied, retained and widely read, so they're a classic leak path.

**Decision.** PII is masked *before* retrieval and generation, so it never reaches the model or the
vector index. Each trace stores:
- an **HMAC-SHA256 hash** of the raw query, salted via `BANKRAG_TRACE_SALT`. This lets repeats be correlated, while short identifiers like NRICs can't be brute-forced from an unsalted hash.
- the **masked** query
- the PII *types* found, never values
- per-stage latency, tokens, cost, prompt version and sha, model, top score and the abstain decision

Tests assert that raw PII values never appear in traces. The PII leak gate is absolute (0).

**Consequences.**
- Debugging can't see the exact original text. That trade-off is intended.
- Pattern-based masking misses names, addresses and account numbers (L5 in the model card), so masked logs still need access control and retention limits.
- The default salt is for development only and must come from a secret store in any real deployment.

## ADR-005: No agent

**Context.** Agent frameworks (LLM planning plus tool calls) are popular, and stakeholders may ask for
one.

**Decision.** No agent and no tools. The pipeline is a fixed, linear sequence with one decision point
(abstain).

**Why.**
1. The need is informational. Every useful action (dispute, waiver, application) has a regulated human or system path already.
2. Tools that act on accounts move the use case to a higher risk tier, and turn a prompt injection from "says something wrong" into "does something wrong".
3. A fixed pipeline is testable: every stage has a unit test, every decision has a reason code, and the same input always takes the same path. Multi-step agent trajectories are much harder to evaluate, gate and explain to a validator.
4. Lower cost and latency: one retrieval and at most one LLM call.

**Revisit when** a validated need for account-specific actions appears. That would need a new use-case
assessment at a higher risk tier, not a feature flag.
