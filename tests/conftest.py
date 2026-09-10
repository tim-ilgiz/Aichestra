"""Shared pytest fixtures for Aichestra tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# Force fake-provider mode in CI/local tests — never consume real quota.
os.environ.setdefault("AICHESTRA_FAKE_PROVIDERS", "1")
os.environ.setdefault("AICHESTRA_NO_REAL_QUOTA", "1")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Resolve the Aichestra repository root from this test file location."""
    from aichestra.repo import find_repo_root

    return find_repo_root(Path(__file__).resolve())


@pytest.fixture
def tmp_path_with_spaces(tmp_path: Path) -> Path:
    """Temporary directory whose path contains spaces (portability edge case)."""
    spaced = tmp_path / "clone with spaces" / "nested dir"
    spaced.mkdir(parents=True)
    return spaced


@pytest.fixture
def fake_aichestra_root(tmp_path_with_spaces: Path) -> Path:
    """Minimal fake Aichestra root under a spaced path for isolation tests."""
    root = tmp_path_with_spaces / "Aichestra"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "aichestra"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    (root / ".specify").mkdir()
    policies = root / "policies"
    policies.mkdir()
    (policies / "defaults.json").write_text(
        json.dumps(
            {
                "local": {"enabled": False},
                "providers": {
                    "preferred_lead": "codex",
                    "fallback_lead": "cursor",
                },
                "orchestration": {"mode_default": "native"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def fixture_project_a(repo_root: Path) -> Path:
    return repo_root / "fixtures" / "project_a"


@pytest.fixture
def fixture_project_b(repo_root: Path) -> Path:
    return repo_root / "fixtures" / "project_b"
