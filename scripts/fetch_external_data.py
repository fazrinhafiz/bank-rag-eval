"""Download the two public datasets used for independent evaluation and convert them to JSONL.

    pip install -e ".[fetch]"          # pyarrow, only needed to read the parquet files
    python scripts/fetch_external_data.py

Outputs (committed, so CI and evals stay offline):
    data/external/banking77_test.jsonl            Banking77 test split, PolyAI, CC-BY-4.0
    data/external/deepset_prompt_injections.jsonl deepset/prompt-injections train+test, Hugging Face
The SHA-256 of each downloaded source file is printed and recorded in data/external/SOURCES.json.
"""
import csv
import hashlib
import io
import json
import ssl
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "external"
BANKING77 = "https://raw.githubusercontent.com/PolyAI-LDN/task-specific-datasets/master/banking_data/test.csv"
DEEPSET = "https://huggingface.co/datasets/deepset/prompt-injections/resolve/main/data/{}-00000-of-00001-{}.parquet"
DEEPSET_FILES = {"train": "9564e8b05b4757ab", "test": "701d16158af87368"}


def _ssl_context() -> ssl.SSLContext:
    """Verify TLS with certifi's CA bundle when available (python.org macOS builds ship without one)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60, context=_ssl_context()) as resp:
        return resp.read()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sources = {}

    raw = fetch(BANKING77)
    sources["banking77_test.csv"] = {"url": BANKING77, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    with (OUT / "banking77_test.jsonl").open("w") as f:
        for i, r in enumerate(rows):
            f.write(json.dumps({"id": f"b77-{i:04d}", "text": r["text"], "intent": r["category"]}) + "\n")
    print(f"banking77: {len(rows)} rows")

    import pyarrow.parquet as pq  # optional extra, only needed here

    n = 0
    with (OUT / "deepset_prompt_injections.jsonl").open("w") as f:
        for split, suffix in DEEPSET_FILES.items():
            url = DEEPSET.format(split, suffix)
            raw = fetch(url)
            sources[f"deepset_{split}.parquet"] = {"url": url, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
            table = pq.read_table(io.BytesIO(raw)).to_pylist()
            for i, r in enumerate(table):
                f.write(json.dumps({"id": f"dpi-{split}-{i:03d}", "text": r["text"], "is_injection": bool(r["label"]), "source_split": split}) + "\n")
                n += 1
    print(f"deepset prompt-injections: {n} rows")
    (OUT / "SOURCES.json").write_text(json.dumps(sources, indent=2) + "\n")
    for name, s in sources.items():
        print(f"  {name}: {s['bytes']} bytes sha256 {s['sha256'][:16]}")


if __name__ == "__main__":
    main()
