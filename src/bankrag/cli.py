"""python -m bankrag {ask,eval,benchmark,calibrate,monitor}"""
from __future__ import annotations

import argparse
import json
import sys

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bankrag", description=__doc__)
    parser.add_argument("--config", help="path to config.yaml (default: repo config or $BANKRAG_CONFIG)")
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="answer one question")
    ask.add_argument("question")
    ask.add_argument("--json", action="store_true", help="print the trace record instead of the answer")
    ask.add_argument("--no-trace", action="store_true", help="do not append to the trace file")
    ask.set_defaults(func=cmd_ask)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
