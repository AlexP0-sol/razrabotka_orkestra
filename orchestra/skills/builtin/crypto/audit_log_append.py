"""Crypto skill: append-only hash-chained order & reconciliation audit log (TR-04, DP-32)."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "audit_log_append",
    "name": "Hash-Chained Order Audit Log Appender",
    "version": "1.0.0",
    "description": "Append entry to hash-chained JSONL audit log returning {seq, sha256}.",
    "tags": ("crypto", "audit", "hashchain", "compliance"),
    "input_schema": {
        "type": "object",
        "required": ["entry"],
        "properties": {"entry": {"type": "object"}, "log_path": {"type": "string"}},
    },
    "output_schema": {"type": "object", "required": ["seq", "sha256", "prev_sha256"]},
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Append `entry` to optional `log_path` maintaining SHA-256 chain integrity."""
    if "entry" not in payload or not isinstance(payload["entry"], dict):
        raise ValueError("entry object is required")
    entry = dict(payload["entry"])
    prev_hash = "0" * 64
    seq = 1
    log_path_raw = payload.get("log_path")

    if log_path_raw:
        log_path = Path(str(log_path_raw))
        if log_path.is_file():
            lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if lines:
                last = json.loads(lines[-1])
                seq = int(last["seq"]) + 1
                prev_hash = str(last["sha256"])

    canonical = json.dumps(
        {"seq": seq, "prev_sha256": prev_hash, "entry": entry},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    record = {"seq": seq, "prev_sha256": prev_hash, "sha256": digest, "entry": entry}

    if log_path_raw:
        log_path = Path(str(log_path_raw))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {"seq": seq, "sha256": digest, "prev_sha256": prev_hash}


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
