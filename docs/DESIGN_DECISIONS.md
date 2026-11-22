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

## ADR-006: Public datasets as independent test sets, never for tuning

**Context.** The golden set and the guardrails were written by the same person, so the golden-set safety
numbers flatter the system. Real customer data can't be used in a public portfolio, and no public dataset
pairs bank policy documents with questions and correct answers.

**Decision.** Use two public datasets for what they can honestly measure, and only as test sets:
- **Banking77** (3,080 real customer queries): out-of-scope refusal, in-scope answer rate and retrieval, false blocks, and real traffic for the drift simulation. Each of its 77 intents is labelled covered / ambiguous / not covered, and the label file was committed before any query was run.
- **deepset/prompt-injections** (662 prompts): injection detection and false-positive rate.

Results are reported but not gated. Nothing (rules, threshold, retrieval mode) is tuned on them.

**Consequences.**
- They exposed three things the golden set hid: the injection rules catch about one in seven independent English attacks; the threshold is too strict for real phrasing; and the first drift monitor missed a surge on real traffic. The fix for the last one, a query-terms signal, was made after seeing the miss, and the report says so.
- To improve the injection detector or the threshold without fooling ourselves, the next step needs its own split: develop on deepset *train* or a labelled Banking77 dev sample, and report on held-out data.
- The intent labels are per intent and the data comes from a UK fintech, so these numbers are approximate.

## ADR-007: Anomaly score: a robust z-score, chosen on dev, not an Isolation Forest

**Context.** Most tampering makes a document contradict itself, and rules catch that. A *consistent
forgery* doesn't: the salary is inflated and every total recomputed. The only signal left is that the
figures are unusual. The first design was an Isolation Forest on ratio features. On its single test run it
flagged **0%** of consistent forgeries on both dev and test.

**Decision.** Redesign on the **dev split only**, then compare methods on dev, per document type:
- **Features:** payslip `log_gross`, `pcb_vs_schedule`, `net_ratio`, `allowance_ratio`; statement balances, flows and transaction shape.
- **Methods:** a robust z-score (median and MAD per feature, max |z| as the score) against an Isolation Forest.
- **Threshold:** the 97% quantile of out-of-fold scores on genuine dev documents, so about 3% of genuine documents are flagged by design.
- **Selection:** by recall on dev amount-changing tampers at that threshold (ties: AUROC).

The robust z-score won for both types:

| type | robust z: dev recall / AUROC | Isolation Forest: dev recall / AUROC |
|---|---|---|
| payslip | 100.0% / 1.000 | 3.3% / 0.851 |
| bank statement | 6.7% / 0.624 | 0.0% / 0.584 |

The model is stored as JSON (method, genuine training matrix, threshold), not a pickle, so it can be reviewed and
loaded without running code.

**Consequences.**
- The z-score explains itself: the reviewer sees which feature was furthest out.
- The payslip result depends on `pcb_vs_schedule`, a feature designed knowing the simulated forger leaves tax unchanged. A forger who recomputes PCB defeats it. The report states this as the upper bound of the design.
- Statements are barely helped (55% consistent-forgery recall on test overall: payslips 10/10, statements 1/10). A one-document model can't see a consistent statement forgery. The fix is cross-document reconciliation (salary credit against payslip net pay), which is next step 7 in the README.
- The redesign followed a test-split run. That is disclosed in the report, and the gates were written before the final test run.

## ADR-008: Document rule policy: reject only on hard evidence, review on suspicion, fail closed

**Context.** An automatic reject is a harmful error: a genuine applicant is turned away without a person
looking. An automatic accept of a forgery is the other harmful error. Manual review costs reviewer time but
harms no one. Different checks produce different strengths of evidence.

**Decision.** Decisions live in [rules/document_rules.yaml](../rules/document_rules.yaml), not in code:
- **Reject** (R01-R06) only when the document contradicts itself, the applicant, or an earlier submission by someone else: arithmetic, impossible NRIC, NRIC or name mismatch, dates contradicting the period, duplicate from another applicant.
- **Manual review** (R10-R15) for suspicion: editing traces, inconsistent fonts, a high anomaly score, statutory mismatches, a partial name match, a stale document.
- **Fail closed** (R20): an unreadable or unsupported PDF, missing fields or a check that crashed go to review. They never reach `auto_accept` by default.
- The most severe matching rule decides, and **every** matching rule is returned as a reason.
- Loading fails if a rule names an unknown finding code, and a test fails if a non-informational code has no rule.
- The file is part of `source_fingerprint`, so every report shows which rules produced it.

**Consequences.**
- On the synthetic test split: 0.0% of genuine documents rejected, 96.5% auto-accepted. 60.0% of tampered documents auto-rejected, and 94.0% not auto-accepted.
- The forensic checks are easy to evade once known, which is why they only ever trigger review.
- A risk or operations owner can tighten or relax the policy by editing YAML, and the document gate in CI shows the effect before release. A policy change is a model change (model card, change management).
- Stale documents go to review, not reject, because the remedy is to ask for a newer one.

## ADR-009: Audit trail: an HMAC hash chain in SQLite, written before the response

**Context.** Logs show what happened operationally, but they're mutable, sampled and full of noise. A bank
must be able to answer *what did the system decide for this customer, why, and under which rules, prompt
and model version*, and show the record wasn't altered afterwards.

**Options considered.**
1. **JSON lines in the log pipeline:** easy to query, no integrity guarantee.
2. **A managed ledger** (for example Azure Confidential Ledger, or immutable storage alone): strong guarantees, but a cloud dependency, which conflicts with the offline, reproducible build.
3. **Postgres with the same chain:** the production answer once there is more than one replica.
4. **SQLite with an HMAC chain** *(chosen)*: no infrastructure, plain SQL for reporting, and the integrity design ports to Postgres unchanged.

**Decision.**
- One table, `audit_events`, append-only through triggers that are re-created whenever the log is opened.
- Each event's hash is `HMAC(key, previous hash + canonical event)`, keyed with `BANKRAG_AUDIT_KEY`. An attacker with file access can drop the triggers and edit, but can't recompute a valid chain without the key.
- `audit head` exports the latest `seq:hash` to write-once storage, because truncating the end leaves a valid shorter chain.
- The event is written **before** the response. If the write fails, the caller gets a 500, so no decision exists without its record.
- Payloads hold ids, outcomes, reason codes, versions and hashes, never personal data. The duplicate registry shares the database and uses salted identity hashes.

**Consequences.**
- `audit demo` (in CI) shows each attack is caught except unanchored truncation, which is expected.
- It needs a single writer per file (ADR-010). Registry check-and-add is atomic within one process only.
- Key rotation isn't implemented: it needs a signed "key rotated" event, after which verification switches keys.
- The human reviewer's decision isn't recorded yet (README next step 6). Until it is, the log shows what the machine decided, not the final outcome.

## ADR-010: Deploy to Azure Container Apps; validate in CI, don't apply

**Context.** The service ships as a container image that CI already smoke-tests. There is no cloud
subscription for this project, but the deployment design should still be concrete, reviewable and checked.

**Decision.**
- **Platform:** Azure Container Apps rather than AKS, which needs cluster operations for one service, or App Service. It runs the same image, gives managed identity, Key Vault secret references and revision-based rollout, and it matches the Azure OpenAI path the generator already supports.
- **Shape:** one replica (the SQLite audit log), Azure Files for `/app/audit` with `BANKRAG_AUDIT_JOURNAL_MODE=DELETE` (SQLite WAL needs shared memory on one host), internal ingress by default, and immutable image tags.
- **Checks in CI:** `terraform fmt -check` and `terraform validate`, plus `tests/test_infra.py`, which pins the design rules and fails when any of them is broken. Nothing is planned or applied.

**Consequences.**
- Validation proves the configuration is well-formed, not that it deploys. Quota, region availability and role-propagation delays are untested.
- During a revision rollover two replicas can briefly share the SQLite file over SMB. The chain fails closed (duplicate `seq` is refused), but the file isn't protected from corruption. That, and scaling out, are why the next infrastructure step is Postgres.
- Writing the Terraform found a real bug: the audit log had WAL hard-coded, which would have been unsafe on the file share.
