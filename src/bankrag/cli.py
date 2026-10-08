"""python -m bankrag {ask,eval,benchmark,calibrate,monitor}"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_config, resolve


def cmd_ask(args) -> int:
    from .pipeline import Pipeline
    from .tracing import Tracer

    cfg = load_config(args.config)
    pipe = Pipeline(cfg, tracer=Tracer(None if args.no_trace else resolve(cfg, "trace_path")))
    resp = pipe.ask(args.question)
    if args.json:
        print(json.dumps(resp.trace, indent=2))
        return 0
    print(resp.answer)
    print(f"\n  reason={resp.reason}  confidence={resp.confidence:.3f}  threshold={pipe.threshold:.3f}  "
          f"provider={pipe.generator.provider}  prompt={pipe.prompt.version}")
    if resp.pii_types:
        print(f"  PII masked before retrieval: {', '.join(resp.pii_types)}  ->  {resp.masked_query}")
    if resp.retrieved:
        print(f"  retrieved: {', '.join(resp.retrieved)}")
    return 0


def _judge(cfg, enabled: bool):
    if not enabled:
        return None
    if cfg["generation"]["provider"] == "offline":
        raise SystemExit("--judge needs a real LLM: set BANKRAG_PROVIDER=openai|azure (see README)")
    from .eval.judge import LLMJudge
    from .generators import make_generator
    from .prompts import Prompt
    return LLMJudge(make_generator(cfg, Prompt.load(resolve(cfg, cfg["generation"]["prompt"]))))


def cmd_eval(args) -> int:
    import yaml

    from .eval.harness import run_eval
    from .eval.metrics import check_gates
    from .eval.report import fmt, write_reports, _kind

    cfg = load_config(args.config)
    run = run_eval(cfg, args.split, judge=_judge(cfg, args.judge))
    gates = check_gates(run.metrics, yaml.safe_load(resolve(cfg, "thresholds").read_text())["gates"])
    stem = "eval_report" if args.split == "test" else f"eval_report_{args.split}"
    md, page = write_reports(run, gates, resolve(cfg, "reports_dir"), stem)
    (resolve(cfg, "reports_dir") / f"{stem}.json").write_text(json.dumps(run.to_json(), indent=2))

    print(f"split={args.split} items={run.metrics['n_items']} mode={run.metadata['retrieval_mode']} "
          f"threshold={run.metadata['abstain_threshold']} provider={run.metadata['provider']}")
    for g in gates:
        print(f"  [{'PASS' if g.passed else 'FAIL'}] {g.metric:<22} {fmt(g.value, _kind(g.metric)):>9}  (gate {g.op} {fmt(g.threshold, _kind(g.metric))})")
    failed = [r for r in run.results if not r.passed]
    print(f"  {len(failed)} failed item(s): {', '.join(r.id for r in failed) or 'none'}")
    print(f"  reports: {md}  {page}")
    if args.split != "test":
        print("  (gates are only enforced on the test split)")
        return 0
    if not all(g.passed for g in gates):
        print("QUALITY GATE FAILED")
        return 1
    print("QUALITY GATE PASSED")
    return 0


def cmd_calibrate(args) -> int:
    from .config import write_abstain_threshold
    from .eval.calibrate import calibrate
    from .eval.harness import load_golden
    from .pipeline import Pipeline

    cfg = load_config(args.config)
    pipe = Pipeline(cfg)
    cal = calibrate(load_golden(resolve(cfg, "golden_set"), "dev"), pipe.retriever, pipe.top_k)
    lines = ["# Abstain-threshold calibration (dev split only)", "",
             f"Retrieval mode `{pipe.retriever.mode}`. {cal.n_items} dev items ({cal.n_should_abstain} should abstain; "
             "injection items excluded because they are blocked before retrieval). The test split is not read.", "",
             f"**Chosen threshold: {cal.threshold:.4f}** - dev abstention P={cal.best.precision:.3f} R={cal.best.recall:.3f} "
             f"F1={cal.best.f1:.3f}, answer rate on answerable dev items {cal.best.answer_rate:.1%}.", "",
             "Ties on F1 are broken by taking the middle of the tied range (maximum margin).", "",
             "| threshold | precision | recall | F1 | answer rate |", "|---|---|---|---|---|"]
    lines += [f"| {r.threshold:.4f} | {r.precision:.3f} | {r.recall:.3f} | {r.f1:.3f} | {r.answer_rate:.1%} |" for r in cal.rows]
    lines += ["", "## Dev confidences", "", "| item | confidence | should abstain |", "|---|---|---|"]
    lines += [f"| {i} | {s:.4f} | {'yes' if a else 'no'} |" for i, s, a in sorted(cal.scores, key=lambda x: x[1])]
    out = resolve(cfg, "reports_dir") / "calibration.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    if not args.dry_run:
        write_abstain_threshold(cfg["_path"], cal.threshold)
    print(f"threshold={cal.threshold:.4f}  dev P={cal.best.precision:.3f} R={cal.best.recall:.3f} F1={cal.best.f1:.3f}"
          f"  {'(dry run, config unchanged)' if args.dry_run else '-> written to ' + cfg['_path']}\n  sweep: {out}")
    return 0


def cmd_benchmark(args) -> int:
    from .eval.benchmark import benchmark_markdown, run_benchmark, select_mode

    cfg = load_config(args.config)
    rows = run_benchmark(cfg)
    text = benchmark_markdown(rows, cfg)
    out = resolve(cfg, "reports_dir") / "benchmark.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    (out.parent / "benchmark.json").write_text(json.dumps({"selected_on_dev": select_mode(rows), "rows": rows}, indent=2))
    print(text)
    print(f"selected on dev: {select_mode(rows)}   (configured: {cfg['retrieval']['mode']})\nwritten: {out}")
    return 0


def cmd_monitor(args) -> int:
    from .monitoring.drift import monitor, report_markdown
    from .tracing import read_traces

    cfg = load_config(args.config)
    if args.baseline and args.current:
        base, cur = read_traces(args.baseline), read_traces(args.current)
        title = f"{args.baseline} vs {args.current}"
    else:
        traces = sorted(read_traces(args.traces or resolve(cfg, "trace_path")), key=lambda t: t["ts"])
        if len(traces) < 20:
            raise SystemExit(f"need at least 20 traces to compare windows, found {len(traces)}")
        half = len(traces) // 2
        base, cur = traces[:half], traces[half:]
        title = f"first {len(base)} vs last {len(cur)} traces"
    rep = monitor(base, cur)
    text = report_markdown(rep, title)
    print(text)
    if args.out:
        Path(args.out).write_text(text)
    return 2 if (args.fail_on_alert and rep.status == "ALERT") else 0


def cmd_eval_external(args) -> int:
    from .eval.external import external_markdown, run_external

    cfg = load_config(args.config)
    res = run_external(cfg)
    out = resolve(cfg, "reports_dir")
    out.mkdir(parents=True, exist_ok=True)
    (out / "external_eval.json").write_text(json.dumps(res, indent=2))
    text = external_markdown(res)
    (out / "external_eval.md").write_text(text)
    print(text)
    print(f"written: {out / 'external_eval.md'}  (not gated)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bankrag", description=__doc__)
    parser.add_argument("--config", help="path to config.yaml (default: repo config or $BANKRAG_CONFIG)")
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="answer one question")
    ask.add_argument("question")
    ask.add_argument("--json", action="store_true", help="print the trace record instead of the answer")
    ask.add_argument("--no-trace", action="store_true", help="do not append to the trace file")
    ask.set_defaults(func=cmd_ask)

    ev = sub.add_parser("eval", help="run the golden set, write reports, enforce quality gates (exit 1 on failure)")
    ev.add_argument("--split", choices=["dev", "test", "all"], default="test")
    ev.add_argument("--judge", action="store_true", help="add LLM-as-judge faithfulness (needs a real LLM provider)")
    ev.set_defaults(func=cmd_eval)

    cal = sub.add_parser("calibrate", help="sweep the abstain threshold on the dev split and write it to config")
    cal.add_argument("--dry-run", action="store_true", help="report only, do not modify config.yaml")
    cal.set_defaults(func=cmd_calibrate)

    bench = sub.add_parser("benchmark", help="compare bm25 vs tfidf vs hybrid")
    bench.set_defaults(func=cmd_benchmark)

    ext = sub.add_parser("eval-external", help="independent eval on Banking77 + deepset prompt injections (not gated)")
    ext.set_defaults(func=cmd_eval_external)

    mon = sub.add_parser("monitor", help="PSI + KS drift check between two windows of traces")
    mon.add_argument("--baseline", help="baseline traces JSONL")
    mon.add_argument("--current", help="current traces JSONL")
    mon.add_argument("--traces", help="single trace file to split in half by time (default: config trace_path)")
    mon.add_argument("--out", help="write the markdown report here")
    mon.add_argument("--fail-on-alert", action="store_true", help="exit 2 if status is ALERT (for scheduled jobs)")
    mon.set_defaults(func=cmd_monitor)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
