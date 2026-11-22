"""Generate docs/openapi.json and docs/INTEGRATION_SPEC.md from the running app's OpenAPI schema.

    python scripts/make_integration_spec.py           # rewrite both files
    python scripts/make_integration_spec.py --check   # exit 1 if they differ from the code

The endpoint and field tables come from the schema, so they cannot drift from the code
(tests/test_integration_spec.py). The prose around them is the contract the schema cannot express:
headers, retries, failure behaviour and data handling. Examples are real responses from the app.
"""
from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path

from .config import load_config, with_overrides

SPEC = Path("docs/INTEGRATION_SPEC.md")
OPENAPI = Path("docs/openapi.json")
SAMPLE = "genuine_payslip.pdf"


def _app(root: Path, audit_db: str):
    from fastapi.testclient import TestClient

    from .api import create_app
    cfg = with_overrides(load_config(root / "config.yaml"), audit_db=audit_db)
    return TestClient(create_app(cfg))


def _type(prop: dict, schemas: dict) -> str:
    if "$ref" in prop:
        return prop["$ref"].rsplit("/", 1)[1]
    if "anyOf" in prop:
        return " or ".join(_type(p, schemas) for p in prop["anyOf"])
    if prop.get("type") == "array":
        return f"list of {_type(prop.get('items', {}), schemas)}"
    t = prop.get("type", "any")
    return {"string": "string", "number": "number", "integer": "integer", "boolean": "boolean", "object": "object",
            "null": "null"}.get(t, t) + (f" ({prop['format']})" if "format" in prop else "")


def _constraints(prop: dict) -> str:
    keys = [("minLength", "min length {}"), ("maxLength", "max length {}"), ("pattern", "pattern `{}`")]
    return ", ".join(fmt.format(prop[k]) for k, fmt in keys if k in prop)


def _model_table(name: str, schemas: dict) -> str:
    s = schemas[name]
    req = set(s.get("required", []))
    rows = ["| field | type | required | notes |", "|---|---|---|---|"]
    for field, prop in s["properties"].items():
        notes = " ".join(x for x in (prop.get("description", ""), _constraints(prop)) if x).replace("|", "\\|")
        rows.append(f"| `{field}` | {_type(prop, schemas)} | {'yes' if field in req else 'no'} | {notes} |")
    return f"#### `{name}`\n\n" + "\n".join(rows)


def _endpoint(path: str, method: str, op: dict, schemas: dict) -> str:
    out = [f"### `{method.upper()} {path}`: {op.get('summary', '')}", ""]
    if op.get("description"):
        out += [" ".join(op["description"].split()), ""]
    body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
    if body:
        out += [f"Request body: `{_type(body, schemas)}`.", ""]
    out += ["| status | body | meaning |", "|---|---|---|"]
    for code, r in sorted(op["responses"].items()):
        ref = r.get("content", {}).get("application/json", {}).get("schema", {})
        out.append(f"| {code} | {('`' + _type(ref, schemas) + '`') if ref else '-'} | {r.get('description', '')} |")
    return "\n".join(out)


def _models_used(spec: dict) -> list[str]:
    order = ["AskRequest", "AskResponse", "VerifyRequest", "Application", "VerifyResponse", "Reason", "FindingOut",
             "Health", "ErrorDetail", "InternalError"]
    return [m for m in order if m in spec["components"]["schemas"]]


def _examples(client, root: Path) -> dict:
    samples = json.loads((root / "docs/samples/samples.json").read_text())
    sample = next(s for s in samples if s["file"] == SAMPLE)
    pdf = (root / "docs/samples" / SAMPLE).read_bytes()
    ask = client.post("/ask", json={"question": "What is the late payment charge on my credit card?"},
                      headers={"X-Request-ID": "case-42"}).json()
    req = {"application": sample["application"], "document_base64": base64.b64encode(pdf).decode()[:40] + "..."}
    ver = client.post("/documents/verify", headers={"X-Request-ID": "loan-7781-doc-1"},
                      json={"application": sample["application"], "document_base64": base64.b64encode(pdf).decode()}).json()
    tampered = next(s for s in samples if s["expected_decision"] == "reject")
    rej = client.post("/documents/verify", headers={"X-Request-ID": "loan-7790-doc-1"},
                      json={"application": tampered["application"],
                            "document_base64": base64.b64encode((root / "docs/samples" / tampered["file"]).read_bytes()).decode()}).json()
    big = client.post("/documents/verify", json={"application": sample["application"], "document_base64": "A" * (8 * 1024 * 1024)})
    return {"ask": ask, "verify_request": req, "verify": ver, "reject": {k: rej[k] for k in ("decision", "reasons", "findings")},
            "too_big": big.json(), "too_big_status": big.status_code}


def _j(obj) -> str:
    return "```json\n" + json.dumps(obj, indent=2, ensure_ascii=False) + "\n```"


def render(root: Path) -> tuple[str, str]:
    with tempfile.TemporaryDirectory() as tmp:
        client = _app(root, str(Path(tmp) / "audit.db"))
        spec = client.get("/openapi.json").json()
        ex = _examples(client, root)
        client.close()
    schemas = spec["components"]["schemas"]
    endpoints = "\n\n".join(_endpoint(p, m, op, schemas) for p, ops in spec["paths"].items() for m, op in ops.items())
    models = "\n\n".join(_model_table(m, schemas) for m in _models_used(spec))
    md = f"""# Integration specification

For a team connecting a channel (mobile app, contact-centre desktop, loan origination system) to the
`bankrag` service. API version **{spec['info']['version']}**. The endpoint and field tables, and the
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
| `document_base64` is not base64 | `422` with `{{"detail": "..."}}` | fix the request |
| Document larger than the limit | `413` with `{{"detail": "..."}}` | ask the applicant for a smaller file |
| Document unreadable, scanned, unsupported, or a check crashed | `200` with `manual_review` and rule `R20` | route to a person. This is fail-closed by design, not an error |
| Verification not installed in this image | `503` | configuration problem; alert the service owner |
| Unhandled error, **or the audit record could not be written** | `500` with `{{"error": "internal_error", "request_id": ...}}` | retry with backoff using the same `X-Request-ID`. No decision is ever returned without its audit record |

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

{endpoints}

## 6. Models

{models}

## 7. Examples

### Ask

```bash
curl -s https://<gateway>/ask -H 'content-type: application/json' -H 'X-Request-ID: case-42' \\
  -d '{{"question": "What is the late payment charge on my credit card?"}}'
```

{_j(ex["ask"])}

### Verify a document: auto-accepted

Request (base64 shortened):

{_j(ex["verify_request"])}

Response:

{_j(ex["verify"])}

### Verify a document: rejected

The arithmetic-tampered sample (decision, reasons and findings only):

{_j(ex["reject"])}

### Document too large: HTTP {ex["too_big_status"]}

{_j(ex["too_big"])}
"""
    return md, json.dumps(spec, indent=2, ensure_ascii=False) + "\n"


def sync(root: Path, check: bool = False) -> list[str]:
    md, oas = render(root)
    stale = []
    for rel, text in ((SPEC, md), (OPENAPI, oas)):
        path = root / rel
        if not path.exists() or path.read_text() != text:
            stale.append(str(rel))
            if not check:
                path.write_text(text)
    return stale
