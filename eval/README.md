# Golden evaluation set

`golden_set.jsonl` has one JSON object per line:

| field | meaning |
|---|---|
| `id` | stable id (`ans-`, `una-`, `inj-`, `pii-` prefix) |
| `question` | the user query, verbatim |
| `category` | `answerable` · `unanswerable` · `injection` · `pii` |
| `split` | `dev` (threshold calibration only) or `test` (quality gate only) |
| `expected_doc` | `doc_id` that contains the answer, `null` if none |
| `key_facts` | short strings that must appear in a correct answer (case-insensitive, verbatim from the doc) |
| `expect_abstain` | `true` if the correct behaviour is to refuse / abstain |
| `pii_values` | (pii only) raw PII strings that must never appear in an answer or a trace |
| `notes` | (optional) why the item is hard |

## Composition

| category | dev | test | total | purpose |
|---|---|---|---|---|
| answerable | 15 | 20 | 35 | spread over all 7 docs; includes paraphrased, indirect and cross-doc "trap" questions |
| unanswerable | 4 | 6 | 10 | 2 off-topic, 8 in-domain but not covered (home loan, fixed deposit, FX, branch hours, car financing, a non-existent card, takaful, SME loan) |
| injection | 2 | 4 | 6 | instruction override, system-prompt extraction, role-play jailbreak, fake system message |
| pii | 2 | 4 | 6 | Malaysian NRIC, Luhn-valid card numbers, MY mobile numbers, email; each still has a real answer |

The set is small, so every metric has a wide confidence interval: one test-split item
is ~3 percentage points. Treat results as a regression signal, not a precise estimate.

`tests/test_golden_set.py` checks the labels: every key fact must appear verbatim in its
expected doc, every category must appear in both splits, and the dev share must be 35–45%.
