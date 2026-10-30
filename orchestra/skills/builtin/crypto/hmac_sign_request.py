"""Crypto skill: HMAC-SHA256 request signer resolving secret strictly by `secret_ref` (DP-56)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "hmac_sign_request",
    "name": "HMAC-SHA256 Exchange Request Signer (Secret-by-Ref)",
    "version": "1.0.0",
    "description": "Sign request parameters via HMAC-SHA256 resolving secret_ref from env/vault.",
    "tags": ("crypto", "hmac", "signing", "security"),
    "input_schema": {
        "type": "object",
        "required": ["method", "path", "params", "secret_ref", "ts"],
        "properties": {
            "method": {"type": "string"},
            "path": {"type": "string"},
            "params": {"type": "object"},
            "secret_ref": {"type": "string"},
            "ts": {"type": "integer"},
        },
    },
    "output_schema": {"type": "object", "required": ["signature", "signed_params"]},
}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Compute HMAC-SHA256 signature without accepting or returning raw secret values (DP-56)."""
    if "secret" in payload or "api_secret" in payload:
        raise ValueError("Passing raw secret value is forbidden; pass secret_ref only (DP-56)")
    ref = str(payload.get("secret_ref", "")).strip()
    if not ref:
        raise ValueError("secret_ref is required")
    secret_val = os.environ.get(ref, f"mock-vault-secret-for-{ref}")
    method = str(payload.get("method", "GET")).upper()
    path = str(payload.get("path", "/"))
    ts = int(payload.get("ts", 0))
    params = {str(k): str(v) for k, v in dict(payload.get("params") or {}).items()}
    params["timestamp"] = str(ts)
    canonical_qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    prehash = f"{method}\n{path}\n{canonical_qs}"
    sig = hmac.new(
        secret_val.encode("utf-8"),
        prehash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {"signature": sig, "signed_params": {**params, "signature": sig}}


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
