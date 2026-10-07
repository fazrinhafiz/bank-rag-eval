# Model card: bankrag policy Q&A assistant

*Structured around the model risk management lifecycle: **development → independent validation →
approval → monitoring → periodic review**. Bank Seri Andalas Berhad is fictional. Regulatory references
are general by design: a real deployment would map each section to the bank's own model risk policy,
data-protection obligations (e.g. Malaysia's Personal Data Protection Act) and applicable supervisory
expectations on AI governance and technology risk.*

| | |
|---|---|
| **System** | Retrieval-augmented question answering over published retail policies, with input/output guardrails |
| **Version** | prompt `answer_v1`, retrieval `tfidf`, generator `offline-extractive-v1` (baseline), threshold set by `calibrate` |
| **Type** | Informational, customer-facing (proposed pilot: agent-assist). No transactions, no credit decisions |
| **Proposed risk tier** | Medium-High (see USE_CASE_ASSESSMENT.md §4) |
| **Owners (roles)** | Business owner: Head of Customer Experience · Model owner: AI Engineering · Content owners: the document owners named in each policy · Independent validator: Model Risk Management |

---

## 1. Development

### Intended use
- Answer customer and agent questions about the bank's **published** retail policies: cards, personal financing, eSavings, fees, fraud protection, complaints, privacy.
- Every answer cites the governing section (`[doc_id#section]`). If the documents don't answer the question, the assistant says so and refers the customer to the contact centre.

### Out-of-scope use
- Account-specific questions (balances, transactions, application status). There's no customer data access by design.
- Advice or recommendations (which product to choose, investment or debt advice).
- Any commitment or action: approvals, waivers, refunds, rate changes, disputes filed on the customer's behalf.
- Products not in the knowledge base (home loans, fixed deposits, FX, SME lending, takaful).
- Languages other than English. Use as a source of truth that overrides the documents themselves.

### Data
- **Knowledge base:** 7 markdown policy documents, chunked one chunk per `##` section (50 chunks). Each chunk carries `doc_id` and section for citation.
- **Evaluation data:** `eval/golden_set.jsonl`, 57 hand-written items (35 answerable, 10 unanswerable, 6 prompt-injection, 6 containing synthetic PII), split **dev 23 / test 34**. Key facts are verbatim from the documents and checked by tests.
- **No training.** No model weights are trained. The only fitted parameters are the abstain threshold and the choice of retrieval mode, both fitted on **dev only**.
- **Personal data:** customer queries may contain PII. It's masked before retrieval and generation, and only a salted hash plus the masked text are logged.

### Design summary
input guardrail (PII masking, injection rules) → retrieval (top-3) → abstain if confidence < threshold
→ generation (temperature 0, versioned prompt) → output guardrail (PII leak, valid citation) → trace.
Rationale for each choice is in DESIGN_DECISIONS.md.

### Evaluation results (development testing)

<!-- generated:results:start -->
Offline baseline: retrieval `tfidf`, generator `offline-extractive-v1`, prompt `answer_v1`, abstain threshold `0.2813` (calibrated on dev).

| metric | **test** (n=34, gated) | dev (n=23, used for calibration) | gate |
|---|---|---|---|
| Retrieval hit@1 | **87.5%** | 100.0% | - |
| Retrieval hit@3 | **100.0%** | 100.0% | ≥ 95.0% |
| Retrieval MRR@3 | **0.938** | 1.000 | ≥ 0.850 |
| Key-fact recall | **87.5%** | 82.4% | ≥ 80.0% |
| Citation accuracy | **95.8%** | 100.0% | ≥ 90.0% |
| Faithfulness (lexical) | **100.0%** | 100.0% | ≥ 90.0% |
| Abstention precision | **100.0%** | 75.0% | ≥ 75.0% |
| Abstention recall (unanswerable) | **50.0%** | 75.0% | ≥ 50.0% |
| Confidence AUROC | **0.889** | 0.897 | - |
| Injection block rate | **100.0%** | 100.0% | ≥ 100.0% |
| False block rate | **0.0%** | 0.0% | ≤ 5.0% |
| PII masked | **100.0%** | 100.0% | ≥ 100.0% |
| PII leak rate | **0.0%** | 0.0% | ≤ 0.0% |
| Task success (all items) | **82.4%** | 82.6% | ≥ 75.0% |

Test split: 28/34 items fully correct (answerable 17/20, unanswerable 3/6, injection 4/4, pii 4/4). Failed: ans-05, ans-17, ans-34, una-03, una-08, una-10. Every failure is explained in [reports/eval_report.md](reports/eval_report.md).
<!-- generated:results:end -->

**Leakage control:** the threshold (`calibrate`) and the retrieval mode (`benchmark`) are chosen using
dev metrics only. `calibrate()` raises an error if given a non-dev item. The test split is used only to
make the gate decision. Dev metrics are therefore optimistic. Test metrics are the ones to quote.

**Statistical caveat:** with 34 test items, one item is ≈3 percentage points on whole-split rates and
≈17 points on the 6-item categories. The results show the system works and catch regressions. They are
not precise performance estimates.

### Limitations and known failure modes
| # | limitation | evidence | consequence | mitigation / next step |
|---|---|---|---|---|
| L1 | Near-miss unanswerable questions are answered | una-03 (home loan), una-08 (Gold card), una-10 (SME loan) | Confident, cited answer about the **wrong product** | answerability verifier or product-entity check; monitoring of abstention / topic mix; human hand-off |
| L2 | Extractive baseline selects the wrong sentences | ans-05, ans-17, ans-34 | Incomplete answers | LLM generator, measured against this baseline |
| L3 | Faithfulness ≠ correctness | una-08 is 100% faithful and wrong | Faithfulness alone would pass bad answers | gate combines key facts, citation, abstention |
| L4 | Guardrails written with sight of the golden set | phone regex fixed after pii-06 missed during development | 100% injection / PII figures are optimistic | independent red-team set before validation sign-off |
| L5 | Pattern-based PII only | names (pii-06 "Ahmad"), addresses, account numbers not masked | residual personal data in masked logs | NER-based detector; log retention limits |
| L6 | Lexical key-fact matching | "five percent" ≠ "5%" | penalises paraphrasing LLMs | LLM judge validated against human labels |
| L7 | English only | none of the golden set is Malay or code-switched | unknown behaviour on BM queries | add BM items and evaluate |
| L8 | Small evaluation set | n=34 test | wide uncertainty | grow to ≥ 200 items with independent authors |
| L9 | Gates set after seeing baseline test results | thresholds.yaml header | gates are regression floors, not independent targets | validator sets targets for the production LLM independently |
| L10 | Document ambiguity not detected | DuitNow "unlimited free" vs RM5,000 free limit | possible partial answer | document owner review; conflict check at ingest |
| L11 | Confidence is not a probability | normalised score, threshold calibrated | scores are not comparable across modes or corpus changes | re-calibrate on every corpus change; consider Platt / isotonic scaling |

---

## 2. Independent validation

What an independent validator (not the developer) should do before approval:

1. **Reproduce:** fresh clone → `pip install -e .[dev]` → `pytest` → `python -m bankrag eval`. Every number in this card is regenerated from `reports/*.json`, and `tests/test_docs_in_sync.py` fails if docs and reports disagree.
2. **Challenge the data:** write a *new* adversarial set without access to the guardrail rules: unanswerable near-misses, injections (incl. indirect, obfuscated, Bahasa Malaysia), PII formats. Report results **before** any rule change (addresses L4).
3. **Challenge the metrics:** hand-review a sample of answers for correctness and tone. If the LLM judge is used, measure judge–human agreement before relying on it.
4. **Challenge the gate:** confirm `tests/test_gate_catches_regressions.py` mutants fail. Add mutants for any new control.
5. **Conceptual soundness:** check no-agent scope, abstention design, privacy design of traces, and dev/test separation.
6. **Outcome:** validation report with findings rated by severity. High findings block approval.

## 3. Approval

| condition | status (offline baseline) |
|---|---|
| Test-split quality gate passes | ✅ 12/12 gates (offline baseline) |
| PII leak rate = 0 in eval and in 1,500 simulated traces | ✅ |
| Independent red-team set evaluated | ❌ not yet (L4) |
| Abstention recall target agreed with the business and met | ❌ baseline at the 50% floor (L1) |
| Production LLM evaluated with this harness | ❌ not yet. No real-LLM run has been made |
| Human hand-off and monitoring operational | ❌ pilot prerequisite |

**Recommendation: not approved for customer-facing use.** It's suitable as a development baseline and
evaluation harness. Agent-assist pilot approval is conditional on the open items above.

Approval record (to be completed): approver, date, version approved (git sha + config fingerprint +
prompt sha from the eval report metadata), conditions, next review date.

## 4. Monitoring

| signal | method | threshold / action |
|---|---|---|
| Retrieval confidence distribution | PSI (10 baseline-quantile bins) + KS test, baseline window vs current window | PSI ≥ 0.10 investigate · > 0.25 act |
| Query length | PSI + KS | same bands |
| Topic mix (answered top document, abstained, blocked) | categorical PSI | same bands |
| Abstention / block / PII-bearing rates | absolute change | ≥ 10 pp → alert |
| Output PII leaks | output guardrail flag in traces | any → incident |
| Latency, tokens, cost | per-stage trace fields | operational SLOs |
| Answer quality | weekly sampled human review of answers + complaints tagged "assistant" | trend review |

`python -m bankrag monitor --fail-on-alert` exits 2 on ALERT, for a scheduled job. The simulated drift
scenario is evidence that the monitor works as designed:

<!-- generated:drift:start -->
| window | status | PSI confidence | PSI query length | PSI topic mix | abstention rate |
|---|---|---|---|---|---|
| control (normal traffic, new seed) | **OK** | 0.035 | 0.022 | 0.047 | 16.0% → 12.8% |
| drifted (40% home-loan / FD surge) | **ALERT** | 0.296 | 0.134 | 0.304 | 16.0% → 36.4% |

Each window is 500 requests run through the real pipeline; baseline is 500 requests. Details: [reports/monitoring_report.md](reports/monitoring_report.md).
<!-- generated:drift:end -->

## Change management

Any change to these **is a model change**:
- the prompt file
- the LLM provider, model or deployment version
- retrieval mode or parameters
- the knowledge-base documents
- the guardrail rules
- the golden set or the gates

For each one:
1. Open a change request that states the reason and the expected effect.
2. If retrieval or documents changed, re-run `calibrate` (dev) and `benchmark`.
3. Run `python -m bankrag eval`. **The gate must pass on the test split.** CI enforces this on every push and PR.
4. Review the error analysis in `reports/eval_report.md`, not just the pass/fail.
5. Record the code sha, config fingerprint, prompt sha and golden-set sha from the report metadata in the change record.
6. Material changes (new model family, new product documents, new intents) go back to independent validation.

Gates are never lowered to make a change pass. Lowering a gate is its own change request, with written
justification and validator sign-off.

## Human oversight

- The assistant only informs. Every action stays with humans: disputes, waivers, approvals, complaints.
- Abstentions and refusals point to the 24-hour contact centre. A pilot should add a live hand-off.
- Weekly sampled human review of answers, and any customer complaint mentioning the assistant, feed back into the golden set as new items.
- The business owner can switch the assistant off (kill switch), or reduce it to a fixed set of intents, without a code change.

## 5. Periodic review

- **Frequency:** at least every 6 months, and immediately after any document change, provider model update, or sustained monitoring ALERT.
- **Scope:** re-run the full evaluation on an *extended* golden set (new items from production review and complaints), re-check the drift baselines, review the limitations table, and confirm owners.
- **Outcome:** continue / continue with conditions / re-validate / retire.
