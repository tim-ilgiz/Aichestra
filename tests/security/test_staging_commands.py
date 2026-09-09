"""Security: staging allowlist — deny before execution."""

from __future__ import annotations

from aichestra.security.staging_allowlist import (
    PRODUCTION_SSH_SUPPORTED,
    evaluate_command,
)
from aichestra.security.staging_ops import build_op


def test_production_ssh_unsupported() -> None:
    assert PRODUCTION_SSH_SUPPORTED is False
    decision = evaluate_command(["uptime"], environment="production")
    assert decision.allowed is False
    assert "Production" in decision.reason or "production" in decision.reason.lower()


def test_allow_safe_staging_ops() -> None:
    op = build_op("uptime")
    decision = evaluate_command(op, environment="staging")
    assert decision.allowed is True
    assert decision.argv == ("uptime",)


def test_reject_destructive_before_exec() -> None:
    cases = [
        ["sudo", "ls"],
        ["rm", "-rf", "/tmp/x"],
        ["kill", "1"],
        ["docker", "stop", "ctr"],
        ["docker", "rm", "ctr"],
        ["kubectl", "apply", "-f", "x.yaml"],
        ["kubectl", "delete", "pod", "x"],
        ["systemctl", "restart", "nginx"],
        ["systemctl", "stop", "nginx"],
        ["chmod", "777", "/tmp"],
    ]
    for argv in cases:
        decision = evaluate_command(argv, environment="staging")
        assert decision.allowed is False, argv


def test_unknown_environment_fail_closed() -> None:
    decision = evaluate_command(["uptime"], environment="mystery")
    assert decision.allowed is False
