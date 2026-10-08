# External datasets

Two public datasets, used **only as independent test sets**. Nothing in the pipeline (rules, thresholds,
retrieval mode) is tuned on them. Re-create the files with `python scripts/fetch_external_data.py`
(`pip install -e ".[fetch]"` first); source URLs and SHA-256 hashes are in `SOURCES.json`.

| file | source | licence | rows | used for |
|---|---|---|---|---|
| `banking77_test.jsonl` | **Banking77** test split. Casanueva et al. (2020), *Efficient Intent Detection with Dual Sentence Encoders*, PolyAI. [Hugging Face](https://huggingface.co/datasets/PolyAI/banking77) · [GitHub](https://github.com/PolyAI-LDN/task-specific-datasets) | CC-BY-4.0 | 3,080 | real customer phrasing: out-of-scope abstention, in-scope retrieval, false blocks; drift-simulation traffic |
| `deepset_prompt_injections.jsonl` | **deepset/prompt-injections**, deepset GmbH. [Hugging Face](https://huggingface.co/datasets/deepset/prompt-injections) | Apache-2.0 per the dataset tags (its metadata also says CC-BY-4.0); both permissive | 662 (train + test) | independent test of the injection rules: detection rate and false-positive rate |

Changes made: Banking77 rows are given ids (`b77-NNNN`) and kept verbatim. The deepset parquet files are
converted to JSONL with an `is_injection` boolean and their original split; text is verbatim. Part of it is
German; the heuristic count is in `reports/external_eval.md`.

`banking77_intent_map.yaml` labels each of the 77 intents as **covered** by the knowledge base (with the
doc ids that answer it), **ambiguous** (excluded from scoring) or **not covered**. It was committed before
any Banking77 query was run through the pipeline.
