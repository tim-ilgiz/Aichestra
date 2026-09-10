"""Additional security fixtures for curl writes and shell redirects."""

from __future__ import annotations

from types import SimpleNamespace

from aichestra.security.staging_allowlist import evaluate_command
from aichestra.security.staging_ops import build_op
from aichestra.security.staging_ssh import run_staging_diagnostic


def test_curl_health_get_allowed() -> None:
    op = build_op("curl_health", url="https://example.com/health")
    decision = evaluate_command(op, environment="staging")
    assert decision.allowed is True


def test_curl_write_flags_denied() -> None:
    cases = [
        ["curl", "-o", "/tmp/out", "https://example.com"],
        ["curl", "-O", "https://example.com/file"],
        ["curl", "-d", "a=1", "https://example.com"],
        ["curl", "--data", "a=1", "https://example.com"],
        ["curl", "-X", "POST", "https://example.com"],
        ["curl", "-X", "PUT", "https://example.com"],
        ["curl", "-T", "file.txt", "https://example.com"],
        ["curl", "-F", "f=@x", "https://example.com"],
        ["curl", "-fsSO", "https://example.com/file"],
    ]
    for argv in cases:
        decision = evaluate_command(argv, environment="staging")
        assert decision.allowed is False, argv


def test_redirect_tokens_denied_before_executor() -> None:
    called = {"n": 0}

    def executor(_argv):
        called["n"] += 1
        return SimpleNamespace(returncode=0, stdout="x", stderr="")

    for argv in (["uptime", ">", "/tmp/x"], ["df", "-h", ">>", "/tmp/x"]):
        result = run_staging_diagnostic(
            alias_name="stg",
            remote_argv=argv,
            allow_raw_argv=True,
            config={
                "staging": {
                    "aliases": {
                        "stg": {"host": "stg.example", "environment": "staging"}
                    }
                }
            },
            executor=executor,
        )
        assert result.ok is False
        assert called["n"] == 0
        assert "redirect" in result.decision.reason.lower() or "denied" in (
            result.error or ""
        ).lower()


def test_operator_path_rejects_raw_argv_without_flag() -> None:
    try:
        run_staging_diagnostic(
            alias_name="stg",
            remote_argv=["uptime"],
            config={
                "staging": {
                    "aliases": {
                        "stg": {"host": "stg.example", "environment": "staging"}
                    }
                }
            },
            dry_run=True,
        )
        raised = False
    except ValueError as exc:
        raised = True
        assert "typed" in str(exc).lower() or "op_kind" in str(exc).lower()
    assert raised
