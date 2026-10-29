"""Small bridge to Hermes' main-agent fallback provider chain.

Workers are separate CLI processes, so Hermes cannot switch their live client
in-place.  This module only reads the same configured chain.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .credentials import pool_provider


logger = logging.getLogger(__name__)


def fallback_chain_status() -> dict[str, Any]:
    """Read Hermes' fallback chain and report whether the read itself worked.

    ``readable`` is what separates "the operator configured no fallbacks" from
    "Hermes moved the API this plugin reads".  On the board both look identical:
    fewer model attempts, no error anywhere.
    """

    try:
        from hermes_cli.config import load_config as load_hermes_config
        from hermes_cli.fallback_config import get_fallback_chain

        raw_entries = get_fallback_chain(load_hermes_config())
    except Exception as exc:
        logger.warning(
            "workers: Hermes fallback chain unreadable (%s); running single-model", exc
        )
        return {
            "readable": False,
            "entries": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "readable": True,
        "entries": raw_entries if isinstance(raw_entries, list) else [],
        "error": "",
    }


def hermes_fallback_entries() -> list[dict[str, Any]]:
    """Return fresh copies of Hermes' top-level fallback entries.

    The plugin never keeps a duplicate fallback configuration.  If the
    Hermes config is temporarily unavailable, an empty chain preserves the
    existing single-model worker behavior.
    """

    result: list[dict[str, Any]] = []
    for raw in fallback_chain_status()["entries"]:
        if not isinstance(raw, dict):
            continue
        provider = pool_provider(raw.get("provider"))
        model = str(raw.get("model") or "").strip()
        if not provider or not model:
            continue
        entry: dict[str, Any] = {"provider": provider, "model": model}
        for key in (
            "base_url",
            "api_key",
            "key_env",
            "api_key_env",
            "api_mode",
            "reasoning_echo",
            "extra_headers",
        ):
            if key in raw and raw[key] not in (None, ""):
                entry[key] = raw[key]
        result.append(entry)
    return result


def fallback_entries_for(primary_provider: str, primary_model: str) -> list[dict[str, Any]]:
    """Return the configured chain with the primary route removed/normalized."""

    primary = (
        pool_provider(primary_provider),
        str(primary_model or "").strip(),
        "",
    )
    result: list[dict[str, Any]] = []
    seen = {primary}
    for entry in hermes_fallback_entries():
        identity = (
            pool_provider(entry.get("provider")),
            str(entry.get("model") or "").strip(),
            str(entry.get("base_url") or "").strip().rstrip("/"),
        )
        if not identity[0] or not identity[1] or identity in seen:
            continue
        seen.add(identity)
        result.append(entry)
    return result


def result_event(output_tail: str) -> dict[str, Any]:
    """Return the last structured CLI result event, if one was emitted."""
    event: dict[str, Any] = {}
    for line in str(output_tail or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("type") == "result":
            event = value
    return event
