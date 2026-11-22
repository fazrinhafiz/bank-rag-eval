# Document verification evaluation

Rules `doc-rules-v1` · source fingerprint `9359713c6abb` · test corpus `1b73e393e244` (350 documents: 200 genuine, 150 tampered) · dev corpus `ec3957609095`.

**All documents are synthetic** (`src/bankrag/docverify/synth.py`): fictional people, employers and banks, simplified payroll rules, and tampering simulated by the same author who wrote the checks. These numbers show the checks do what they were designed to do and stay correct as the code changes. They are **not** an estimate of performance on real documents - see the limitations at the end.

## Gates (test split)

| gate | value | threshold | result | why |
|---|---|---|---|---|
| genuine_reject_rate | 0.0% | <= 0.0% | PASS | Auto-rejection must rest on hard evidence; no genuine document may be auto-rejected. |
| auto_decision_error_rate | 3.1% | <= 5.0% | PASS | When the machine decides alone it must almost always be right (corpus mix). |
| extraction_f1 | 100.0% | >= 99.0% | PASS | Text-layer extraction, including the held-out layouts P3 and S3. |
| recall_arithmetic | 100.0% | >= 95.0% | PASS | Rule-detectable: totals that do not add up. |
| recall_invalid_nric | 100.0% | >= 95.0% | PASS | Rule-detectable: impossible MyKad number. |
| recall_period_mismatch | 100.0% | >= 95.0% | PASS | Rule-detectable: dates contradict the stated period. |
| recall_name_mismatch | 100.0% | >= 95.0% | PASS | Rule-detectable: someone else's document. |
| recall_duplicate_resubmission | 100.0% | >= 95.0% | PASS | Registry: same document from another applicant. |
| recall_font_inconsistency | 100.0% | >= 90.0% | PASS | Forensic: edited amounts in another font or size. |
| recall_metadata_modified | 100.0% | >= 90.0% | PASS | Forensic: saved again by an editing tool. |
| recall_consistent_forgery | 55.0% | >= 40.0% | PASS | KNOWN WEAKNESS: only the anomaly score can see it; dev 65% (payslips 10/10, statements 3/10). Floor below dev. |
| genuine_auto_accept_rate | 96.5% | >= 90.0% | PASS | At most ~1 in 10 genuine documents sent to a human. |

## Headline

| metric | test | dev |
|---|---|---|
| Tampered documents not auto-accepted | **94.0%** | 95.3% |
| Tampered documents auto-rejected | **60.0%** | 60.0% |
| Genuine documents auto-accepted | **96.5%** | 94.0% |
| Genuine documents sent to manual review | **3.5%** | 6.0% |
| Genuine documents wrongly rejected | **0.0%** | 0.0% |
| Automation rate (corpus mix) | **83.4%** | 81.4% |
| Error rate of automatic decisions (corpus mix) | **3.1%** | 2.5% |
| Automation rate at 2% fraud prevalence | **95.9%** | 93.5% |
| Error rate of automatic decisions at 2% prevalence | **0.1%** | 0.1% |
| Field extraction F1 | **100.0%** | 100.0% |
| Documents with every field exact | **100.0%** | 100.0% |
| Anomaly score AUROC (genuine vs amount-changing tampers) | **0.784** | 0.834 |

## Recall by tamper kind (test)

Recall = share NOT auto-accepted (sent to review or rejected).

| kind | what was done | payslip | statement | recall | checks that fired |
|---|---|---|---|---|---|
| arithmetic | one figure inflated, totals left unchanged | 10/10 (10 rejected) | 10/10 (10 rejected) | **100.0%** | `arithmetic_mismatch` 20, `anomaly_score_high` 6, `name_partial_match` 2 |
| consistent_forgery | salary inflated and every total recomputed; tax (PCB) left as it was | 10/10 (0 rejected) | 1/10 (0 rejected) | **55.0%** | `anomaly_score_high` 11 |
| font_inconsistency | consistent forgery, edited figures drawn in a different font or size | 10/10 (0 rejected) | 10/10 (0 rejected) | **100.0%** | `font_inconsistent` 20, `anomaly_score_high` 10 |
| metadata_modified | consistent forgery, saved again by an editing tool (incremental update or full rewrite) | 10/10 (0 rejected) | 10/10 (0 rejected) | **100.0%** | `pdf_rewritten_after_creation` 20, `anomaly_score_high` 10, `pdf_editor_producer` 10, `pdf_incremental_update` 10 |
| invalid_nric | fabricated identity: impossible NRIC (payslips only; statements carry no NRIC) | 10/10 (10 rejected) | - | **100.0%** | `nric_invalid` 10 |
| period_mismatch | old document relabelled with a recent period; dates inside it not changed | 10/10 (10 rejected) | 10/10 (10 rejected) | **100.0%** | `date_outside_period` 20 |
| name_mismatch | someone else's genuine document submitted | 10/10 (10 rejected) | 10/10 (10 rejected) | **100.0%** | `name_mismatch` 20, `nric_mismatch` 10 |
| duplicate_resubmission | a document already submitted by another applicant, as-is or with the name and NRIC changed | 10/10 (10 rejected) | 10/10 (10 rejected) | **100.0%** | `duplicate_other_applicant` 20, `name_mismatch` 10, `nric_mismatch` 5, `pdf_modified_after_creation` 2 |

## Genuine documents (test)

Findings on genuine documents: `pdf_modified_after_creation` 12, `name_partial_match` 6, `anomaly_score_high` 1. `pdf_modified_after_creation` is informational (a statement re-downloaded later) and triggers no rule.

| layout | in dev? | genuine auto-accepted | tampered detected | extraction F1 |
|---|---|---|---|---|
| P1 | yes | 94.1% of 34 | 100.0% of 30 | 1.000 |
| P2 | yes | 100.0% of 33 | 100.0% of 27 | 1.000 |
| P3 | **no (held out)** | 93.9% of 33 | 100.0% of 23 | 1.000 |
| S1 | yes | 100.0% of 34 | 89.7% of 29 | 1.000 |
| S2 | yes | 93.9% of 33 | 85.0% of 20 | 1.000 |
| S3 | **no (held out)** | 97.0% of 33 | 85.7% of 21 | 1.000 |

## Missed tampering: 9 auto-accepted

| id | kind | variant | layout | anomaly score |
|---|---|---|---|---|
| test-0290 | consistent_forgery | recomputed | S1 | 2.658 |
| test-0291 | consistent_forgery | recomputed | S2 | 1.349 |
| test-0292 | consistent_forgery | recomputed | S3 | 1.054 |
| test-0294 | consistent_forgery | recomputed | S2 | 2.278 |
| test-0295 | consistent_forgery | recomputed | S3 | 2.018 |
| test-0296 | consistent_forgery | recomputed | S1 | 4.429 |
| test-0297 | consistent_forgery | recomputed | S2 | 1.349 |
| test-0298 | consistent_forgery | recomputed | S3 | 1.193 |
| test-0299 | consistent_forgery | recomputed | S1 | 1.694 |

## Genuine documents not auto-accepted: 7

| id | layout | variant | decision | findings |
|---|---|---|---|---|
| test-0006 | P1 | plain | manual_review | name_partial_match |
| test-0012 | P1 | plain | manual_review | name_partial_match |
| test-0014 | P3 | plain | manual_review | name_partial_match |
| test-0044 | P3 | plain | manual_review | name_partial_match |
| test-0123 | S3 | plain | manual_review | name_partial_match |
| test-0134 | S2 | plain | manual_review | name_partial_match |
| test-0158 | S2 | plain | manual_review | anomaly_score_high |

## Anomaly model (fitted and selected on dev only)

Trained on genuine documents of the dev split (seed 11, corpus ec3957609095). Threshold = 97% quantile of out-of-fold scores of genuine dev documents. Method chosen per document type by recall on dev amount-changing tampers at that threshold (ties: AUROC).

| doc type | features | method | dev recall (amount tampers) | dev AUROC | dev genuine flagged | selected |
|---|---|---|---|---|---|---|
| payslip | log_gross, pcb_vs_schedule, net_ratio, allowance_ratio | robust_z | 100.0% | 1.000 | 3.0% | ✅ |
| payslip | log_gross, pcb_vs_schedule, net_ratio, allowance_ratio | iforest | 3.3% | 0.851 | 3.0% |  |
| bank_statement | log_opening, log_credits, log_debits, credit_debit_log_ratio, closing_vs_opening, n_transactions, largest_credit_share | robust_z | 6.7% | 0.624 | 3.0% | ✅ |
| bank_statement | log_opening, log_credits, log_debits, credit_debit_log_ratio, closing_vs_opening, n_transactions, largest_credit_share | iforest | 0.0% | 0.584 | 3.0% |  |

## Limitations

- Synthetic documents, synthetic tampering, same author for both: high recall on rule-detectable kinds is close to true by construction. The informative rows are the hard ones (consistent forgery), the held-out layouts, and the genuine documents sent to review.
- The payslip anomaly feature `pcb_vs_schedule` was designed knowing the simulated forger leaves tax unchanged. A forger who also recomputes PCB defeats it; the 100% on payslip consistent forgeries is the upper bound of that design.
- A bank statement whose arithmetic was kept consistent has nothing inside it to contradict: the anomaly score catches few of these. The fix is a cross-document check (salary credit on the statement vs net pay on the payslip) or data from the source (open banking, EPF/LHDN), not a better model of one document.
- Process note: the test split was run once with the first anomaly design (IsolationForest on ratio features, 0% of consistent forgeries on dev and test). The redesign and method choice used dev only; gates were written before this test run.
- Text-layer PDFs only. Scans and photos have no text layer and go to manual review; OCR is not implemented.
- Each tampered document carries one kind of tampering. Real forgeries often leave several traces, which makes them easier to catch, but real forgers also adapt to known checks.
- Forensic checks are easy to evade once known (strip metadata, re-render the whole PDF in one font). They raise the cost of casual edits; they do not stop a determined forger.
- Payroll rules are simplified. Real EPF/SOCSO/EIS tables and PCB have bands and exceptions, so the statutory check would need the official tables before production use.
- The expected rates assume 2% of submissions are tampered and every tamper kind is equally common.
