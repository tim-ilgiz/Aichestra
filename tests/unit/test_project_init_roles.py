"""Unit: role bindings, project init/settings, portable config home."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aichestra.config.paths import user_config_home
from aichestra.config.project_settings import (
    init_project,
    settings_set,
    show_settings,
)
from aichestra.config.roles import (
    load_coordinator_binding,
    load_quota_policy,
    load_role_bindings,
    normalize_runtime_id,
    parse_role_binding,
)
from aichestra.repo import resolve_aichestra_config_root


def test_normalize_runtime_aliases() -> None:
    assert normalize_runtime_id("local") == "opencode"
    assert normalize_runtime_id("local-worker") == "opencode"
    assert normalize_runtime_id("Codex") == "codex"
    with pytest.raises(ValueError):
        normalize_runtime_id("chatgpt")


def test_parse_role_binding_string_and_object() -> None:
    assert parse_role_binding("cursor").runtime == "cursor"
    b = parse_role_binding(
        {"runtime": "opencode", "provider": "ollama", "model": "qwen2.5-coder:14b"}
    )
    assert b.runtime == "opencode"
    assert b.provider == "ollama"
    assert b.model == "qwen2.5-coder:14b"


def test_load_role_bindings_and_quota() -> None:
    cfg = {
        "roles": {
            "implement": "codex",
            "research": {"runtime": "cursor"},
            "tests": {"runtime": "opencode", "model": "llama3.2"},
        },
        "quota": {"mode": "auto", "implement_fallback": "cursor"},
        "orchestration": {"coordinator": "cursor"},
    }
    roles = load_role_bindings(cfg)
    assert roles["implement"].runtime == "codex"
    assert roles["tests"].model == "llama3.2"
    assert roles["docs"].runtime == "cursor"  # default
    quota = load_quota_policy(cfg)
    assert quota.mode == "auto"
    assert quota.implement_fallback.runtime == "cursor"
    assert load_quota_policy(
        {"quota": {"mode": "auto", "roles": {"implement": {"runtime": "gemini"}}}}
    ).implement_fallback.runtime == "gemini"
    assert load_coordinator_binding(cfg).runtime == "cursor"
    with pytest.raises(ValueError, match="orchestration.coordinator"):
        load_role_bindings({"roles": {"coordinator": "codex"}})


def test_verification_enabled_toggle() -> None:
    from aichestra.orchestration.verification import (
        require_verification_toggle,
        verification_commands_from_config,
        verification_enabled,
    )

    assert verification_enabled(None) is False
    assert verification_enabled({}) is False
    assert verification_enabled({"verification": {"enabled": False}}) is False
    assert verification_enabled({"verification": {"enabled": True}}) is True
    # String typos must not fail-open via bool("flase") == True.
    assert verification_enabled({"verification": {"enabled": "flase"}}) is False
    assert verification_enabled({"verification": {"enabled": "true"}}) is False
    with pytest.raises(ValueError, match="must be boolean"):
        require_verification_toggle({"verification": {"enabled": "flase"}})
    assert verification_commands_from_config(
        {"verification": {"enabled": False}, "verify": ["pytest"]}
    ) == []
    assert verification_commands_from_config(
        {"verification": {"enabled": True}, "verify": ["pytest", "-q"]}
    ) == [["pytest", "-q"]]


def test_legacy_quota_implement_fallback_with_empty_roles() -> None:
    quota = load_quota_policy(
        {
            "quota": {
                "mode": "auto",
                "roles": {},
                "implement_fallback": "gemini",
            }
        }
    )
    assert quota.mode == "auto"
    assert quota.implement_fallback.runtime == "gemini"
    # Explicit roles.implement wins over legacy alias.
    assert (
        load_quota_policy(
            {
                "quota": {
                    "mode": "auto",
                    "roles": {"implement": "codex"},
                    "implement_fallback": "gemini",
                }
            }
        ).implement_fallback.runtime
        == "codex"
    )


def test_layered_custom_runtime_settings_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runtime registered in machine-local must be bindable in project.json."""
    from aichestra.config.layering import save_machine_local

    cfg_home = tmp_path / "cfg"
    cfg_home.mkdir()
    monkeypatch.setenv("AICHESTRA_CONFIG_HOME", str(cfg_home))
    monkeypatch.delenv("AICHESTRA_REPO_ROOT", raising=False)
    save_machine_local(
        {"execution": {"runtimes": {"my-agent": {}}}},
        cfg_home,
    )
    project = tmp_path / "app"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='app'\n", encoding="utf-8")
    (project / "AGENTS.md").write_text("# app\n", encoding="utf-8")
    # Leave the Aichestra clone cwd so resolve_aichestra_config_root uses
    # user config home (installed-package path), not the contributor clone.
    monkeypatch.chdir(project)
    init_project(project, yes=True)
    shown = settings_set(project, ["roles.tests=my-agent"])
    assert shown["roles"]["tests"]["runtime"] == "my-agent"
    # Project file stores the binding without requiring a local runtime copy.
    raw = json.loads((project / ".aichestra" / "project.json").read_text(encoding="utf-8"))
    assert raw["roles"]["tests"]["runtime"] == "my-agent"
    assert "my-agent" not in (raw.get("execution") or {}).get("runtimes", {})


def test_settings_rejects_non_boolean_verification_enabled(tmp_path: Path) -> None:
    init_project(tmp_path, yes=True)
    with pytest.raises(ValueError, match="must be boolean"):
        settings_set(tmp_path, ["verification.enabled=flase"])


def test_init_and_settings_roundtrip(tmp_path: Path) -> None:
    project = tmp_path / "app"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='app'\n", encoding="utf-8")
    result = init_project(project, yes=True)
    assert result["created"] is True
    path = Path(result["path"])
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["roles"]["implement"]["runtime"] == "codex"
    assert data["orchestration"]["coordinator"]["runtime"] == "codex"
    assert data["quota"]["roles"]["implement"]["runtime"] == "cursor"

    shown = settings_set(
        project,
        [
            "orchestration.coordinator=cursor",
            "roles.research=cursor",
            "roles.tests.runtime=opencode",
            "roles.tests.model=qwen2.5-coder:14b",
            "quota.mode=auto",
            "quota.roles.implement=gemini",
        ],
    )
    assert shown["orchestration"]["coordinator"]["runtime"] == "cursor"
    assert shown["roles"]["implement"]["runtime"] == "codex"
    assert shown["roles"]["tests"]["runtime"] == "opencode"
    assert shown["roles"]["tests"]["model"] == "qwen2.5-coder:14b"
    assert shown["quota"]["mode"] == "auto"
    assert shown["quota"]["roles"]["implement"]["runtime"] == "gemini"
    again = show_settings(project)
    assert again["roles"]["research"]["runtime"] == "cursor"


def test_bootstrap_from_foreign_project_uses_user_config_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Installed-package bootstrap must not adopt a foreign project as root."""
    from aichestra.bootstrap.core import bootstrap
    from aichestra.config.layering import machine_local_path

    foreign = tmp_path / "KateRentBot"
    foreign.mkdir()
    (foreign / "pyproject.toml").write_text("[project]\nname='bot'\n", encoding="utf-8")
    (foreign / "AGENTS.md").write_text("# bot\n", encoding="utf-8")
    cfg_home = tmp_path / "aichestra-cfg"
    monkeypatch.setenv("AICHESTRA_CONFIG_HOME", str(cfg_home))
    monkeypatch.delenv("AICHESTRA_REPO_ROOT", raising=False)
    monkeypatch.chdir(foreign)
    monkeypatch.setenv("AICHESTRA_FAKE_PROVIDERS", "1")
    result = bootstrap()
    assert Path(result.repo_root).resolve() == cfg_home.resolve()
    assert machine_local_path(cfg_home).is_file()
    assert not (foreign / ".local").exists()
    assert "ensure_user_config_home" in result.actions


def test_doctor_from_foreign_project_uses_user_config_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aichestra.doctor import run_doctor

    foreign = tmp_path / "app"
    foreign.mkdir()
    (foreign / "pyproject.toml").write_text("[project]\nname='app'\n", encoding="utf-8")
    (foreign / "AGENTS.md").write_text("# app\n", encoding="utf-8")
    cfg_home = tmp_path / "cfg"
    monkeypatch.setenv("AICHESTRA_CONFIG_HOME", str(cfg_home))
    monkeypatch.delenv("AICHESTRA_REPO_ROOT", raising=False)
    monkeypatch.chdir(foreign)
    report = run_doctor()
    aichestra = next(c for c in report.checks if c.name == "aichestra")
    assert aichestra.status.value == "PASS"
    assert str(cfg_home.resolve()) in aichestra.detail
    assert "config root resolved" in aichestra.detail


def test_resolve_config_root_falls_back_to_user_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (foreign / "AGENTS.md").write_text("# x\n", encoding="utf-8")
    cfg_home = tmp_path / "aichestra-cfg"
    monkeypatch.setenv("AICHESTRA_CONFIG_HOME", str(cfg_home))
    monkeypatch.delenv("AICHESTRA_REPO_ROOT", raising=False)
    monkeypatch.chdir(foreign)
    root = resolve_aichestra_config_root(start=foreign)
    assert root == cfg_home.resolve()


def test_user_config_home_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "cfghome"
    monkeypatch.setenv("AICHESTRA_CONFIG_HOME", str(home))
    assert user_config_home() == home.resolve()


def test_settings_menu_uses_discovery_and_preserves_cancel(tmp_path, monkeypatch, capsys):
    import sys
    from aichestra.config.project_settings import interactive_settings, load_project_config
    from aichestra.execution.domain import AgentRuntime, DiscoveryFacts
    init_project(tmp_path, yes=True)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("aichestra.execution.discovery.discover_execution_facts",
                        lambda _: DiscoveryFacts(runtimes=(AgentRuntime("cursor", available=True),)))
    replies = iter(["2", "1", "9"])
    monkeypatch.setattr("builtins.input", lambda _: next(replies))
    interactive_settings(tmp_path)
    assert load_project_config(tmp_path)["roles"]["implement"] == {"runtime": "cursor"}
    out = capsys.readouterr().out
    assert "1. Coordinator" in out
    assert "2. Coding" in out
    assert "8. Agents & Models" in out
    assert "9. Save" in out
    assert "1. Coordinator  2. Coding" not in out
    assert "Choose a runtime for Coding" in out
    assert "Orca model catalog unavailable; add/manage models in Orca." in out
    assert "Coding will use Cursor's default model." in out
    before = load_project_config(tmp_path)
    replies = iter(["6", "2", "1", "0"])
    interactive_settings(tmp_path)
    assert load_project_config(tmp_path) == before


def test_settings_menu_prompts_for_model_when_extras_exist(tmp_path, monkeypatch):
    import sys
    from aichestra.config.project_settings import interactive_settings, load_project_config
    from aichestra.execution.domain import AgentRuntime, DiscoveryFacts
    init_project(tmp_path, yes=True)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        "aichestra.execution.discovery.discover_execution_facts",
        lambda _: DiscoveryFacts(runtimes=(AgentRuntime("cursor", available=True),)),
    )
    monkeypatch.setattr(
        "aichestra.config.project_settings._discovered_model_options",
        lambda runtime, facts, layered: [
            (f"Use {runtime}'s default model", {"runtime": runtime}),
            (
                "ollama/qwen2.5-coder:14b",
                {"runtime": runtime, "provider": "ollama", "model": "qwen2.5-coder:14b"},
            ),
        ],
    )
    replies = iter(["2", "1", "2", "9"])
    monkeypatch.setattr("builtins.input", lambda _: next(replies))
    interactive_settings(tmp_path)
    assert load_project_config(tmp_path)["roles"]["implement"] == {
        "runtime": "cursor",
        "provider": "ollama",
        "model": "qwen2.5-coder:14b",
    }


def test_custom_runtime_settings_and_invalid_mutation_are_atomic(tmp_path):
    from aichestra.config.project_settings import apply_settings_sets
    cfg = {"execution": {"runtimes": {"custom": {}}}, "roles": {"tests": {"runtime": "cursor"}}}
    result = apply_settings_sets(cfg, ["roles.tests=custom"])
    assert result["roles"]["tests"]["runtime"] == "custom"
    with pytest.raises(ValueError):
        apply_settings_sets(cfg, ["roles.tests.runtime=unknown"])
    assert cfg["roles"]["tests"]["runtime"] == "cursor"
