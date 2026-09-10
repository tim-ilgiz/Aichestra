"""Contract: Mode C POLICY_PACKAGE carries role_bindings + quota_policy."""

from __future__ import annotations

from pathlib import Path

from aichestra.orchestration.modes import Mode
from aichestra.orchestration.workflow import ModeCPolicyPackage, ModeCRunController, WorkflowBindings


def test_policy_package_includes_role_bindings() -> None:
    bindings = WorkflowBindings(
        project_root="/tmp/proj",
        task_prompt="demo",
        role_bindings={
            "implement": {"runtime": "codex"},
            "research": {"runtime": "cursor"},
            "tests": {"runtime": "opencode", "model": "qwen2.5-coder:14b"},
            "docs": {"runtime": "cursor"},
        },
        quota_policy={
            "mode": "manual",
            "implement_fallback": {"runtime": "cursor"},
        },
    )
    ctl = ModeCRunController(
        mode=Mode.ORCHESTRATED, research_useful=False, bindings=bindings
    )
    ctl.state.metadata["project_context"] = {"project_root": "/tmp/proj"}
    data = ctl._build_policy_package("run_test").to_dict()
    assert data["role_bindings"]["implement"]["runtime"] == "codex"
    assert data["role_bindings"]["tests"]["model"] == "qwen2.5-coder:14b"
    assert data["quota_policy"]["mode"] == "manual"


def test_orca_preamble_mentions_role_bindings() -> None:
    src = (
        Path(__file__).resolve().parents[2] / "src/aichestra/providers/orca.py"
    ).read_text(encoding="utf-8")
    assert "role_bindings" in src
    assert "quota_policy" in src
    assert "aichestra settings" in src


def test_mode_c_policy_package_dataclass_fields() -> None:
    pkg = ModeCPolicyPackage(
        run_id="r1",
        task_prompt="t",
        research_query="q",
        project_root="/p",
        project_context={},
        role_bindings={"implement": {"runtime": "codex"}},
        quota_policy={
            "mode": "auto",
            "implement_fallback": {"runtime": "cursor"},
        },
    )
    d = pkg.to_dict()
    assert d["role_bindings"]["implement"]["runtime"] == "codex"
    assert d["quota_policy"]["mode"] == "auto"
