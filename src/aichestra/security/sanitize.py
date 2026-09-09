"""Sanitize secrets-like material before cloud handoff (FR-034)."""

from __future__ import annotations

import re

_REDACTED = "[REDACTED]"

# Sensitive key names used in JSON / structured logs (with or without quotes).
_SENSITIVE_KEY_ALT = (
    r"authorization|passwd|password|access[_-]?token|refresh[_-]?token|"
    r"api[_-]?key|apikey|x-api-key|set-cookie|cookie|secret|private[_-]?key|token"
)

# Authorization / Bearer / password / cookie / token / private keys / JSON fields
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # JSON / structured: "password":"…" or "password": "…"
    re.compile(
        rf'(?i)("(?:{_SENSITIVE_KEY_ALT})"\s*:\s*")([^"]*)(")'
    ),
    # JSON single-quoted / bare key variants: password: "…" / 'password':'…'
    re.compile(
        rf"(?i)(['\"]?(?:{_SENSITIVE_KEY_ALT})['\"]?\s*[:=]\s*['\"])"
        r"([^'\"\r\n,}\]]+)(['\"])"
    ),
    # Unquoted assignment forms: password=… / token: …
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
        if pattern.groups >= 3:
            out = pattern.sub(
                lambda m: f"{m.group(1)}{_REDACTED}{m.group(3)}", out
            )
        elif pattern.groups >= 2:
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
            result[key] = [_sanitize_list_item(v) for v in value]
        else:
            result[key] = value
    return result


def _sanitize_list_item(value: object) -> object:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return sanitize_mapping(value)
    if isinstance(value, list):
        return [_sanitize_list_item(v) for v in value]
    return value
