# Use-case assessment: policy Q&A assistant

*The qualification done **before** building. It decides whether the use case deserves GenAI at all, how
risky it is, and what the simplest viable approach is. Bank Seri Andalas Berhad is fictional. The
business figures below are illustrative assumptions for the exercise, not measurements.*

## 1. Business need

Customers ask the contact centre, branches and social channels repetitive questions whose answers already
exist in published product terms and policies. Typical questions: "what is the late payment charge?",
"how do I undo the Kill Switch?", "how long do you keep my data?". Answers are scattered across documents
owned by different teams, and agents sometimes quote outdated or wrong figures.

**Need:** consistent, cited answers to policy questions, available 24/7, that defer to a human when the
documents don't answer the question.

## 2. Value hypothesis

| lever | hypothesis | how it would be measured |
|---|---|---|
| Containment | A share of policy questions can be answered without an agent | % of sessions resolved without hand-off; sampled QA |
| Consistency | Every answer cites the governing document, so fewer wrong quotes | citation accuracy in eval; complaint root-cause tags |
| Agent assist | The same engine can serve agents first (lower risk) | agent handle time; agent acceptance rate |
| Speed of change | Policy updates go live after re-ingestion and a passed quality gate | lead time from document change to production |

Before any build is approved, the value must be sized with real contact-centre data: volume by intent,
the share answerable from documents, and cost per contact. **Go/no-go depends on that sizing.**

## 3. Data

- **Source:** 7 published retail policy and product documents (markdown, owned by named teams, with effective dates).
- **Sensitivity:** the documents are public. Customer **questions** are not: they can contain NRICs, card numbers, phone numbers and emails, so the data-protection obligations sit on the *query and log* side.
- **Quality:** documents are short, well structured (one topic per `##` section) and consistent, with one ambiguity noted (DuitNow "unlimited free" vs a RM5,000 free-transfer limit). Content needs an owner and a review cycle.
- **Gaps:** home loans, fixed deposits, FX rates and branch hours are not covered. The assistant must say so, not improvise.

## 4. Risk assessment

| risk | example | inherent | key controls in this design |
|---|---|---|---|
| Wrong or invented answer | quotes a non-existent Gold card fee | High | grounding + citations, calibrated abstention, key-fact eval, quality gate |
| Unauthorised commitment | "confirm my fee is waived" | High | injection rules, prompt rule "never promise approvals", no write actions (no agent) |
| Privacy breach | NRIC stored in logs | High | input masking, salted hash + masked-only traces, output PII check, leak gate = 0 |
| Prompt injection / jailbreak | "ignore previous instructions" | Medium | rule-based detection before retrieval, refusal, injection gate |
| Unfair or inconsistent treatment | different answers to the same question | Medium | temperature 0, versioned prompt, deterministic retrieval |
| Out-of-scope drift | customers start asking about mortgages | Medium | PSI / abstention monitoring with alerts |
| Third-party model risk | provider changes model behaviour | Medium | pinned model / deployment, re-run gate on any change |

**Proposed risk tier: Medium-High (customer-facing, informational only).** The output informs customers
but **executes no transactions and makes no credit decisions**. That's what keeps it out of the highest
tier, and it's why "no agent / no tools" is a hard requirement, not just a simplification. Recommended
rollout: agent-assist first, then customer-facing for a limited set of intents.

## 5. Feasibility

- **Technical:** high. Small, clean corpus. A deterministic baseline reaches 100% hit@3 on the golden set with no ML training.
- **Hard part:** answering *only* when the documents actually answer the question. Retrieval-confidence abstention catches off-topic questions but not near-miss product questions (see MODEL_CARD).
- **Dependencies:** an approved LLM endpoint (e.g. a private Azure OpenAI deployment) for production fluency; document owners; contact-centre hand-off.

## 6. Simplest approach considered

| option | what it is | fits? | why / why not |
|---|---|---|---|
| A. Curated FAQ + keyword search | Hand-written Q&A pairs, search returns the FAQ | Partly | Cheapest and lowest risk; but FAQs go stale, don't cover long-tail phrasing, and duplicate the source of truth |
| **B. Retrieval + grounded answer (RAG)** | Retrieve the governing section, answer only from it, cite it, abstain otherwise | **Yes** | Uses the documents as the single source of truth; citations make every answer auditable; can start with a no-LLM extractive baseline |
| C. Agent with tools | LLM plans and calls tools (account lookup, fee reversal…) | **No** | The need is *informational*. Tools add action risk (unauthorised commitments, wrong account), a larger attack surface for injection, non-deterministic multi-step paths that are hard to test, and higher cost, for no added value here |

**Decision: B, built up from the simplest working version.** Start with lexical retrieval and an
extractive baseline (no LLM), measure it, then add an LLM only where the evaluation shows a measurable
gain. The benchmark already applied this principle once: the hybrid retriever was implemented, measured
and **not** adopted because it gave no gain over a single retriever.

## 7. Effort (indicative)

| phase | scope | effort |
|---|---|---|
| Discovery and sizing | intents, volumes, document owners, risk tiering | 1–2 weeks |
| Baseline + evaluation harness | this repository | 2–3 weeks |
| LLM integration + independent validation | private endpoint, red-team set, judge validation, human review sample | 3–4 weeks |
| Pilot (agent-assist) | limited intents, monitoring, feedback loop | 4–6 weeks |

## 8. Go / no-go

**Go: conditional, for an agent-assist pilot.**

Conditions before customer-facing launch:
1. The value is sized with real contact-centre data and the business case clears the bank's threshold.
2. The test-split quality gate passes with the production LLM, and **abstention recall on an expanded, independently written unanswerable set reaches an agreed target** (the offline baseline's 50% is not enough).
3. An independent red-team exercise finds no PII leak or successful injection.
4. Model risk sign-off (MODEL_CARD.md) and a named business owner, model owner and document owners.
5. Monitoring and a human hand-off path are live.

**No-go triggers:** any PII leak in validation, any unauthorised commitment, or a use case that grows to
need account-specific actions. That last one would need a new assessment at a higher risk tier.
