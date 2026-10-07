"""Simulate baseline, stable and drifted traffic, then run the drift monitor on it.

    python scripts/simulate_drift.py            # writes reports/monitoring_report.md
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bankrag.config import load_config, resolve  # noqa: E402
from bankrag.monitoring.simulate import main  # noqa: E402

if __name__ == "__main__":
    text, stable, drifted = main()
    out = resolve(load_config(), "reports_dir") / "monitoring_report.md"
    out.write_text(text)
    print(text)
    print(f"control status: {stable.status}   drifted status: {drifted.status}\nwritten: {out}")
    sys.exit(0 if stable.status == "OK" and drifted.status == "ALERT" else 1)
