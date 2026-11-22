# Integration specification

For a team connecting a channel (mobile app, contact-centre desktop, loan origination system) to the
`bankrag` service. API version **0.1.0**. The endpoint and field tables, and the
machine-readable [openapi.json](openapi.json), are generated from the code by
`python scripts/make_integration_spec.py`. A test fails if they fall out of date. The examples
are real responses from the service, made with the synthetic sample documents in [samples/](samples/).

**Status:** runs locally and in the container image. It is not deployed anywhere (see
[infra/azure/README.md](../infra/azure/README.md)).

## 1. What the service decides, and what it leaves to the caller

| endpoint | the service decides | the caller decides |
|---|---|---|
| `POST /ask` | `answered`, `abstained` (not confident, or no valid citation) or `blocked` (injection, PII in the output) | what to show the customer, and the hand-off to a human when the outcome is not `answered` |
| `POST /documents/verify` | `auto_accept`, `manual_review` or `reject`, with every reason | the credit decision. A `reject` means the document is not acceptable evidence, not that the loan is declined |

A `manual_review` must reach a person with the `reasons` and `findings`. Nothing else in the response
replaces that review.

## 2. Conventions

- **Transport:** JSON over HTTPS. The service listens on port 8000 and expects TLS to end in front of it
  (in the Azure design, at Container Apps ingress).
- **Authentication:** none in the service itself. It is meant to sit behind an API gateway that
  authenticates the calling system (mTLS or OAuth client credentials) and applies rate limits. Do not
  expose it directly.
- **`X-Request-ID`:** send your own correlation id (1-64 characters of `A-Z a-z 0-9 . _ -`). It is returned
  in the header and the body, and recorded in the logs, the trace and the audit log. Anything else is
  replaced by a generated id, which is returned the same way. Store it: it is how support and audit find one
  interaction (`bankrag audit query sql/lookup_request.sql --param id=<application_id>`).
- **Dates** are `YYYY-MM-DD`. Money in `fields` is a number in ringgit (`8700.0`).
- **Versions:** every response can be tied to the code and rules that produced it: `/health` reports
  `source_fingerprint` and `prompt_version`, and `/documents/verify` returns `rules_version`. Record them
  with the decision.

## 3. Failure behaviour

| situation | response | what the caller should do |
|---|---|---|
| Invalid JSON or a field outside its constraints | `422` with FastAPI's `detail` list | fix the request; do not retry |
| `document_base64` is not base64 | `422` with `{"detail": "..."}` | fix the request |
| Document larger than the limit | `413` with `{"detail": "..."}` | ask the applicant for a smaller file |
| Document unreadable, scanned, unsupported, or a check crashed | `200` with `manual_review` and rule `R20` | route to a person. This is fail-closed by design, not an error |
| Verification not installed in this image | `503` | configuration problem; alert the service owner |
| Unhandled error, **or the audit record could not be written** | `500` with `{"error": "internal_error", "request_id": ...}` | retry with backoff using the same `X-Request-ID`. No decision is ever returned without its audit record |

**Retries are safe.** Sending the same document again for the same `application_id` gives the same
decision and is not treated as a duplicate submission (tested). The same document under a different
application by a different applicant is rejected as `duplicate_other_applicant`. Each attempt is a
separate audit event, so retries stay visible.

**Timeouts:** answers and document checks are synchronous and CPU-bound. With the offline baseline they
take milliseconds to well under a second; with an external LLM, allow for the provider's latency. A client
timeout of 30 seconds is a reasonable starting point. Measure it in your environment.

## 4. Personal data

- Send only what the field descriptions ask for. The NRIC is optional; without it the identity check falls
  back to the applicant's name.
- The question text is never echoed back, never logged and never written to the audit log. Traces keep
  only a PII-masked copy and a salted hash.
- The PDF is processed in memory and not stored. The response returns the extracted fields with the
  NRIC masked to its last four digits and transactions reduced to a count.
- The audit log holds ids, decisions, reason codes, versions and hashes, never names, NRICs or amounts.

See [ARCHITECTURE.md](ARCHITECTURE.md#personal-data-flow) for where each item of personal data goes.

## 5. Endpoints

### `GET /health`: Liveness and the versions behind every answer

| status | body | meaning |
|---|---|---|
| 200 | `Health` | Successful Response |

### `POST /ask`: Answer one policy question

Runs the guarded pipeline: mask PII, block injections, retrieve, abstain below the calibrated threshold, generate with citations, check the output. The question is never echoed back or logged.

Request body: `AskRequest`.

| status | body | meaning |
|---|---|---|
| 200 | `AskResponse` | Successful Response |
| 422 | `HTTPValidationError` | Validation Error |
| 500 | `InternalError` | Unhandled error, or the audit record could not be written. No decision is returned without its audit record. |

### `POST /documents/verify`: Check a payslip or bank statement

Extracts the fields, checks them against each other, the application, earlier submissions and the file's own history, then applies the rule policy. Unreadable or unsupported documents come back as manual_review (fail closed), not as an error. Re-sending the same document for the same application is safe: it does not count as a duplicate.

Request body: `VerifyRequest`.

| status | body | meaning |
|---|---|---|
| 200 | `VerifyResponse` | Successful Response |
| 413 | `ErrorDetail` | Document larger than docverify.max_upload_mb. |
| 422 | `HTTPValidationError` | Validation Error |
| 500 | `InternalError` | Unhandled error, or the audit record could not be written. No decision is returned without its audit record. |
| 503 | `ErrorDetail` | Document verification is not installed in this image. |

## 6. Models

#### `AskRequest`

| field | type | required | notes |
|---|---|---|---|
| `question` | string | yes | Customer question. PII is masked before processing. min length 1, max length 2000 |

#### `AskResponse`

| field | type | required | notes |
|---|---|---|---|
| `request_id` | string | yes | Your X-Request-ID, or the one generated for this call. |
| `answer` | string | yes | The answer with [doc_id#section] citations, or the abstention / refusal text to show instead. |
| `outcome` | string | yes | answered \| abstained \| blocked |
| `reason` | string | yes | ok \| low_confidence \| generator_abstained \| citation_missing \| pii_leak_blocked \| injection_blocked |
| `confidence` | number | yes | Retrieval confidence (normalised score, not a probability). Below the calibrated threshold the service abstains. |
| `citations` | list of string | yes | Policy sections the answer is drawn from, as doc_id#section. |
| `prompt_version` | string | yes | Version of the prompt that produced the answer. |
| `model` | string | yes | Generator that produced the answer (offline-extractive-v1 for the baseline). |

#### `VerifyRequest`

| field | type | required | notes |
|---|---|---|---|
| `application` | Application | yes |  |
| `document_base64` | string | yes | The PDF, base64-encoded. Max size: docverify.max_upload_mb. min length 1 |

#### `Application`

| field | type | required | notes |
|---|---|---|---|
| `application_id` | string | yes | Your loan application id. Duplicate detection compares documents across applications. pattern `^[A-Za-z0-9._-]{1,64}$` |
| `applicant_name` | string | yes | As on the application; compared with the name on the document. min length 1, max length 200 |
| `nric` | string or null | no | Optional MyKad number (with or without dashes). Without it, identity is checked by name only. |
| `application_date` | string (date) | yes | Dates on the document are checked against this date (stale or future-dated documents). |

#### `VerifyResponse`

| field | type | required | notes |
|---|---|---|---|
| `request_id` | string | yes | Your X-Request-ID, or the one generated for this call. |
| `decision` | string | yes | auto_accept \| manual_review \| reject |
| `reasons` | list of Reason | yes | Every rule that matched; empty when auto-accepted. |
| `findings` | list of FindingOut | yes | Everything the checks found, including findings no rule acts on. |
| `doc_type` | string or null | yes | payslip \| bank_statement, or null if not recognised. |
| `fields` | object | yes | Extracted fields; NRIC masked to the last 4 digits, transactions summarised. |
| `missing_fields` | list of string | yes | Required fields that could not be extracted (the document then goes to review). |
| `anomaly_score` | number or null | yes | How unusual the figures are compared with genuine documents; null if not scored. |
| `anomaly_threshold` | number or null | yes | Scores above this raise anomaly_score_high (manual review). |
| `sha256` | string | yes | SHA-256 of the file as received. Also in the audit log. |
| `rules_version` | string | yes | Version of rules/document_rules.yaml that made the decision. |
| `extraction_method` | string | yes | How fields were read: text_layer, or llm when the optional LLM extractor is used. |

#### `Reason`

| field | type | required | notes |
|---|---|---|---|
| `rule` | string | yes | Rule id in rules/document_rules.yaml, e.g. R01. |
| `decision` | string | yes | What this rule decides: manual_review \| reject. |
| `reason` | string | yes | Plain-language reason, suitable for a reviewer. |
| `codes` | list of string | yes | The finding codes that triggered the rule. |

#### `FindingOut`

| field | type | required | notes |
|---|---|---|---|
| `code` | string | yes | Stable finding code, e.g. arithmetic_mismatch. |
| `source` | string | yes | Check group that produced it: consistency \| extraction \| forensics \| anomaly \| registry \| system. |
| `severity` | string | yes | high \| medium \| info. The decision comes from the rules, not from severity. |
| `detail` | string | yes | What was found. Never the full NRIC; may quote the invalid part, such as an impossible birth date. |

#### `Health`

| field | type | required | notes |
|---|---|---|---|
| `status` | string | yes | ok when the service is up. |
| `version` | string | yes | Package version. |
| `source_fingerprint` | string | yes | Hash of the code, config, prompts, rules, documents and gates; matches the eval reports it was gated on. |
| `retrieval_mode` | string | yes | bm25 \| tfidf \| hybrid. |
| `provider` | string | yes | offline \| openai \| azure. OpenAI-compatible servers such as Ollama use openai. |
| `prompt_version` | string | yes | Prompt in use. |
| `abstain_threshold` | number | yes | Calibrated confidence below which /ask abstains. |
| `audit_head_seq` | integer or null | no | Events in the audit log (None if disabled). |

#### `ErrorDetail`

| field | type | required | notes |
|---|---|---|---|
| `detail` | string | yes |  |

#### `InternalError`

| field | type | required | notes |
|---|---|---|---|
| `error` | string | yes | Always internal_error. Details are in the service log under request_id. |
| `request_id` | string | yes |  |

## 7. Examples

### Ask

```bash
curl -s https://<gateway>/ask -H 'content-type: application/json' -H 'X-Request-ID: case-42' \
  -d '{"question": "What is the late payment charge on my credit card?"}'
```

```json
{
  "request_id": "case-42",
  "answer": "If the minimum payment is not received by the payment due date, a late payment charge of 1% of the total outstanding balance is imposed, subject to a minimum of RM10 and a maximum of RM100. [credit-card#late-payment-charge] Any amount above the credit limit and any overdue amounts are added to the minimum payment. [credit-card#minimum-monthly-payment]",
  "outcome": "answered",
  "reason": "ok",
  "confidence": 0.61,
  "citations": [
    "credit-card#late-payment-charge",
    "credit-card#minimum-monthly-payment"
  ],
  "prompt_version": "answer_v1",
  "model": "offline-extractive-v1"
}
```

### Verify a document: auto-accepted

Request (base64 shortened):

```json
{
  "application": {
    "application_id": "APP-test-0004",
    "applicant_name": "Lakshmi a/p Muthu",
    "nric": "880226-01-6450",
    "application_date": "2026-10-31"
  },
  "document_base64": "JVBERi0xLjMKJenr8b8KMSAwIG9iago8PAovQ291..."
}
```

Response:

```json
{
  "request_id": "loan-7781-doc-1",
  "decision": "auto_accept",
  "reasons": [],
  "findings": [],
  "doc_type": "payslip",
  "fields": {
    "employer": "Syarikat Maju Jaya Sdn Bhd",
    "employee_name": "LAKSHMI MUTHU",
    "nric": "XXXXXX-XX-6450",
    "employee_no": "E31308",
    "pay_period": "2026-08",
    "pay_date": "2026-08-25",
    "basic_salary": 8250.0,
    "allowances": 450.0,
    "gross_pay": 8700.0,
    "epf_employee": 957.0,
    "socso_employee": 30.0,
    "eis_employee": 12.0,
    "pcb": 645.26,
    "total_deductions": 1644.26,
    "net_pay": 7055.74
  },
  "missing_fields": [],
  "anomaly_score": 1.402271,
  "anomaly_threshold": 3.823771,
  "sha256": "c70e0be572e14c77636e9c1164c116966480406a41a767dd3fc311be01b3c6d7",
  "rules_version": "doc-rules-v1",
  "extraction_method": "text_layer"
}
```

### Verify a document: rejected

The arithmetic-tampered sample (decision, reasons and findings only):

```json
{
  "decision": "reject",
  "reasons": [
    {
      "rule": "R01",
      "decision": "reject",
      "reason": "Figures on the document do not add up.",
      "codes": [
        "arithmetic_mismatch"
      ]
    },
    {
      "rule": "R14",
      "decision": "manual_review",
      "reason": "The name only partly matches the application.",
      "codes": [
        "name_partial_match"
      ]
    }
  ],
  "findings": [
    {
      "code": "arithmetic_mismatch",
      "source": "consistency",
      "severity": "high",
      "detail": "basic 14000.00 + allowances 1200.00 != gross 7100.00"
    },
    {
      "code": "name_partial_match",
      "source": "consistency",
      "severity": "medium",
      "detail": "name similarity 0.67"
    }
  ]
}
```

### Document too large: HTTP 413

```json
{
  "detail": "document larger than 5 MB"
}
```
