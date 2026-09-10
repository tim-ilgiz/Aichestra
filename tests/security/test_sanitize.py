"""Security: diagnostic sanitization before cloud handoff."""

from __future__ import annotations

import json

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


def test_sanitize_json_password_and_access_token() -> None:
    raw = '{"password":"DUMMY_PASSWORD","access_token":"DUMMY_TOKEN"}'
    cleaned = sanitize_text(raw)
    assert "DUMMY_PASSWORD" not in cleaned
    assert "DUMMY_TOKEN" not in cleaned
    assert "[REDACTED]" in cleaned
    assert '"password":' in cleaned
    assert '"access_token":' in cleaned


def test_sanitize_nested_json_in_logs() -> None:
    raw = 'info user={"password": "nested-secret", "ok": true}'
    cleaned = sanitize_text(raw)
    assert "nested-secret" not in cleaned


def test_sanitize_mapping_nested() -> None:
    data = {
        "headers": {"Authorization": "Bearer XYZ"},
        "nested": {"password": "pw"},
        "list": ["token: abc123", {"access_token": "tok"}],
    }
    out = sanitize_mapping(data)
    assert "XYZ" not in out["headers"]["Authorization"]
    assert "pw" not in out["nested"]["password"]
    assert "abc123" not in out["list"][0]
    assert out["list"][1]["access_token"] == "[REDACTED]"


def test_staging_json_secrets_redacted_in_to_dict() -> None:
    from types import SimpleNamespace

    from aichestra.security.staging_ssh import run_staging_diagnostic

    blob = '{"password":"DUMMY_PASSWORD","access_token":"DUMMY_TOKEN"}'

    def executor(_argv):
        return SimpleNamespace(returncode=0, stdout=blob, stderr="")

    result = run_staging_diagnostic(
        alias_name="stg",
        op_kind="uptime",
        config={
            "staging": {
                "aliases": {
                    "stg": {
                        "host": "stg.example",
                        "user": "deploy",
                        "environment": "staging",
                    }
                }
            }
        },
        executor=executor,
    )
    payload = result.to_dict()
    dumped = json.dumps(payload)
    assert "DUMMY_PASSWORD" not in dumped
    assert "DUMMY_TOKEN" not in dumped
    assert "DUMMY_PASSWORD" in result.stdout
    assert "[REDACTED]" in payload["sanitized_for_cloud"]
