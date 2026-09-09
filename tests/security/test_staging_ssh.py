"""Tests for staging SSH alias resolution and gated runner."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aichestra.security.staging_allowlist import PRODUCTION_SSH_SUPPORTED
from aichestra.security.staging_ssh import (
    UnknownStagingAliasError,
    build_ssh_argv,
    resolve_staging_alias,
    run_staging_diagnostic,
)


def test_production_ssh_not_supported():
    assert PRODUCTION_SSH_SUPPORTED is False


def test_unknown_alias_fails_closed(tmp_path):
    # Empty machine-local / no aliases
    with pytest.raises(UnknownStagingAliasError) as exc:
        resolve_staging_alias(
            "missing-alias",
            config={"staging": {"aliases": {}}},
        )
    assert "Fail closed" in str(exc.value)


def test_production_alias_stripped_from_loader():
    with pytest.raises(UnknownStagingAliasError):
        resolve_staging_alias(
            "prod",
            config={
                "staging": {
                    "aliases": {
                        "prod": {
                            "host": "prod.example",
                            "environment": "production",
                        }
                    }
                }
            },
        )


def test_destructive_rejected_before_executor():
    called = {"n": 0}

    def executor(_argv):
        called["n"] += 1
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    result = run_staging_diagnostic(
        alias_name="stg",
        remote_argv=["sudo", "rm", "-rf", "/"],
        allow_raw_argv=True,
        config={
            "staging": {
                "aliases": {"stg": {"host": "stg.example", "environment": "staging"}}
            }
        },
        executor=executor,
    )
    assert result.ok is False
    assert called["n"] == 0
    assert "denied" in (result.error or result.decision.reason).lower()


def test_safe_op_runs_and_sanitizes():
    def executor(_argv):
        return SimpleNamespace(
            returncode=0,
            stdout="Authorization: Bearer SECRETTOKEN\nuptime ok",
            stderr="",
        )

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
    assert result.ok is True
    assert "SECRETTOKEN" not in result.sanitized_for_cloud
    assert "Bearer" in result.sanitized_for_cloud or "REDACTED" in result.sanitized_for_cloud
    assert result.ssh_argv[0] in {"ssh", result.ssh_argv[0]}
    assert "stg.example" in " ".join(result.ssh_argv)
    assert "uptime" in result.ssh_argv


def test_build_ssh_argv_never_reads_missing_key(tmp_path):
    from aichestra.security.staging_ssh import StagingAlias

    alias = StagingAlias(
        name="stg",
        host="stg.example",
        identity_file=str(tmp_path / "does-not-exist"),
    )
    argv = build_ssh_argv(alias, ["uptime"])
    assert "-i" not in argv  # missing key path not passed
