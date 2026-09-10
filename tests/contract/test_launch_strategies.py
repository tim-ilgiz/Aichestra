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
    expected_binding,
    prove_bootstrap_launch,
    select_bootstrap,
    select_bootstrap_candidate,
    structured_binding_matches,
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


def _opencode_config(*, model: str = "qwen", provider: str = "ollama") -> dict:
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": f"{provider}/{model}",
        "provider": {
            provider: {
                "npm": "@ai-sdk/openai-compatible",
                "name": f"{provider} (Aichestra ExecutionTarget)",
                "options": {"baseURL": "http://127.0.0.1:11434/v1"},
                "models": {model: {"name": model}},
            }
        },
    }


def _process_argv(*, model: str = "qwen") -> list[str]:
    config = json.dumps(_opencode_config(model=model), separators=(",", ":"))
    return ["env", f"OPENCODE_CONFIG_CONTENT={config}", "/usr/bin/opencode"]


def _structured_terminal(handle: str, *, model: str = "qwen", with_screen_decoy: bool = True) -> dict:
    terminal = {
        "handle": handle,
        "process": {"pid": 4242, "argv": _process_argv(model=model)},
    }
    if with_screen_decoy:
        # Screen text that would falsely satisfy substring proof — must be ignored.
        terminal["tail"] = [
            "ollama/qwen http://127.0.0.1:11434/v1 /usr/bin/opencode OPENCODE_CONFIG_CONTENT"
        ]
        terminal["preview"] = "ollama/qwen"
    return {"ok": True, "result": {"terminal": terminal}}


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
    # Bootstrap selection must not treat provisionable as selected-before-proof.
    assert select_bootstrap([resolved]) is None
    assert select_bootstrap_candidate([resolved]) is resolved


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


def test_terminal_bridge_prepare_and_confirm_require_structured_process_evidence():
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
        if argv[1:3] in (["terminal", "show"], ["terminal", "read"]):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(_structured_terminal("term_1")),
            )
        raise AssertionError(argv)

    prepared = adapter.prepare(
        target, LaunchContext(binary="orca", worktree="current", run=run)
    )
    assert prepared.arguments == ["--terminal", "term_1"]
    assert prepared.owns_terminal is True
    assert any(c[1:3] == ["terminal", "create"] for c in calls)
    assert any(c[1:3] == ["terminal", "show"] for c in calls)
    assert "process" in prepared.evidence
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


def test_screen_substring_is_not_binding_proof():
    """qwen must not match via substring of qwen2 / screen decoy text."""
    target = _opencode_target()
    # Screen text contains exact tokens; without structured process → fail.
    assert not structured_binding_matches(
        target,
        {
            "text": "ollama/qwen http://127.0.0.1:11434/v1 /usr/bin/opencode",
            "tail": ["ollama/qwen"],
            "proof_tokens": ["ollama/qwen", "http://127.0.0.1:11434/v1"],
        },
    )
    # Structured argv with wrong model qwen2 must not satisfy expected qwen.
    wrong = {
        "process": {"pid": 1, "argv": _process_argv(model="qwen2")},
        "handle": "term_x",
    }
    assert not structured_binding_matches(target, wrong)
    # Exact structured argv for qwen succeeds.
    assert structured_binding_matches(
        target,
        {"process": {"pid": 1, "argv": _process_argv(model="qwen")}, "handle": "term_x"},
    )


def test_terminal_bridge_rejects_create_receipt_as_process_proof():
    """startupCommand on create must not satisfy binding; live structured show must."""
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
        if argv[1:3] in (["terminal", "show"], ["terminal", "read"]):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "result": {
                            "terminal": {
                                "handle": "term_leak",
                                "tail": [bridge.command],
                                "startupCommand": bridge.command,
                            }
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


def test_terminal_bridge_rejects_failed_read_returncode():
    target = _opencode_target()
    adapter = TerminalBridgeLaunch()
    closed: list[str] = []

    def run(argv, **_kwargs):
        if argv[1:3] == ["terminal", "create"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"ok": True, "result": {"terminal": {"handle": "term_rc"}}}),
            )
        if argv[1:3] == ["terminal", "wait"]:
            return SimpleNamespace(returncode=0, stdout="{}")
        if argv[1:3] == ["terminal", "show"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(_structured_terminal("term_rc")),
            )
        if argv[1:3] == ["terminal", "read"]:
            return SimpleNamespace(
                returncode=1,
                stdout=json.dumps(_structured_terminal("term_rc")),
            )
        if argv[1:3] == ["terminal", "close"]:
            closed.append(argv[argv.index("--terminal") + 1])
            return SimpleNamespace(returncode=0, stdout="{}")
        raise AssertionError(argv)

    with pytest.raises(ValueError, match="observation failed|did not prove"):
        adapter.prepare(target, LaunchContext(binary="orca", worktree="current", run=run))
    assert closed == ["term_rc"]


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
        if argv[1:3] in (["terminal", "show"], ["terminal", "read"]):
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
            stdout=json.dumps(_structured_terminal("term_live")),
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
                stdout=json.dumps(_structured_terminal("term_prod")),
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


def test_prove_bootstrap_launch_promotes_bridge_before_run(monkeypatch):
    target = _opencode_target()
    monkeypatch.setattr(
        "aichestra.execution.launch_strategies.load_orca_schema",
        lambda binary=None: _schema(),
    )
    launches = discover_launches([target], binary="orca-test")
    resolved = resolve_targets(
        DiscoveryFacts(
            runtimes=(target.runtime,),
            providers=(target.provider,),
            models=(target.model,),
        ),
        (Compatibility("opencode", "ollama", "qwen"),),
        known_launches=launches,
    )[0]
    assert resolved.provisionable
    assert select_bootstrap([resolved]) is None

    def run(argv, **_kwargs):
        if argv[1:3] == ["terminal", "create"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"ok": True, "result": {"terminal": {"handle": "term_boot"}}}),
            )
        if argv[1:3] == ["terminal", "wait"]:
            return SimpleNamespace(returncode=0, stdout="{}")
        if argv[1:3] in (["terminal", "show"], ["terminal", "read"]):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(_structured_terminal("term_boot")),
            )
        raise AssertionError(argv)

    proven, prepared = prove_bootstrap_launch(
        resolved,
        LaunchContext(binary="orca", worktree="current", run=run),
    )
    assert proven.runnable
    assert not proven.provisionable
    assert prepared.terminal_handle == "term_boot"
    assert prepared.owns_terminal is True
    assert expected_binding(proven)["model"] == "qwen"
    assert select_bootstrap([proven]) is proven


def test_prove_launch_is_bootstrap_alias_and_promotes_candidate(monkeypatch):
    from aichestra.execution.launch_strategies import prove_launch

    target = _opencode_target()
    monkeypatch.setattr(
        "aichestra.execution.launch_strategies.load_orca_schema",
        lambda binary=None: _schema(),
    )
    launches = discover_launches([target], binary="orca-test")
    resolved = resolve_targets(
        DiscoveryFacts(
            runtimes=(target.runtime,),
            providers=(target.provider,),
            models=(target.model,),
        ),
        (Compatibility("opencode", "ollama", "qwen"),),
        known_launches=launches,
    )[0]
    assert resolved.provisionable

    def run(argv, **_kwargs):
        if argv[1:3] == ["terminal", "create"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"ok": True, "result": {"terminal": {"handle": "term_inner"}}}),
            )
        if argv[1:3] == ["terminal", "wait"]:
            return SimpleNamespace(returncode=0, stdout="{}")
        if argv[1:3] in (["terminal", "show"], ["terminal", "read"]):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(_structured_terminal("term_inner")),
            )
        raise AssertionError(argv)

    proven, prepared = prove_launch(
        resolved,
        LaunchContext(binary="orca", worktree="current", run=run),
    )
    assert proven.runnable
    assert prepared.terminal_handle == "term_inner"


def _bridge_target_for_controller(monkeypatch):
    target = _opencode_target()
    monkeypatch.setattr(
        "aichestra.execution.launch_strategies.load_orca_schema",
        lambda binary=None: _schema(),
    )
    launches = discover_launches([target], binary="orca-test")
    return resolve_targets(
        DiscoveryFacts(
            runtimes=(target.runtime,),
            providers=(target.provider,),
            models=(target.model,),
        ),
        (Compatibility("opencode", "ollama", "qwen"),),
        known_launches=launches,
    )[0]


def _controller_bindings(tmp_path, *, orca, targets):
    import sys

    from aichestra.orchestration.workflow import WorkflowBindings

    return WorkflowBindings(
        orca=orca,
        providers=[],
        project_root=str(tmp_path),
        task_prompt="bridge bootstrap",
        maintenance_kwargs={"change_summary": "x", "touches_behavior": False},
        verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        execution_targets=tuple(targets),
    )


def test_controller_bridge_proof_fail_zero_run_create(tmp_path, monkeypatch):
    """T173/T174: bootstrap proof FAIL → zero Orca run-create."""
    from aichestra.orchestration.modes import Mode
    from aichestra.orchestration.workflow import ModeCRunController
    from tests.fakes.providers import fake_orca

    bridge = _bridge_target_for_controller(monkeypatch)
    assert bridge.provisionable

    monkeypatch.setattr(
        "aichestra.execution.launch_strategies.prove_bootstrap_launch",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("binding proof failed")),
    )
    monkeypatch.setattr(
        "aichestra.providers.orca.resolve_orca_binary",
        lambda: "orca-test",
    )
    orca = fake_orca("success")
    state = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_controller_bindings(tmp_path, orca=orca, targets=[bridge]),
    ).run_all()
    assert state.failed
    assert orca.run_creates == 0
    assert not any((r.role or "") == "ensure_run" for r in orca.sent)


def test_controller_bridge_proof_success_one_terminal_one_run(tmp_path, monkeypatch):
    """T173/T174: proof SUCCESS → exactly one owned terminal + one run-create."""
    from aichestra.execution.launch_strategies import PreparedLaunch, mark_launch_proven
    from aichestra.orchestration.modes import Mode
    from aichestra.orchestration.workflow import MODE_C_HANDOFF_ROLE, ModeCRunController
    from tests.fakes.providers import fake_orca

    bridge = _bridge_target_for_controller(monkeypatch)
    proves = []

    def prove_ok(target, ctx):
        proves.append(target.id)
        return mark_launch_proven(target), PreparedLaunch(
            arguments=["--terminal", "term_one"],
            terminal_handle="term_one",
            owns_terminal=True,
            evidence={"process": {"argv": _process_argv()}},
        )

    monkeypatch.setattr(
        "aichestra.execution.launch_strategies.prove_bootstrap_launch",
        prove_ok,
    )
    monkeypatch.setattr(
        "aichestra.providers.orca.resolve_orca_binary",
        lambda: "orca-test",
    )
    orca = fake_orca("success")
    state = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_controller_bindings(tmp_path, orca=orca, targets=[bridge]),
    ).run_all()
    assert not state.failed, state.failed
    assert proves == [bridge.id]
    assert orca.run_creates == 1
    handoff = next(r for r in orca.sent if (r.role or "") == MODE_C_HANDOFF_ROLE)
    prepared = (handoff.context or {}).get("prepared_bootstrap_launch")
    assert prepared["terminal_handle"] == "term_one"
    assert prepared["owns_terminal"] is True
    # Promoted bootstrap appears in runnable execution_targets, not as candidate.
    package = state.metadata["mode_c_policy_package"]
    assert any(row["id"] == bridge.id and row["runnable"] for row in package["execution_targets"])
    assert not any(row["id"] == bridge.id for row in package["execution_target_candidates"])


def test_controller_run_create_fail_aborts_owned_terminal(tmp_path, monkeypatch):
    """T174: run-create FAIL → owned bridge terminal closed via abort_prepared."""
    from aichestra.execution.launch_strategies import PreparedLaunch, mark_launch_proven
    from aichestra.orchestration.modes import Mode
    from aichestra.orchestration.workflow import ModeCRunController
    from aichestra.providers.base import FailureClass, ProviderTaskResult
    from tests.fakes.providers import fake_orca

    bridge = _bridge_target_for_controller(monkeypatch)
    aborts: list = []

    def prove_ok(target, ctx):
        return mark_launch_proven(target), PreparedLaunch(
            arguments=["--terminal", "term_abort"],
            terminal_handle="term_abort",
            owns_terminal=True,
        )

    def tracking_abort(prepared, ctx):
        aborts.append(prepared.terminal_handle)
        return {"ok": True, "closed": True}

    monkeypatch.setattr(
        "aichestra.execution.launch_strategies.prove_bootstrap_launch",
        prove_ok,
    )
    monkeypatch.setattr(
        "aichestra.execution.launch_strategies.abort_prepared",
        tracking_abort,
    )
    monkeypatch.setattr(
        "aichestra.providers.orca.resolve_orca_binary",
        lambda: "orca-test",
    )
    orca = fake_orca("success")
    real_send = orca.send

    def send_fail_ensure(session, request):
        role = (request.role or "").strip().lower()
        if role == "ensure_run":
            orca.sent.append(request)
            orca.run_creates += 1
            return ProviderTaskResult(
                ok=False,
                output="",
                failure=FailureClass.ERROR,
                detail="run-create failed",
                session_id=session.session_id,
                metadata={"fake": True},
            )
        return real_send(session, request)

    orca.send = send_fail_ensure  # type: ignore[method-assign]
    state = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_controller_bindings(tmp_path, orca=orca, targets=[bridge]),
    ).run_all()
    assert state.failed
    assert orca.run_creates == 1
    assert aborts == ["term_abort"]
    assert not any((r.role or "") == "mode_c_handoff" for r in orca.sent)


def test_controller_handoff_reuses_prepared_launch_no_second_prepare(
    tmp_path, monkeypatch
):
    """T174: successful handoff reuses PreparedLaunch; no second prepare/create."""
    from aichestra.execution.launch_strategies import PreparedLaunch, mark_launch_proven
    from aichestra.orchestration.modes import Mode
    from aichestra.orchestration.workflow import MODE_C_HANDOFF_ROLE, ModeCRunController
    from tests.fakes.providers import fake_orca

    bridge = _bridge_target_for_controller(monkeypatch)
    prepare_calls = {"n": 0}

    def prove_ok(target, ctx):
        prepare_calls["n"] += 1
        return mark_launch_proven(target), PreparedLaunch(
            arguments=["--terminal", "term_reuse"],
            terminal_handle="term_reuse",
            owns_terminal=True,
        )

    monkeypatch.setattr(
        "aichestra.execution.launch_strategies.prove_bootstrap_launch",
        prove_ok,
    )
    monkeypatch.setattr(
        "aichestra.providers.orca.resolve_orca_binary",
        lambda: "orca-test",
    )
    # Fake Orca handoff does not call adapter prepare — assert controller passed
    # the preflight payload exactly once and did not invoke prove again.
    orca = fake_orca("success")
    state = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_controller_bindings(tmp_path, orca=orca, targets=[bridge]),
    ).run_all()
    assert not state.failed, state.failed
    assert prepare_calls["n"] == 1
    assert orca.run_creates == 1
    handoff = next(r for r in orca.sent if (r.role or "") == MODE_C_HANDOFF_ROLE)
    assert (handoff.context or {})["prepared_bootstrap_launch"]["terminal_handle"] == (
        "term_reuse"
    )
    assert state.metadata["bootstrap_prepared_launch"]["terminal_handle"] == "term_reuse"
