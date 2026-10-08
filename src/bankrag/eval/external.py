"""Independent evaluation on public datasets the system was never tuned on.

Banking77 (real customer queries):
    out-of-scope  - queries from intents the KB does not cover: did the assistant abstain?
    in-scope      - queries from covered intents: was an acceptable doc retrieved, and did it answer?
    false blocks  - share of all 3,080 benign queries refused by the injection rules
deepset/prompt-injections:
    detection rate on injections and false-positive rate on benign prompts, overall and for the
    English subset (language is guessed with a simple German-marker heuristic).
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from ..config import resolve
from ..guardrails import check_input
from ..pipeline import Pipeline
from .harness import file_sha, source_fingerprint

EXT = "data/external"
_GERMAN = re.compile(r"[äöüßÄÖÜ]|\b(der|und|ist|nicht|ich|Sie|wie|eine|einen|für|mit|auf|das|zu|den|dem|sind|oder|auch|sich|wird|werden|Ihre?|bitte|warum|welche)\b", re.I)


def is_german(text: str) -> bool:
    return len(_GERMAN.findall(text)) >= 2


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_intent_map(root: Path) -> dict[str, dict]:
    raw = yaml.safe_load((root / EXT / "banking77_intent_map.yaml").read_text())
    out = {i: {"label": "covered", "docs": v["docs"]} for i, v in raw["covered"].items()}
    out.update({i: {"label": "ambiguous", "docs": []} for i in raw["ambiguous"]})
    out.update({i: {"label": "not_covered", "docs": []} for i in raw["not_covered"]})
    return out


def run_banking77(pipe: Pipeline, root: Path) -> dict:
    rows = _jsonl(root / EXT / "banking77_test.jsonl")
    imap = load_intent_map(root)
    per_intent = defaultdict(lambda: {"n": 0, "answered": 0, "blocked": 0, "hit1": 0, "hit3": 0})
    answered_examples = defaultdict(list)
    pii_flagged = 0
    for r in rows:
        resp = pipe.ask(r["text"])
        info = imap[r["intent"]]
        s = per_intent[r["intent"]]
        s["n"] += 1
        answered = not (resp.abstained or resp.blocked)
        s["answered"] += answered
        s["blocked"] += resp.blocked
        pii_flagged += bool(resp.pii_types)
        if info["label"] == "covered" and not resp.blocked:
            docs = resp.retrieved_docs
            s["hit1"] += bool(docs) and docs[0] in info["docs"]
            s["hit3"] += any(d in info["docs"] for d in docs[:3])
        if info["label"] == "not_covered" and answered and len(answered_examples[r["intent"]]) < 2:
            answered_examples[r["intent"]].append({"id": r["id"], "text": r["text"], "top_chunk": resp.retrieved[0],
                                                   "confidence": round(resp.confidence, 4)})

    def total(label, key):
        return sum(v[key] for i, v in per_intent.items() if imap[i]["label"] == label)

    n_cov, n_oos = total("covered", "n"), total("not_covered", "n")
    intents = []
    for i, v in sorted(per_intent.items()):
        intents.append({"intent": i, "label": imap[i]["label"], "docs": imap[i]["docs"], **v})
    return {
        "n_queries": len(rows),
        "n_intents": {k: sum(1 for x in imap.values() if x["label"] == k) for k in ("covered", "ambiguous", "not_covered")},
        "n_by_label": {"covered": n_cov, "ambiguous": total("ambiguous", "n"), "not_covered": n_oos},
        "oos_abstention_rate": 1 - total("not_covered", "answered") / n_oos,
        "in_scope_answer_rate": total("covered", "answered") / n_cov,
        "in_scope_hit_at_1": total("covered", "hit1") / n_cov,
        "in_scope_hit_at_3": total("covered", "hit3") / n_cov,
        "false_block_rate": sum(v["blocked"] for v in per_intent.values()) / len(rows),
        "pii_flag_rate": pii_flagged / len(rows),
        "intents": intents,
        "answered_oos_examples": dict(answered_examples),
    }


def run_injections(root: Path) -> dict:
    rows = _jsonl(root / EXT / "deepset_prompt_injections.jsonl")
    groups = {"all": rows, "english": [r for r in rows if not is_german(r["text"])], "german": [r for r in rows if is_german(r["text"])]}
    out = {"n": len(rows), "n_german_guess": len(groups["german"])}
    for name, rs in groups.items():
        inj = [r for r in rs if r["is_injection"]]
        ben = [r for r in rs if not r["is_injection"]]
        caught = [r for r in inj if check_input(r["text"]).blocked]
        fps = [r for r in ben if check_input(r["text"]).blocked]
        out[name] = {"n_injection": len(inj), "n_benign": len(ben),
                     "detection_rate": len(caught) / len(inj) if inj else 0.0,
                     "false_positive_rate": len(fps) / len(ben) if ben else 0.0}
    rules = Counter(rule for r in rows if r["is_injection"] for rule in check_input(r["text"]).injection_rules)
    out["rule_hits"] = dict(rules.most_common())
    missed = [r for r in groups["english"] if r["is_injection"] and not check_input(r["text"]).blocked]
    out["missed_english_examples"] = [{"id": r["id"], "text": r["text"][:220]} for r in missed[:8]]
    fps = [r for r in rows if not r["is_injection"] and check_input(r["text"]).blocked]
    out["false_positive_examples"] = [{"id": r["id"], "text": r["text"][:220], "rules": check_input(r["text"]).injection_rules} for r in fps[:8]]
    return out


def run_external(cfg: dict) -> dict:
    root = Path(cfg["_root"])
    pipe = Pipeline(cfg)
    return {
        "metadata": {"source_fingerprint": source_fingerprint(root), "retrieval_mode": pipe.retriever.mode,
                     "abstain_threshold": pipe.threshold, "provider": pipe.generator.provider,
                     "banking77_sha": file_sha(root / EXT / "banking77_test.jsonl"),
                     "injections_sha": file_sha(root / EXT / "deepset_prompt_injections.jsonl")},
        "banking77": run_banking77(pipe, root),
        "injections": run_injections(root),
    }


def external_markdown(res: dict) -> str:
    b, j, m = res["banking77"], res["injections"], res["metadata"]
    pct = lambda x: f"{x:.1%}"
    lines = [
        "# External evaluation (independent public datasets, not gated)", "",
        "Generated by `python -m bankrag eval-external`. Nothing in the system was tuned on these datasets. "
        f"Retrieval `{m['retrieval_mode']}`, abstain threshold `{m['abstain_threshold']}` (calibrated on the golden dev split), "
        f"source fingerprint `{m['source_fingerprint']}`.", "",
        f"## Banking77: {b['n_queries']:,} real customer queries", "",
        f"Intent map: {b['n_intents']['covered']} covered intents ({b['n_by_label']['covered']} queries), "
        f"{b['n_intents']['ambiguous']} ambiguous ({b['n_by_label']['ambiguous']}, excluded), "
        f"{b['n_intents']['not_covered']} not covered ({b['n_by_label']['not_covered']}).", "",
        "| metric | value |", "|---|---|",
        f"| Out-of-scope abstention rate (not-covered queries refused) | **{pct(b['oos_abstention_rate'])}** |",
        f"| In-scope answer rate (covered queries answered) | {pct(b['in_scope_answer_rate'])} |",
        f"| In-scope retrieval hit@1 (acceptable doc ranked first) | {pct(b['in_scope_hit_at_1'])} |",
        f"| In-scope retrieval hit@3 | {pct(b['in_scope_hit_at_3'])} |",
        f"| False block rate (benign queries refused as injection, all {b['n_queries']:,}) | {pct(b['false_block_rate'])} |",
        f"| PII flagged (queries with something masked) | {pct(b['pii_flag_rate'])} |", "",
        "### Covered intents", "", "| intent | acceptable docs | n | answered | hit@1 | hit@3 |", "|---|---|---|---|---|---|",
    ]
    for i in (x for x in b["intents"] if x["label"] == "covered"):
        lines.append(f"| {i['intent']} | {', '.join(i['docs'])} | {i['n']} | {i['answered'] / i['n']:.0%} | {i['hit1'] / i['n']:.0%} | {i['hit3'] / i['n']:.0%} |")
    worst = sorted((x for x in b["intents"] if x["label"] == "not_covered"), key=lambda x: -x["answered"] / x["n"])
    lines += ["", "### Not-covered intents the assistant answers most often", "",
              "| intent | n | answered (should be 0%) |", "|---|---|---|"]
    lines += [f"| {i['intent']} | {i['n']} | {i['answered'] / i['n']:.0%} |" for i in worst[:12]]
    lines += ["", "Examples of out-of-scope queries that were answered:", ""]
    for intent in [w["intent"] for w in worst[:6]]:
        for e in b["answered_oos_examples"].get(intent, [])[:1]:
            lines.append(f"- `{intent}`: \"{e['text']}\" → `{e['top_chunk']}` (confidence {e['confidence']:.3f})")
    lines += ["", f"## deepset/prompt-injections: {j['n']} prompts", "",
              f"Language is guessed with a German-marker heuristic: {j['n_german_guess']} of {j['n']} look German. "
              "The injection rules are English-only by design, so the English subset is the fair measure.", "",
              "| subset | injections | detection rate | benign | false-positive rate |", "|---|---|---|---|---|"]
    for name in ("all", "english", "german"):
        s = j[name]
        lines.append(f"| {name} | {s['n_injection']} | **{pct(s['detection_rate'])}** | {s['n_benign']} | {pct(s['false_positive_rate'])} |")
    lines += ["", "Rules that fired on injections: " + (", ".join(f"`{k}` {v}" for k, v in j["rule_hits"].items()) or "none"), "",
              "Missed English injections (sample):", ""]
    lines += [f"- {e['id']}: \"{e['text']}\"" for e in j["missed_english_examples"]]
    if j["false_positive_examples"]:
        lines += ["", "False positives (benign prompts blocked):", ""]
        lines += [f"- {e['id']} {e['rules']}: \"{e['text']}\"" for e in j["false_positive_examples"]]
    lines += ["", "## Reading this", "",
              "These results are measured, not tuned. They are reported before any change to the rules or the threshold, "
              "so they show how the golden-set numbers carry over to data written by other people.", ""]
    return "\n".join(lines)
