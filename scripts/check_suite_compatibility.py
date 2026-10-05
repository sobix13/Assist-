#!/usr/bin/env python3
"""Read source versions and coordination hashes. Never imports companion code or touches their databases."""
import argparse
import hashlib
import json
from pathlib import Path


def inspect(paths):
    root = Path(__file__).resolve().parents[1]
    expected = hashlib.sha256((root / "ripcars_coordination.py").read_bytes()).hexdigest()
    report = {"ok": True, "protocol_sha256": expected, "targets": []}
    for name, path in paths.items():
        folder = Path(path)
        item = {"target": name, "path": str(folder), "ok": False}
        try:
            item["version"] = (folder / "VERSION").read_text().strip()
            item["protocol_sha256"] = hashlib.sha256((folder / "ripcars_coordination.py").read_bytes()).hexdigest()
            item["ok"] = item["protocol_sha256"] == expected
        except OSError as exc:
            item["error"] = type(exc).__name__
        report["targets"].append(item)
        report["ok"] = report["ok"] and item["ok"]
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("gate", "crew", "raffle", "verifier"):
        p.add_argument("--" + name, required=True)
    a = p.parse_args()
    result = inspect(vars(a))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
