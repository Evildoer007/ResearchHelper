"""Small, internal-only diagnostics for a single formal OptionHelper quote."""

from __future__ import annotations

import os
import re


_SECRET_NAMES = (
    "DEEPSEEK_API_KEY", "TAVILY_API_KEY", "IFIND_USERNAME", "IFIND_PASSWORD",
    "IFIND_ACCESS_TOKEN", "OPTIONHELPER_API_KEY", "OPENAI_API_KEY",
)


def safe_error_tail(stderr: str, *, limit: int = 8000) -> str:
    """Keep useful worker progress/errors without persisting configured credentials."""
    tail = str(stderr or "")[-limit:]
    try:
        from core import config
        configured_secrets = [str(getattr(config, name, "") or "") for name in _SECRET_NAMES]
    except ImportError:
        configured_secrets = []
    for secret in configured_secrets:
        if len(secret) >= 4:
            tail = tail.replace(secret, "[REDACTED]")
    for name in _SECRET_NAMES:
        secret = os.environ.get(name, "")
        if secret and len(secret) >= 4:
            tail = tail.replace(secret, "[REDACTED]")
    tail = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", tail)
    tail = re.sub(
        r"(?i)(\b(?:api[_-]?key|access[_-]?token|password|authorization)\b\s*[:=]\s*)"
        r"([^\s,;]+)", r"\1[REDACTED]", tail,
    )
    return tail


def last_worker_stage(stderr: str) -> str:
    stages = re.findall(r"\[OptionHelper/[^\]\r\n]+\]", str(stderr or ""))
    return stages[-1] if stages else ""
