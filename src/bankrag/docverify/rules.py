"""A small, explicit rule engine: findings in, decision out.

Conditions:  {any: [codes]}  at least one of the codes was found
             {all: [codes]}  every one of the codes was found
             {count: {source: forensics, at_least: 2}}  that many findings from one source
Decisions are ordered reject > manual_review > auto_accept; the most severe match wins and every matching
rule is reported, so a reviewer sees all the reasons, not just the first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .checks import CODES, Finding

DECISIONS = ("reject", "manual_review", "auto_accept")      # most severe first
SOURCES = {v[0] for v in CODES.values()}


class RuleError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    id: str
    when: dict
    decision: str
    reason: str

    def matches(self, findings: list[Finding]) -> bool:
        codes = {f.code for f in findings}
        if "any" in self.when:
            return bool(codes & set(self.when["any"]))
        if "all" in self.when:
            return set(self.when["all"]) <= codes
        c = self.when["count"]
        return sum(1 for f in findings if f.source == c["source"]) >= int(c["at_least"])


@dataclass
class Decision:
    decision: str
    reasons: list[dict] = field(default_factory=list)   # [{rule, decision, reason, codes}]

    @property
    def automated(self) -> bool:
        return self.decision != "manual_review"


@dataclass
class RuleSet:
    version: str
    default: str
    settings: dict
    rules: list[Rule]

    @classmethod
    def load(cls, path: str | Path) -> "RuleSet":
        data = yaml.safe_load(Path(path).read_text())
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "RuleSet":
        for key in ("version", "default", "rules"):
            if key not in data:
                raise RuleError(f"rules file needs {key!r}")
        if data["default"] not in DECISIONS:
            raise RuleError(f"unknown default decision {data['default']!r}")
        rules, ids = [], set()
        for r in data["rules"]:
            if r.get("id") in ids:
                raise RuleError(f"duplicate rule id {r.get('id')!r}")
            ids.add(r.get("id"))
            if r.get("decision") not in DECISIONS:
                raise RuleError(f"{r.get('id')}: unknown decision {r.get('decision')!r}")
            when = r.get("when") or {}
            if len(when) != 1 or next(iter(when)) not in ("any", "all", "count"):
                raise RuleError(f"{r['id']}: 'when' needs exactly one of any / all / count")
            if "count" in when:
                if when["count"].get("source") not in SOURCES:
                    raise RuleError(f"{r['id']}: unknown source {when['count'].get('source')!r}")
            else:
                unknown = [c for c in next(iter(when.values())) if c not in CODES]
                if unknown:
                    raise RuleError(f"{r['id']}: unknown finding code(s) {unknown}")
            rules.append(Rule(r["id"], when, r["decision"], r.get("reason", "")))
        return cls(str(data["version"]), data["default"], data.get("settings", {}), rules)

    def referenced_codes(self) -> set[str]:
        out = set()
        for r in self.rules:
            out |= set(r.when.get("any", [])) | set(r.when.get("all", []))
        return out

    def decide(self, findings: list[Finding]) -> Decision:
        hits = [r for r in self.rules if r.matches(findings)]
        if not hits:
            return Decision(self.default)
        decision = min((r.decision for r in hits), key=DECISIONS.index)
        reasons = []
        for r in sorted(hits, key=lambda r: (DECISIONS.index(r.decision), r.id)):
            codes = sorted({f.code for f in findings} & (set(r.when.get("any", [])) | set(r.when.get("all", []))))
            reasons.append({"rule": r.id, "decision": r.decision, "reason": r.reason, "codes": codes})
        return Decision(decision, reasons)
