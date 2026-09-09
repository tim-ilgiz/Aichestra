"""Unit tests: repo root and config layering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aichestra.config.layering import (
    local_enabled,
    machine_local_path,
    resolve_config,
    save_machine_local,
)
from aichestra.repo import find_repo_root


def test_find_repo_root_from_tests(repo_root: Path) -> None:
    assert (repo_root / "pyproject.toml").is_file()
    assert (repo_root / "AGENTS.md").is_file()
    assert find_repo_root(repo_root / "tests" / "conftest.py") == repo_root


def test_find_repo_root_arbitrary_spaced_path(fake_aichestra_root: Path) -> None:
    nested = fake_aichestra_root / "src" / "deep"
    nested.mkdir(parents=True)
    assert " " in str(fake_aichestra_root)
    assert find_repo_root(nested) == fake_aichestra_root.resolve()


def test_config_layering_precedence(fake_aichestra_root: Path) -> None:
    save_machine_local(
        {"local": {"enabled": True}, "providers": {"note": "machine"}},
        repo_root=fake_aichestra_root,
    )
    project = fake_aichestra_root / "proj"
    (project / ".aichestra").mkdir(parents=True)
    (project / ".aichestra" / "project.json").write_text(
        json.dumps({"project_id": "p1", "providers": {"project_flag": True}}) + "\n",
        encoding="utf-8",
    )
    cfg = resolve_config(
        repo_root=fake_aichestra_root,
        project_root=project,
        runtime_override={"runtime": {"task": "x"}},
    )
    assert cfg["local"]["enabled"] is True
    assert cfg["providers"]["preferred_lead"] == "codex"
    assert cfg["providers"]["note"] == "machine"
    assert cfg["providers"]["project_flag"] is True
    assert cfg["runtime"]["task"] == "x"
    assert local_enabled(cfg) is True


def test_machine_local_path_under_dot_local(fake_aichestra_root: Path) -> None:
    path = machine_local_path(fake_aichestra_root)
    assert path.name == "machine.local.json"
    assert path.parent.name == ".local"


def test_defaults_local_disabled(repo_root: Path) -> None:
    cfg = resolve_config(repo_root=repo_root, machine_local={})
    assert cfg["local"]["enabled"] is False
