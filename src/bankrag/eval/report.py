"""Markdown + self-contained HTML eval reports. Failures are listed in full - never summarised away."""
from __future__ import annotations

import html
from pathlib import Path

from .harness import EvalRun
from .metrics import GateResult

METRIC_GROUPS = {
    "Overall": [("task_success", "task success (item fully correct)", "pct"), ("n_items", "items", "f0")],
    "Retrieval (answerable + PII items)": [
        ("hit_at_1", "hit@1", "pct"), ("hit_at_3", "hit@3", "pct"), ("mrr", "MRR@3", "f3"),
    ],
    "Answer quality (answerable + PII items)": [
        ("answer_rate", "answer rate (not abstained)", "pct"),
        ("key_fact_recall", "key-fact recall (abstain = 0)", "pct"),
        ("answer_accuracy", "strict answer accuracy (all facts + right citation)", "pct"),
        ("citation_accuracy", "citation accuracy (cites expected doc, answered items)", "pct"),
        ("faithfulness", "faithfulness, lexical (answered items)", "pct"),
        ("judge_faithfulness", "faithfulness, LLM-as-judge", "pct"),
    ],
    "Safety": [
        ("abstention_precision", "abstention precision (non-injection items)", "pct"),
        ("abstention_recall", "abstention recall on unanswerable", "pct"),
        ("abstention_f1", "abstention F1", "f3"),
        ("confidence_auroc", "confidence AUROC (answerable vs unanswerable)", "f3"),
        ("injection_block_rate", "injection block rate", "pct"),
        ("false_block_rate", "false block rate (legit questions blocked)", "pct"),
        ("pii_mask_rate", "PII masked before retrieval", "pct"),
        ("pii_leak_rate", "PII leak rate (answer or trace)", "pct"),
    ],
    "Operational (wall-clock, varies by machine)": [
        ("latency_p50_ms", "end-to-end latency p50", "ms"), ("latency_p95_ms", "end-to-end latency p95", "ms"),
        ("mean_tokens", "mean tokens / request (estimated for offline)", "f0"), ("mean_cost_usd", "mean cost / request", "usd"),
    ],
}


def fmt(value, kind: str) -> str:
    if value is None:
        return "n/a"
    return {"pct": f"{value:.1%}", "f3": f"{value:.3f}", "f0": f"{value:.0f}", "ms": f"{value:.2f} ms",
            "usd": f"${value:.6f}"}.get(kind, str(value))


def _kind(metric: str) -> str:
    for rows in METRIC_GROUPS.values():
        for key, _, kind in rows:
            if key == metric:
                return kind
    return "f3"


def _excerpt(text: str, n: int = 260) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _intro(run: EvalRun) -> list[str]:
    n = run.metrics["n_items"]
    lines = [f"Split **{run.split}**, {n} items. "]
    if run.split == "test":
        lines.append("The abstain threshold was calibrated on the **dev** split only; no test item influenced it. ")
    else:
        lines.append("⚠️ This is not the test split, so the results are not a gate decision. ")
    lines.append(f"With {n} items, one item moves a whole-split rate by {1 / n:.1%}; treat differences of a few points as noise.")
    return lines


def render_markdown(run: EvalRun, gates: list[GateResult]) -> str:
    ok = all(g.passed for g in gates)
    md = ["# bankrag evaluation report", "",
          f"**Quality gate: {'PASS ✅' if ok else 'FAIL ❌'}** - {sum(g.passed for g in gates)}/{len(gates)} gates passed", "",
          "".join(_intro(run)), "", "## Run metadata", "", "| key | value |", "|---|---|"]
    md += [f"| {k} | `{v}` |" for k, v in run.metadata.items()]
    md += ["", "## Quality gates", "", "| metric | value | gate | status | rationale |", "|---|---|---|---|---|"]
    for g in gates:
        md.append(f"| {g.metric} | {fmt(g.value, _kind(g.metric))} | {g.op} {fmt(g.threshold, _kind(g.metric))} | "
                  f"{'pass' if g.passed else '**FAIL**'} | {g.why} |")
    for group, rows in METRIC_GROUPS.items():
        md += ["", f"## {group}", "", "| metric | value |", "|---|---|"]
        md += [f"| {label} | {fmt(run.metrics.get(key), kind)} |" for key, label, kind in rows if key in run.metrics]
    md += ["", "## Results by category", "", "| category | passed | total |", "|---|---|---|"]
    md += [f"| {c} | {v['passed']} | {v['n']} |" for c, v in run.categories.items()]
    failed = [r for r in run.results if not r.passed]
    md += ["", f"## Error analysis - {len(failed)} failed item(s)", ""]
    if not failed:
        md.append("No failures on this split.")
    for r in failed:
        md += [f"### {r.id} ({r.category})", "",
               f"- **question (masked):** {r.masked_question}",
               f"- **outcome:** reason=`{r.reason}`, confidence={r.confidence:.3f}, top chunks: {', '.join(r.retrieved) or 'none'}",
               *[f"- **why it failed:** {f}" for f in r.failures],
               f"- **answer:** {_excerpt(r.answer)}", ""]
    md += ["## Known weaknesses", "", "See `docs/MODEL_CARD.md` (Limitations) for the systematic weaknesses behind these failures.", ""]
    return "\n".join(md)


CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d2433;--muted:#5b6475;--line:#e3e5ea;--ok:#127a46;--okbg:#e6f4ec;--bad:#b42318;--badbg:#fdecea;--accent:#1f4e8c}
@media (prefers-color-scheme:dark){:root{--bg:#12151b;--card:#1a1f27;--ink:#e6e8ec;--muted:#9aa3b2;--line:#2a313c;--ok:#5fd394;--okbg:#14301f;--bad:#ff8a80;--badbg:#3a1a18;--accent:#8ab4f8}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1000px;margin:0 auto;padding:32px 16px 64px}h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 12px}
.sub{color:var(--muted);margin:0 0 20px}.verdict{display:inline-block;padding:6px 12px;border-radius:8px;font-weight:600}
.pass{background:var(--okbg);color:var(--ok)}.fail{background:var(--badbg);color:var(--bad)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:4px 0;overflow-x:auto}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:8px 14px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600}tr:last-child td{border-bottom:0}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}code{font:13px ui-monospace,Menlo,monospace}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
.fail-item{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--bad);border-radius:8px;padding:12px 16px;margin:0 0 12px}
.fail-item h3{margin:0 0 6px;font-size:15px}.fail-item p{margin:4px 0}.reason{color:var(--bad)}.muted{color:var(--muted)}
"""


def render_html(run: EvalRun, gates: list[GateResult]) -> str:
    e = html.escape
    ok = all(g.passed for g in gates)
    intro = "".join(_intro(run)).replace("**", "")
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>bankrag eval report</title><style>{CSS}</style></head><body><main>",
             "<h1>bankrag evaluation report</h1>",
             f"<p class='sub'>{e(intro)}</p>",
             f"<span class='verdict {'pass' if ok else 'fail'}'>Quality gate: {'PASS' if ok else 'FAIL'} - "
             f"{sum(g.passed for g in gates)}/{len(gates)} gates passed</span>",
             "<h2>Quality gates</h2><div class='card'><table><tr><th>metric</th><th>value</th><th>gate</th><th>status</th><th>rationale</th></tr>"]
    for g in gates:
        k = _kind(g.metric)
        parts.append(f"<tr><td><code>{e(g.metric)}</code></td><td class='num'>{fmt(g.value, k)}</td><td class='num'>{g.op} {fmt(g.threshold, k)}</td>"
                     f"<td><span class='verdict {'pass' if g.passed else 'fail'}'>{'pass' if g.passed else 'FAIL'}</span></td><td>{e(g.why)}</td></tr>")
    parts.append("</table></div><div class='grid'>")
    for group, rows in METRIC_GROUPS.items():
        parts.append(f"<div><h2>{e(group)}</h2><div class='card'><table>")
        parts += [f"<tr><td>{e(label)}</td><td class='num'>{fmt(run.metrics.get(key), kind)}</td></tr>" for key, label, kind in rows if key in run.metrics]
        parts.append("</table></div></div>")
    parts.append("<div><h2>Results by category</h2><div class='card'><table><tr><th>category</th><th>passed</th><th>total</th></tr>")
    parts += [f"<tr><td>{e(c)}</td><td class='num'>{v['passed']}</td><td class='num'>{v['n']}</td></tr>" for c, v in run.categories.items()]
    parts.append("</table></div></div></div>")
    failed = [r for r in run.results if not r.passed]
    parts.append(f"<h2>Error analysis - {len(failed)} failed item(s)</h2>")
    if not failed:
        parts.append("<p>No failures on this split.</p>")
    for r in failed:
        parts.append(f"<div class='fail-item'><h3>{e(r.id)} <span class='muted'>({e(r.category)})</span></h3>"
                     f"<p><b>Question (masked):</b> {e(r.masked_question)}</p>"
                     f"<p class='muted'>reason=<code>{e(r.reason)}</code> · confidence {r.confidence:.3f} · top chunks: {e(', '.join(r.retrieved) or 'none')}</p>"
                     + "".join(f"<p class='reason'>✗ {e(f)}</p>" for f in r.failures)
                     + f"<p><b>Answer:</b> {e(_excerpt(r.answer))}</p></div>")
    parts.append("<h2>Run metadata</h2><div class='card'><table>")
    parts += [f"<tr><td>{e(k)}</td><td><code>{e(str(v))}</code></td></tr>" for k, v in run.metadata.items()]
    parts.append("</table></div><p class='muted'>Known weaknesses: see docs/MODEL_CARD.md (Limitations).</p></main></body></html>")
    return "".join(parts)


def write_reports(run: EvalRun, gates: list[GateResult], reports_dir: str | Path, stem: str = "eval_report") -> tuple[Path, Path]:
    out = Path(reports_dir)
    out.mkdir(parents=True, exist_ok=True)
    md, page = out / f"{stem}.md", out / f"{stem}.html"
    md.write_text(render_markdown(run, gates))
    page.write_text(render_html(run, gates))
    return md, page
