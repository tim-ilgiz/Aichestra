"""Security: diagnostic sanitization before cloud handoff."""

from __future__ import annotations

from aichestra.security.sanitize import sanitize_mapping, sanitize_text


def test_sanitize_authorization_bearer_password_cookie_token_key() -> None:
    raw = "\n".join(
        [
            "Authorization: Bearer SUPERSECRETTOKEN",
            "password=hunter2",
            "Cookie: session=abc; other=1",
            "token: mytokensecret",
            "-----BEGIN PRIVATE KEY-----",
            "MIIEsecretKEYDATA",
            "-----END PRIVATE KEY-----",
        ]
    )
    cleaned = sanitize_text(raw)
    assert "SUPERSECRETTOKEN" not in cleaned
    assert "hunter2" not in cleaned
    assert "session=abc" not in cleaned
    assert "mytokensecret" not in cleaned
    assert "MIIEsecretKEYDATA" not in cleaned
    assert "BEGIN PRIVATE KEY" not in cleaned
    assert "[REDACTED]" in cleaned


def test_sanitize_mapping_nested() -> None:
    data = {
        "headers": {"Authorization": "Bearer XYZ"},
        "nested": {"password": "pw"},
        "list": ["token: abc123"],
    }
    out = sanitize_mapping(data)
    assert "XYZ" not in out["headers"]["Authorization"]
    assert "pw" not in out["nested"]["password"]
    assert "abc123" not in out["list"][0]
