# Architecture

How the parts of `bankrag` fit together, where personal data goes, and how a change reaches production.
For the API contract, see [INTEGRATION_SPEC.md](INTEGRATION_SPEC.md). For the reasoning behind each
choice, see [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md).

## System

```mermaid
flowchart LR
    subgraph callers["Calling systems (not in this repo)"]
        CH["Customer channel<br/>app · web · contact centre"]
        LOS["Loan origination system"]
        REV["Document reviewers"]
    end
    GW["API gateway<br/>authentication · rate limits · TLS<br/>(not in this repo)"]
    CH --> GW
    LOS --> GW

    subgraph svc["bankrag service: one container, one replica"]
        API["FastAPI<br/>/ask · /documents/verify · /health<br/>X-Request-ID on every call"]
        subgraph rag["Policy Q&A"]
            G1["Input guardrail<br/>PII masking · injection rules"] --> R["Retriever<br/>BM25 · TF-IDF · hybrid"]
            R --> A{"confidence ≥<br/>threshold?"}
            A -- yes --> GEN["Generator<br/>offline baseline or LLM"] --> G2["Output guardrail<br/>PII leak · citations"]
        end
        subgraph doc["Document intake"]
            X["PDF extraction<br/>text layer · fonts · metadata"] --> CK["Checks<br/>arithmetic · statutory · identity<br/>dates · forensics"]
            X --> AN["Anomaly score<br/>robust z, fitted on dev"]
            X --> REG["Duplicate registry<br/>salted identity hashes"]
            CK & AN & REG --> RU["Rule engine<br/>rules/document_rules.yaml"]
        end
        API --> G1
        API --> X
    end

    KB[("Policy documents<br/>data/docs")] --> R
    LLM["LLM provider<br/>(optional, external)"]
    GEN -. masked question + retrieved passages .-> LLM
    X -. optional LLM extractor:<br/>document text, NRIC masked .-> LLM

    AUD[("Audit log<br/>SQLite, HMAC hash chain<br/>+ duplicate registry")]
    TR[("Traces<br/>JSONL, masked")]
    LOGS[["JSON logs on stdout"]]
    G2 --> AUD
    RU --> AUD
    G2 --> TR
    API --> LOGS
    RU -- manual_review --> REV
    AUD -. "audit head" .-> WORM[("Write-once anchor store")]
    TR --> MON["Drift monitor<br/>PSI · KS · rates"]
```

**Two automations, one service.** Both answer through the same API, and both follow the same rules:
1. every decision carries a reason code
2. every decision is recorded in the audit log before the response is returned
3. anything the service cannot check goes to a person, not to an error

**What sits outside the repo:**
- **The API gateway.** It does authentication and rate limiting; the service has no auth of its own.
- **The callers**, including the reviewer workflow that receives `manual_review` documents.
- **A real LLM.** The default generator is an offline extractive baseline, so nothing leaves the process unless a provider is configured.

**Single replica.** The audit log and duplicate registry are one SQLite file with one writer. Scaling out
means moving them to Postgres first (ADR-009, [infra/azure/README.md](../infra/azure/README.md#known-gaps)).

## Personal data flow

The design principle: personal data is used where a decision needs it, then reduced to a mask, a count
or a salted hash before anything is written down.

```mermaid
flowchart LR
    IN["Request<br/>question · applicant name · NRIC · PDF"] --> MEM["In memory only<br/>for the duration of the request"]
    MEM -- "masked question<br/>(NRIC, card, phone, email → [TYPE])" --> LLM["LLM provider<br/>(if configured)"]
    MEM -- "masked question<br/>salted hash · PII types" --> TR[("Trace")]
    MEM -- "ids · outcome · reason codes<br/>versions · file hash<br/>salted identity hash" --> AUD[("Audit log")]
    MEM -- "ids · decision · codes · latency" --> LOG[["Logs"]]
    MEM -- "decision · reasons · fields<br/>NRIC → XXXXXX-XX-1234<br/>transactions → count" --> OUT["Response to caller"]
```

| data | where it goes | how it's stored | how it's checked |
|---|---|---|---|
| Question text | memory; masked copy to the generator | trace: PII-masked text + salted HMAC hash. Logs and audit: not at all | PII leak gate = 0 on the golden set; `test_pii_never_reaches_response_logs_or_trace` |
| NRIC, card, phone or email in a question | replaced by `[NRIC]`, `[CARD]` and so on before retrieval | trace keeps only the *types* found | same tests; Luhn and NRIC date checks avoid masking random numbers |
| Names or addresses in a question | **not detected**: pattern-based masking misses them | they stay in the masked trace text | known limitation (model card L5). Traces need access control and retention limits |
| Applicant name, NRIC (application) | memory, for the identity check | registry: salted hash of the NRIC, or of the sorted name tokens when there's no NRIC | audit demo scans the database file: 0 names or NRICs found in 384 events |
| The PDF | memory only; never written to disk | audit: its SHA-256 | container smoke test checks the audit database for raw NRICs |
| Extracted fields (name, employer, amounts) | the response to the caller, NRIC masked | not stored. Registry keys are a truncated, *unsalted* SHA-256 of employer, staff number, period and two amounts (bank, account number, period and balances for a statement) | the key reveals nothing on its own, but someone holding the same document could confirm it was submitted. A salted or keyed fingerprint is the fix |
| Document text, with the optional LLM extractor | sent to the provider with the NRIC masked. Names and amounts are sent | not stored by `bankrag` | off by default; the provider's data terms apply |

**Secrets.** `BANKRAG_TRACE_SALT` (trace and identity hashes) and `BANKRAG_AUDIT_KEY` (audit chain) come
from the environment, which is Key Vault in the Azure design. The development key and salt each trigger a
warning at start-up. Rotating the trace salt breaks repeat-question and duplicate-applicant matching across the
rotation. Rotating the audit key needs a signed "key rotated" event, which isn't implemented.

## From change to production

```mermaid
flowchart LR
    PR["Change<br/>code · prompt · rules · docs · gates"] --> CI
    subgraph CI["CI on every push"]
        T["pytest<br/>240+ tests incl. mutation tests"] --> E["RAG gate<br/>golden test split"]
        E --> D["Document gate<br/>synthetic test corpus"]
        D --> AU["Audit tamper test"]
        AU --> C["Container build<br/>+ 11-check smoke test"]
        TF["terraform fmt + validate<br/>+ policy tests"]
    end
    CI -- all pass --> IMG["Image tagged with git SHA<br/>source_fingerprint in /health"]
    IMG -. not done: no subscription .-> ACA["Azure Container Apps"]
```

`source_fingerprint` hashes everything that can change a result: code, config, prompts, rules, policy
documents, golden set, gates and the anomaly model. The eval reports, `/health` and the change record all
carry it, so a running container can be matched to the evidence it was approved on.
