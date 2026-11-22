# Model card: bankrag

Two systems share this repository and this card. **Part A** is the policy Q&A assistant.
**[Part B](#part-b-document-verification)** is document verification for loan applications.

# Part A: policy Q&A assistant

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
- **Independent evaluation data (not gated, never tuned on):** the Banking77 test split, 3,080 real customer banking queries (PolyAI, CC-BY-4.0), with each of its 77 intents labelled covered / ambiguous / not covered by the knowledge base before any query was run; and deepset/prompt-injections, 662 prompts labelled injection or benign, written by people other than the developer. Sources and licences: `data/external/README.md`.
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

Test split: 28/34 items fully correct (answerable 17/20, unanswerable 3/6, injection 4/4, pii 4/4). Failed: ans-05, ans-17, ans-34, una-03, una-08, una-10. Every failure is explained in [reports/eval_report.md](../reports/eval_report.md).
<!-- generated:results:end -->

**Leakage control:** the threshold (`calibrate`) and the retrieval mode (`benchmark`) are chosen using
dev metrics only. `calibrate()` raises an error if given a non-dev item. The test split is used only to
make the gate decision. Dev metrics are therefore optimistic. Test metrics are the ones to quote.

**Statistical caveat:** with 34 test items, one item is ≈3 percentage points on whole-split rates and
≈17 points on the 6-item categories. The results show the system works and catch regressions. They are
not precise performance estimates.

### Independent evaluation (not gated)

<!-- generated:external:start -->
| measure | golden set (test, written by the author) | independent public data |
|---|---|---|
| Out-of-scope questions refused | 50.0% (6 near-miss items) | **85.4%** of 2,120 Banking77 queries from 53 uncovered intents |
| In-scope questions answered | 100.0% | **45.3%** of 360 Banking77 queries from 9 covered intents |
| In-scope retrieval hit@3 | 100.0% | **79.4%** |
| Legitimate questions blocked as injection | 0.0% | **0.0%** of 3,080 Banking77 queries; 0.0% of 399 deepset benign prompts |
| Injection attempts blocked | 100.0% (4 items) | **13.6%** of 169 English deepset injections (9.5% of all 263, incl. German) |

Details: [reports/external_eval.md](../reports/external_eval.md).
<!-- generated:external:end -->

These results are the most important evidence in this card: they show the golden-set safety figures
do not transfer to data written by other people (L4, L12).

### Limitations and known failure modes
| # | limitation | evidence | consequence | mitigation / next step |
|---|---|---|---|---|
| L1 | Near-miss unanswerable questions are answered | una-03 (home loan), una-08 (Gold card), una-10 (SME loan) | Confident, cited answer about the **wrong product** | answerability verifier or product-entity check; monitoring of abstention / topic mix; human hand-off |
| L2 | Extractive baseline selects the wrong sentences | ans-05, ans-17, ans-34 | Incomplete answers | LLM generator, measured against this baseline |
| L3 | Faithfulness ≠ correctness | una-08 is 100% faithful and wrong | Faithfulness alone would pass bad answers | gate combines key facts, citation, abstention |
| L4 | Injection rules do not generalise | 100% on the golden set but 13.6% of English deepset injections; rules were written with sight of the golden set (the phone regex was also fixed after pii-06 missed) | most real injection attempts reach retrieval and generation | trained classifier or LLM-based check, developed on deepset train and measured on deepset test; prompt and output guardrails remain the backstop |
| L5 | Pattern-based PII only | names (pii-06 "Ahmad"), addresses, account numbers not masked | residual personal data in masked logs | NER-based detector; log retention limits |
| L6 | Lexical key-fact matching | "five percent" ≠ "5%" | penalises paraphrasing LLMs | LLM judge validated against human labels |
| L7 | English only | none of the golden set is Malay or code-switched | unknown behaviour on BM queries | add BM items and evaluate |
| L8 | Small evaluation set | n=34 test | wide uncertainty | grow to ≥ 200 items with independent authors |
| L9 | Gates set after seeing baseline test results | thresholds.yaml header | gates are regression floors, not independent targets | validator sets targets for the production LLM independently |
| L10 | Document ambiguity not detected | DuitNow "unlimited free" vs RM5,000 free limit | possible partial answer | document owner review; conflict check at ingest |
| L11 | Confidence is not a probability | normalised score, threshold calibrated | scores are not comparable across modes or corpus changes | re-calibrate on every corpus change; consider Platt / isotonic scaling |
| L12 | Abstain threshold does not transfer to real phrasing | only 45.3% of covered Banking77 queries are answered (100% on the golden set) | many legitimate questions get a referral instead of an answer | recalibrate on a labelled dev sample of real queries |

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
| Independent injection set evaluated | ⚠️ done (deepset): 13.6% English detection, not acceptable (L4) |
| Real-query in-scope answer rate acceptable | ❌ 45.3% on Banking77 covered intents (L12) |
| Abstention recall target agreed with the business and met | ❌ baseline at the 50% floor (L1) |
| Production LLM evaluated with this harness | ❌ not yet. No real-LLM run has been made |
| Human hand-off and monitoring operational | ❌ pilot prerequisite |

**Recommendation: not approved for customer-facing use.** It's suitable as a development baseline and
evaluation harness. Agent-assist pilot approval is conditional on the open items above.

Approval record (to be completed): approver, date, version approved (git commit + the
`source_fingerprint` from the eval report metadata, which hashes the code, config, prompts, golden set,
gates and documents that produced the results), conditions, next review date.

## 4. Monitoring

| signal | method | threshold / action |
|---|---|---|
| Retrieval confidence distribution | PSI (10 baseline-quantile bins) + KS test, baseline window vs current window | PSI ≥ 0.10 investigate · > 0.25 act |
| Query length | PSI + KS | same bands |
| Topic mix (answered top document, abstained, blocked) | categorical PSI | same bands |
| Query terms (stemmed content terms of the PII-masked queries) | categorical PSI over the union of each window's 30 most frequent terms | same bands |
| Abstention / block / PII-bearing rates | absolute change | ≥ 10 pp → alert |
| Output PII leaks | output guardrail flag in traces | any → incident |
| Latency, tokens, cost | per-stage trace fields | operational SLOs |
| Answer quality | weekly sampled human review of answers + complaints tagged "assistant" | trend review |

`python -m bankrag monitor --fail-on-alert` exits 2 on ALERT, for a scheduled job. The drift scenario
replays real Banking77 queries with a simulated top-up surge. The first version of the monitor (without
query terms) reported that surge as OK; the query-terms signal was added after that miss, and the
repeated-seed check measures how it behaves on normal traffic:

<!-- generated:drift:start -->
| window | status | PSI confidence | PSI query length | PSI topic mix | PSI query terms | abstention rate |
|---|---|---|---|---|---|---|
| control (normal mix, new seed) | **OK** | 0.039 | 0.020 | 0.031 | 0.029 | 69.2% → 68.8% |
| drifted (40% top-up surge) | **ALERT** | 0.085 | 0.087 | 0.070 | 0.793 | 69.2% → 74.0% |

Real Banking77 queries and deepset injection attempts, 500 per window, run through the real pipeline. Repeated over 20 seeds: control windows gave 20 OK / 0 WATCH / 0 ALERT; drifted windows gave 0 OK / 0 WATCH / 20 ALERT. Details: [reports/monitoring_report.md](../reports/monitoring_report.md).
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
5. Record the git commit and the report's `source_fingerprint`, `prompt_sha` and `golden_set_sha` in the change record. Recomputing `source_fingerprint` from the approved checkout must give the same value, which proves the report belongs to that exact tree.
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

---

# Part B: Document verification

| | |
|---|---|
| **System** | Checks a payslip or bank statement against the loan application it was submitted with; decides `auto_accept`, `manual_review` or `reject` with reasons |
| **Version** | rules `doc-rules-v1`, anomaly model `data/docverify/anomaly_model.json` (robust z, fitted on dev), text-layer extraction |
| **Type** | Decision support inside loan origination. It decides whether a document is acceptable *evidence*, not whether the loan is granted |
| **Proposed risk tier** | High while it can auto-reject (it affects access to credit). Medium in the recommended pilot, where every reject is confirmed by a person |
| **Owners (roles)** | Business owner: Head of Consumer Lending Operations · Model owner: AI Engineering · Rule owner: Credit Risk / Fraud · Independent validator: Model Risk Management |

## B1. Development

### Intended use
- Screen income documents at intake: catch documents that contradict themselves, the applicant or an earlier submission, and send suspicious ones to a reviewer with the reasons.
- Auto-accept documents with no findings, so reviewers spend their time on the rest.

### Out-of-scope use
- Credit decisions, affordability or income assessment. The extracted figures are not verified income.
- Scanned documents or phone photos (no text layer): they go to manual review because OCR isn't implemented.
- Document types other than Malaysian payslips and bank statements.
- Proving a document is genuine. `auto_accept` means *no check found a problem*, not *verified with the issuer*.

### Data
- **Synthetic only.** Fictional people, employers and banks, generated by `docverify/synth.py`:
  - 6 layouts: P1, P2, S1 and S2 in both splits; P3 and S3 held out of dev.
  - English and Malay labels.
  - Simplified EPF, SOCSO, EIS and PCB rules.
  - 8 kinds of tampering, written by the same person who wrote the checks.
- **Splits:** dev (seed 11) is used to fit the anomaly model and choose its method; test (seed 23, 350 documents: 200 genuine, 150 tampered) is gated. No real documents were used.
- **Personal data:** the PDF is processed in memory and not stored. The response masks the NRIC. The audit log and registry keep only hashes. See [ARCHITECTURE.md](ARCHITECTURE.md#personal-data-flow).

### Design summary
text-layer extraction (spans with font and position) → field parsing → checks (arithmetic, statutory
rates, identity, dates, PDF metadata, fonts) + anomaly score + duplicate registry → rule engine
([rules/document_rules.yaml](../rules/document_rules.yaml)) → decision with every matching reason → audit
event. Rationale: ADR-007 (anomaly method), ADR-008 (rule policy), ADR-009 (audit).

### Evaluation results (synthetic test split, gated in CI)

<!-- generated:docverify:start -->
| measure | **test** (200 genuine, 150 tampered; gated) | dev (used to fit the anomaly model) |
|---|---|---|
| Tampered documents not auto-accepted | **94.0%** | 95.3% |
| Tampered documents auto-rejected | **60.0%** | 60.0% |
| Genuine documents auto-accepted | **96.5%** | 94.0% |
| Genuine documents wrongly rejected | **0.0%** | 0.0% |
| Automation rate at 2% fraud prevalence | **95.9%** | 93.5% |
| Error rate of automatic decisions at 2% prevalence | **0.1%** | 0.1% |
| Field extraction F1 | **100.0%** | 100.0% |

| tamper kind | arithmetic | consistent forgery | font inconsistency | metadata modified | invalid nric | period mismatch | name mismatch | duplicate resubmission |
|---|---|---|---|---|---|---|---|---|
| recall (test) | 100.0% | 55.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

Recall = share not auto-accepted. All documents are synthetic and the tampering was written by the same author as the checks, so recall on rule-detectable kinds is close to true by construction; the informative numbers are consistent forgery, the held-out layouts and the genuine review rate. Details and every miss: [reports/docverify_report.md](../reports/docverify_report.md).
<!-- generated:docverify:end -->

Every gate, every miss and the per-layout results are in [reports/docverify_report.md](../reports/docverify_report.md).

### Limitations and known failure modes

| # | limitation | evidence | consequence | mitigation / next step |
|---|---|---|---|---|
| D1 | Synthetic data, same author for tampering and checks | the whole corpus | recall on rule-detectable kinds is close to true by construction; no estimate of real-world performance | shadow-mode pilot on real submissions, with reviewer outcomes as labels |
| D2 | Consistent statement forgeries are mostly missed | 1/10 statements on test, 3/10 on dev | an internally consistent fake statement is auto-accepted | cross-document check: statement salary credit vs payslip net pay (README next step 7) |
| D3 | Payslip anomaly detection relies on unchanged tax | `pcb_vs_schedule` feature (ADR-007) | a forger who recomputes PCB defeats it | source data (EPF / LHDN / open banking) rather than a better single-document model |
| D4 | Forensic checks are easy to evade | metadata and font checks | a careful forger re-renders the whole PDF | they only trigger review, never reject (ADR-008) |
| D5 | Simplified payroll rules | `statutory.py` | false statutory mismatches on real payslips | official EPF, SOCSO, EIS and PCB tables before production |
| D6 | No OCR | text layer required | scans always go to review | OCR evaluated on a labelled public dataset |
| D7 | Name matching is token-based | 6 genuine test documents sent to review for a partial name match | reviewer load on names with patronymics and abbreviations | measure on real name pairs; tune with reviewers |
| D8 | Reviewer outcomes aren't recorded | no `review_decision` event | overturn rates can't be measured, so the rules can't be tuned on outcomes | README next step 6 |

## B2. Independent validation

1. **Reproduce:** `pip install -e ".[dev,docs,synth]"`, then `python -m bankrag doc-eval`. The gates must pass and the corpus hashes must match the report.
2. **Challenge the data:** have someone other than the developer forge documents without reading the checks, ideally from real templates with consent. Report recall before any rule change.
3. **Challenge the gate:** `tests/test_docverify_gates.py` disables each check group and the gate must fail. Add a mutant for every new check.
4. **Conceptual soundness:** is each `reject` rule hard evidence? Is fail-closed complete (every extraction failure reaches R20)? Does the 2%-prevalence assumption match the bank's fraud data?
5. **Privacy:** confirm nothing identifying is stored (`audit demo` scans the database file) and review the unsalted registry fingerprint (ARCHITECTURE.md).

## B3. Approval

| condition | status |
|---|---|
| Synthetic test-split gates pass | ✅ 12/12 |
| No genuine document auto-rejected on test | ✅ 0.0% |
| Evaluated on real documents | ❌ not done; synthetic only (D1) |
| Independent forgeries evaluated | ❌ not done |
| Reviewer workflow and outcome capture in place | ❌ (D8) |
| Official statutory tables | ❌ (D5) |

**Recommendation: not approved for automatic rejection.** Suitable for a **shadow-mode pilot**: run on
real submissions alongside the current manual process, show reasons to reviewers, record their decisions,
and measure agreement. After that, `auto_accept` could be enabled for documents with no findings, and
`reject` kept as "review with a strong recommendation" until reviewer agreement on rejects is measured.

## B4. Monitoring

| signal | how | status |
|---|---|---|
| Decision mix per week (accept / review / reject) | `sql/decisions_by_week.sql` over the audit log | ✅ query exists |
| Finding-code frequencies | `sql/finding_codes.sql` | ✅ query exists |
| Review queue and its age | `sql/review_queue.sql` | ✅ query exists |
| Documents shared between applicants | `sql/shared_documents.sql` | ✅ query exists |
| Rule and model versions in force | `sql/versions_in_force.sql` | ✅ query exists |
| Anomaly score distribution | PSI against the dev baseline, as for Q&A confidence | ❌ not built |
| Reviewer overturn rate per rule | needs `review_decision` events | ❌ not built (D8) |
| Audit integrity | `bankrag audit verify --anchor` on a schedule | ⚠️ command exists; the schedule and anchor export are not built |

A sudden rise in one finding code points to a new layout from a major employer or bank, which is worth
checking with a reviewer before suspecting fraud.

## B5. Change management and oversight

- Any change to the rules file, the anomaly model, the checks, the extraction or the gates is a model change. It follows Part A's change process, with `python -m bankrag doc-eval` as the gate.
- Rule changes are owned by Credit Risk / Fraud and reviewed by AI Engineering. Lowering a gate needs validator sign-off.
- Reviewers see every reason and finding. They can overrule any decision, and the loan decision always stays with credit.
- Kill switches, both in the rules file and needing no code change: `default: manual_review` stops automatic acceptance (documents with no findings go to a person), and setting R01-R06 to `manual_review` stops automatic rejection. Both are tested.
