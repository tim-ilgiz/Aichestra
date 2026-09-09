"""Sanitize secrets-like material before cloud handoff (FR-034)."""

from __future__ import annotations

import re

_REDACTED = "[REDACTED]"

# Authorization / Bearer / password / cookie / token / private keys
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)([^\r\n]+)"),
    re.compile(r"(?i)(\bbearer\s+)([A-Za-z0-9\-._~+/]+=*)"),
    re.compile(r"(?i)(password\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)(passwd\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)((?:set-)?cookie\s*[:=]\s*)([^\r\n]+)"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)(access[_-]?token\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)(refresh[_-]?token\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)(secret\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)(x-api-key\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)(\btoken\s*[:=]\s*)(\S+)"),
    re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]*?"
        r"-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
    ),
    re.compile(r"(?i)(ssh-rsa\s+)([A-Za-z0-9+/=]{40,})"),
)


def sanitize_text(text: str) -> str:
    """Redact credentials-like strings; never return private key material."""
    if not text:
        return text
    out = text
    for pattern in _PATTERNS:
        if pattern.groups >= 2:
            out = pattern.sub(lambda m: f"{m.group(1)}{_REDACTED}", out)
        else:
            out = pattern.sub(_REDACTED, out)
    return out


_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "password",
        "passwd",
        "cookie",
        "set-cookie",
        "token",
        "access_token",
        "access-token",
        "refresh_token",
        "refresh-token",
        "api_key",
        "api-key",
        "apikey",
        "secret",
        "private_key",
        "private-key",
    }
)


def _sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    lowered = key.strip().lower().replace(" ", "_")
    if lowered in _SENSITIVE_KEYS:
        return True
    return any(part in lowered for part in ("password", "token", "secret", "cookie"))


def sanitize_mapping(data: dict) -> dict:
    """Return a copied mapping with string values sanitized.

    Values under credential-like keys are fully redacted even when the value
    alone would not match inline ``password=…`` text patterns.
    """
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            if _sensitive_key(key):
                result[key] = _REDACTED
            else:
                result[key] = sanitize_text(value)
        elif isinstance(value, dict):
            result[key] = sanitize_mapping(value)
        elif isinstance(value, list):
            result[key] = [
                sanitize_text(v) if isinstance(v, str) else v for v in value
            ]
        else:
            result[key] = value
    return result
