"""T174/T175: launch strategies and binding proof (not discovery alone)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from aichestra.execution.domain import (
    AgentRuntime,
    Compatibility,
    DiscoveryFacts,
    ExecutionCapabilities,
    LaunchCapability,
    LaunchStrategy,
    Locality,
    Model,
    ModelProvider,
)
from aichestra.execution.launch_strategies import (
    ExistingTerminalLaunch,
    LaunchContext,
    NativeLaunch,
    NativeModelLaunch,
    TerminalBridgeLaunch,
    abort_prepared,
    bridge_command_for,
    discover_launches,
)
from aichestra.execution.targets import resolve_targets


def _schema(*extra_flags: str) -> dict:
    flags = ["agent", "task", "worktree", "terminal", "model", *extra_flags]
    return {
        "schemaVersion": 1,
        "commands": [
            {"path": ["orchestration", "worker-start"], "flags": flags},
            {"path": ["terminal", "create"], "flags": ["command", "worktree", "title"]},
            {"path": ["terminal", "wait"], "flags": ["terminal", "for", "timeout-ms"]},
            {"path": ["terminal", "read"], "flags": ["terminal"]},
            {"path": ["terminal", "show"], "flags": ["terminal"]},
            {"path": ["terminal", "close"], "flags": ["terminal"]},
        ],
    }


def _opencode_target(*, endpoint: str = "http://127.0.0.1:11434"):
    runtime = AgentRuntime("opencode", True, binary_path="/usr/bin/opencode")
    provider = ModelProvider(
        "ollama", True, endpoint=endpoint, locality=Locality.LOCAL
    )
    model = Model(
        "qwen",
        "ollama",
        True,
        capabilities=ExecutionCapabilities(frozenset({"code"})),
    )
    facts = DiscoveryFacts(runtimes=(runtime,), providers=(provider,), models=(model,))
    bindings = (Compatibility("opencode", "ollama", "qwen"),)
    return resolve_targets(facts, bindings)[0]


def _binding_tail() -> list[str]:
    return [
        "env OPENCODE_CONFIG_CONTENT='{\"model\":\"ollama/qwen\","
        "\"provider\":{\"ollama\":{\"options\":"
        "{\"baseURL\":\"http://127.0.0.1:11434/v1\"}}}}' "
        "/usr/bin/opencode"
    ]


def test_native_rejects_provider_model_endpoint_claim():
    target = _opencode_target()
    assert not NativeLaunch("opencode").accepts(target)
    assert not NativeLaunch("opencode").prove(target, _schema())


def test_native_model_requires_effective_model_receipt():
    runtime = AgentRuntime("codex", True)
    facts = DiscoveryFacts(runtimes=(runtime,))
    bindings = (Compatibility("codex", model="gpt-5.5"),)
    target = resolve_targets(facts, bindings)[0]
    adapter = NativeModelLaunch("codex")
    assert adapter.accepts(target)
    assert adapter.prove(target, _schema())
    assert adapter.arguments(target) == ["--agent", "codex", "--model", "gpt-5.5"]
    assert adapter.confirms(
        target, {"result": {"launch": {"effective": {"agent": "codex", "model": "gpt-5.5"}}}}
    )
    assert not adapter.confirms(
        target, {"result": {"launch": {"effective": {"agent": "codex"}}}}
    )
    # Provider/endpoint dimensions are not covered by --model alone.
    with_provider = _opencode_target()
    assert not adapter.accepts(with_provider)


def test_discover_launches_native_proven_bridge_only_provisionable(monkeypatch):
    runtime = AgentRuntime("codex", True)
    facts = DiscoveryFacts(runtimes=(runtime,))
    native = resolve_targets(facts, (Compatibility("codex"),))[0]
    bridge = _opencode_target()
    monkeypatch.setattr(
        "aichestra.execution.launch_strategies.load_orca_schema",
        lambda binary=None: _schema(),
    )
    launches = discover_launches([native, bridge], binary="orca-test")
    by_runtime = {item.runtime: item for item in launches}
    assert by_runtime["codex"].strategy is LaunchStrategy.ORCA_NATIVE
    assert by_runtime["codex"].proven is True
    assert by_runtime["codex"].model is None
    assert by_runtime["opencode"].strategy is LaunchStrategy.ORCA_TERMINAL_BRIDGE
    assert by_runtime["opencode"].proven is False
    assert by_runtime["opencode"].provider == "ollama"
    assert by_runtime["opencode"].model == "qwen"
    assert by_runtime["opencode"].endpoint == "http://127.0.0.1:11434"
    resolved = resolve_targets(
        DiscoveryFacts(
            runtimes=(bridge.runtime,),
            providers=(bridge.provider,),
            models=(bridge.model,),
        ),
        (Compatibility("opencode", "ollama", "qwen"),),
        known_launches=(by_runtime["opencode"],),
    )[0]
    # Schema+builder ⇒ provisionable / dispatchable, NOT runnable yet.
    assert not resolved.runnable
    assert resolved.provisionable
    assert resolved.dispatchable
    assert resolved.launch_strategy is LaunchStrategy.ORCA_TERMINAL_BRIDGE


def test_prompt_metadata_is_not_launch_proof():
    target = _opencode_target()
    # No LaunchCapability → unsupported even when "opencode/ollama" appears in a prompt.
    assert target.launch_strategy is LaunchStrategy.UNSUPPORTED
    assert not target.runnable


def test_bridge_command_embeds_binding_in_orca_process_argv():
    target = _opencode_target()
    bridge = bridge_command_for(target)
    assert bridge is not None
    assert "ollama/qwen" in bridge.command
    assert "http://127.0.0.1:11434/v1" in bridge.command
    assert "OPENCODE_CONFIG_CONTENT" in bridge.command
    assert "/usr/bin/opencode" in bridge.command


def test_terminal_bridge_prepare_and_confirm_require_process_evidence():
    target = _opencode_target()
    adapter = TerminalBridgeLaunch()
    assert adapter.prove(target, _schema())
    calls: list[list[str]] = []

    def run(argv, **_kwargs):
        calls.append(list(argv))
        if argv[1:3] == ["terminal", "create"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"ok": True, "result": {"terminal": {"handle": "term_1"}}}),
            )
        if argv[1:3] == ["terminal", "wait"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True}))
        if argv[1:3] == ["terminal", "read"]:
            # Actual process echo must contain binding tokens.
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "result": {
                            "terminal": {
                                "handle": "term_1",
                                "tail": _binding_tail(),
                            }
                        },
                    }
                ),
            )
        raise AssertionError(argv)

    prepared = adapter.prepare(
        target, LaunchContext(binary="orca", worktree="current", run=run)
    )
    assert prepared.arguments == ["--terminal", "term_1"]
    assert prepared.owns_terminal is True
    assert any(c[1:3] == ["terminal", "create"] for c in calls)
    assert adapter.confirms(
        target,
        {
            "result": {"dispatchId": "d1", "agentTerminalHandle": "term_1"},
            "aichestra_terminal_evidence": prepared.evidence,
        },
    )
    assert not adapter.confirms(
        target,
        {
            "result": {"dispatchId": "d1", "agentTerminalHandle": "term_1"},
            # Evidence missing → prompt/config alone is not enough.
        },
    )


def test_terminal_bridge_rejects_create_receipt_as_process_proof():
    """startupCommand on create must not satisfy binding; live read must."""
    target = _opencode_target()
    adapter = TerminalBridgeLaunch()
    bridge = bridge_command_for(target)
    assert bridge is not None
    closed: list[str] = []

    def run(argv, **_kwargs):
        if argv[1:3] == ["terminal", "create"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "result": {
                            "terminal": {
                                "handle": "term_leak",
                                "startupCommand": bridge.command,
                                "command": bridge.command,
                            }
                        },
                    }
                ),
            )
        if argv[1:3] == ["terminal", "wait"]:
            return SimpleNamespace(returncode=0, stdout="{}")
        if argv[1:3] == ["terminal", "read"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "result": {
                            "terminal": {"handle": "term_leak", "tail": ["shell"]}
                        },
                    }
                ),
            )
        if argv[1:3] == ["terminal", "close"]:
            closed.append(argv[argv.index("--terminal") + 1])
            return SimpleNamespace(returncode=0, stdout="{}")
        raise AssertionError(argv)

    with pytest.raises(ValueError, match="did not prove"):
        adapter.prepare(target, LaunchContext(binary="orca", worktree="current", run=run))
    assert closed == ["term_leak"]


def test_terminal_bridge_rejects_unproven_process_and_closes_owned_terminal():
    target = _opencode_target()
    adapter = TerminalBridgeLaunch()
    closed: list[str] = []

    def run(argv, **_kwargs):
        if argv[1:3] == ["terminal", "create"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"ok": True, "result": {"terminal": {"handle": "term_x"}}}),
            )
        if argv[1:3] == ["terminal", "wait"]:
            return SimpleNamespace(returncode=0, stdout="{}")
        if argv[1:3] == ["terminal", "read"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"ok": True, "result": {"terminal": {"handle": "term_x", "tail": ["shell"]}}}
                ),
            )
        if argv[1:3] == ["terminal", "close"]:
            closed.append(argv[argv.index("--terminal") + 1])
            return SimpleNamespace(returncode=0, stdout="{}")
        raise AssertionError(argv)

    with pytest.raises(ValueError, match="did not prove"):
        adapter.prepare(target, LaunchContext(binary="orca", worktree="current", run=run))
    assert closed == ["term_x"]


def test_abort_prepared_never_closes_foreign_existing_terminal():
    ctx = LaunchContext(binary="orca", run=lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    from aichestra.execution.launch_strategies import PreparedLaunch

    result = abort_prepared(
        PreparedLaunch(
            arguments=["--terminal", "foreign"],
            terminal_handle="foreign",
            owns_terminal=False,
        ),
        ctx,
    )
    assert result["skipped"] is True


def test_existing_terminal_requires_attested_handle():
    target = _opencode_target()
    adapter = ExistingTerminalLaunch()
    assert not adapter.prove(target, _schema())  # no handle at discovery time
    with pytest.raises(ValueError, match="terminal_handle"):
        adapter.prepare(target, LaunchContext(binary="orca", run=lambda *a, **k: None))

    def run(argv, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "result": {
                        "terminal": {
                            "handle": "term_live",
                            "tail": [
                                "OPENCODE_CONFIG_CONTENT ollama/qwen "
                                "http://127.0.0.1:11434/v1 /usr/bin/opencode"
                            ],
                        }
                    },
                }
            ),
        )

    prepared = adapter.prepare(
        target,
        LaunchContext(binary="orca", terminal_handle="term_live", run=run),
    )
    assert prepared.arguments == ["--terminal", "term_live"]
    assert prepared.owns_terminal is False
    # Injected proven capability may advertise existing-terminal once attested.
    launch = LaunchCapability(
        "opencode",
        "ollama",
        "qwen",
        "http://127.0.0.1:11434",
        LaunchStrategy.ORCA_EXISTING_TERMINAL,
        proven=True,
    )
    asserted = resolve_targets(
        DiscoveryFacts(
            runtimes=(target.runtime,),
            providers=(target.provider,),
            models=(target.model,),
        ),
        (Compatibility("opencode", "ollama", "qwen"),),
        known_launches=(launch,),
    )[0]
    assert asserted.runnable
    assert asserted.launch_strategy is LaunchStrategy.ORCA_EXISTING_TERMINAL


def test_discover_launches_attests_existing_terminal_from_live_handle(monkeypatch):
    target = _opencode_target()
    monkeypatch.setattr(
        "aichestra.execution.launch_strategies.load_orca_schema",
        lambda binary=None: _schema(),
    )

    def run(argv, **_kwargs):
        if argv[1:3] in (["terminal", "show"], ["terminal", "read"]):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "result": {
                            "terminal": {
                                "handle": "term_prod",
                                "tail": _binding_tail(),
                            }
                        },
                    }
                ),
            )
        raise AssertionError(argv)

    launches = discover_launches(
        [target],
        binary="orca-test",
        terminal_handle="term_prod",
        run=run,
    )
    assert len(launches) == 1
    assert launches[0].strategy is LaunchStrategy.ORCA_EXISTING_TERMINAL
    assert launches[0].proven is True
    resolved = resolve_targets(
        DiscoveryFacts(
            runtimes=(target.runtime,),
            providers=(target.provider,),
            models=(target.model,),
        ),
        (Compatibility("opencode", "ollama", "qwen"),),
        known_launches=launches,
    )[0]
    assert resolved.runnable
    assert not resolved.provisionable
