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
    }
    roles = load_role_bindings(cfg)
    assert roles["implement"].runtime == "codex"
    assert roles["tests"].model == "llama3.2"
    assert roles["docs"].runtime == "cursor"  # default
    quota = load_quota_policy(cfg)
    assert quota.mode == "auto"
    assert quota.implement_fallback.runtime == "cursor"


def test_verification_enabled_toggle() -> None:
    from aichestra.orchestration.verification import (
        verification_commands_from_config,
        verification_enabled,
    )

    assert verification_enabled(None) is False
    assert verification_enabled({}) is False
    assert verification_enabled({"verification": {"enabled": False}}) is False
    assert verification_enabled({"verification": {"enabled": True}}) is True
    assert verification_commands_from_config(
        {"verification": {"enabled": False}, "verify": ["pytest"]}
    ) == []
    assert verification_commands_from_config(
        {"verification": {"enabled": True}, "verify": ["pytest", "-q"]}
    ) == [["pytest", "-q"]]


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

    shown = settings_set(
        project,
        [
            "roles.research=cursor",
            "roles.tests.runtime=opencode",
            "roles.tests.model=qwen2.5-coder:14b",
            "quota.mode=auto",
        ],
    )
    assert shown["roles"]["tests"]["runtime"] == "opencode"
    assert shown["roles"]["tests"]["model"] == "qwen2.5-coder:14b"
    assert shown["quota"]["mode"] == "auto"
    again = show_settings(project)
    assert again["roles"]["research"]["runtime"] == "cursor"


def test_user_config_home_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "cfghome"
    monkeypatch.setenv("AICHESTRA_CONFIG_HOME", str(home))
    assert user_config_home() == home.resolve()


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
