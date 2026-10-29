"""JSON bridge from the OpenCode runtime to Workers' classifier and pool.

Launched through Hermes' runtime command so it imports the same core classifier
the native agent uses, rather than the plugin's own Python environment.
"""

from __future__ import annotations

import json
import sys

from .credentials import rotate_credential, select_credential
from .provider_errors import classify, from_payload


def _emit(value, code: int = 0) -> int:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    return code


def _payload():
    try:
        value = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def main() -> int:
    action = str(sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if action == "select":
        provider = sys.argv[2] if len(sys.argv) > 2 else ""
        model = sys.argv[3] if len(sys.argv) > 3 else None
        value = select_credential(provider, model=model)
        return _emit(
            {"ok": True, **value} if value else {"ok": False, "error": "no_credential"},
            0 if value else 1,
        )
    if action == "handle":
        payload = _payload()
        if payload is None:
            return _emit({"ok": False, "error": "invalid_input"}, 2)
        error = from_payload(payload)
        decision = classify(error)
        result = {
            "ok": True,
            "status_code": error.status_code,
            "reason": decision.reason,
            "rotate": False,
            "should_fallback": decision.should_fallback,
        }
        if decision.should_rotate_credential:
            next_credential = rotate_credential(payload)
            if next_credential and next_credential.get("key"):
                result.update(
                    {
                        "rotate": True,
                        "key": next_credential["key"],
                        "credential_id": str(
                            next_credential.get("credential_id") or ""
                        ),
                    }
                )
        return _emit(result)
    return _emit({"ok": False, "error": "unknown_action"}, 2)


if __name__ == "__main__":
    raise SystemExit(main())
