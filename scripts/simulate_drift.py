"""Simulate baseline, stable and drifted traffic, then run the drift monitor on it.

    python scripts/simulate_drift.py            # writes reports/monitoring_report.md
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bankrag.config import load_config, resolve  # noqa: E402
from bankrag.monitoring.simulate import main  # noqa: E402

if __name__ == "__main__":
    text, stable, drifted = main()
    out = resolve(load_config(), "reports_dir") / "monitoring_report.md"
    out.write_text(text)
    summary = {}
    for name, rep in (("control", stable), ("drifted", drifted)):
        summary[name] = {"status": rep.status, "n_baseline": rep.n_baseline, "n_current": rep.n_current,
                         "psi": {f.name: round(f.psi, 3) for f in rep.features},
                         "ks_p": {f.name: float(f"{f.ks_p:.6g}") for f in rep.features if f.ks_p is not None},
                         "rates": {r.name: [round(r.baseline, 3), round(r.current, 3)] for r in rep.rates}}
    out.with_name("monitoring_summary.json").write_text(json.dumps(summary, indent=2))
    print(text)
    print(f"control status: {stable.status}   drifted status: {drifted.status}\nwritten: {out}")
    sys.exit(0 if stable.status == "OK" and drifted.status == "ALERT" else 1)
