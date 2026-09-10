"""Unit tests: cross-platform path / precedence handling."""

from __future__ import annotations

import json
from pathlib import Path

from aichestra.config.layering import deep_merge, resolve_config
from aichestra.repo import find_repo_root


def test_deep_merge_nested() -> None:
    merged = deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"c": 3, "d": 4}})
    assert merged == {"a": {"b": 1, "c": 3, "d": 4}}


def test_resolve_from_spaced_clone(fake_aichestra_root: Path) -> None:
    cfg = resolve_config(
        repo_root=fake_aichestra_root,
        machine_local={"local": {"enabled": False}},
        runtime_override={"orchestration": {"mode_default": "orchestrated"}},
    )
    assert cfg["local"]["enabled"] is False
    assert cfg["orchestration"]["mode_default"] == "orchestrated"


def test_find_repo_root_from_file_path(repo_root: Path) -> None:
    file_start = repo_root / "src" / "aichestra" / "repo.py"
    assert find_repo_root(file_start) == repo_root


def test_line_ending_agnostic_json(tmp_path: Path, fake_aichestra_root: Path) -> None:
    ml = fake_aichestra_root / ".local"
    ml.mkdir(exist_ok=True)
    (ml / "machine.local.json").write_bytes(
        b'{\r\n  "local": {\r\n    "enabled": true\r\n  }\r\n}\r\n'
    )
    cfg = resolve_config(repo_root=fake_aichestra_root)
    assert cfg["local"]["enabled"] is True
