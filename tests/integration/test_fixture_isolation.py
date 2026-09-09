"""Integration: fixture project isolation."""

from __future__ import annotations

import json
from pathlib import Path

from aichestra.config.layering import resolve_config


def test_fixture_projects_have_distinct_ids(
    fixture_project_a: Path, fixture_project_b: Path
) -> None:
    a = json.loads(
        (fixture_project_a / ".aichestra" / "project.json").read_text(encoding="utf-8")
    )
    b = json.loads(
        (fixture_project_b / ".aichestra" / "project.json").read_text(encoding="utf-8")
    )
    assert a["project_id"] != b["project_id"]
    assert a["stack"] != b["stack"]


def test_config_does_not_leak_between_projects(
    repo_root: Path, fixture_project_a: Path, fixture_project_b: Path
) -> None:
    cfg_a = resolve_config(
        repo_root=repo_root,
        project_root=fixture_project_a,
        machine_local={},
        runtime_override={"task": {"id": "task-a"}},
    )
    cfg_b = resolve_config(
        repo_root=repo_root,
        project_root=fixture_project_b,
        machine_local={},
        runtime_override={"task": {"id": "task-b"}},
    )
    assert cfg_a["project_id"] == "fixture-project-a"
    assert cfg_b["project_id"] == "fixture-project-b"
    assert cfg_a["task"]["id"] == "task-a"
    assert cfg_b["task"]["id"] == "task-b"
    assert cfg_a["task"]["id"] != cfg_b["task"]["id"]
