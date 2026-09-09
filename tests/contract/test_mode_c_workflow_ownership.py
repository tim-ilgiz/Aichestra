"""T165/T166: workflow ownership + project instruction authority contracts."""

from __future__ import annotations

import sys
from pathlib import Path

from aichestra.orchestration.modes import Mode
from aichestra.orchestration.project_context import discover_project_context
from aichestra.orchestration.workflow import (
    MODE_C_HANDOFF_ROLE,
    GateKind,
    ModeCRunController,
    WorkflowBindings,
)
from tests.fakes.providers import fake_codex, fake_orca


def _bindings(root: Path, *, prompt: str, orca) -> WorkflowBindings:
    return WorkflowBindings(
        orca=orca,
        lead=fake_codex("success"),
        project_root=str(root),
        task_prompt=prompt,
        maintenance_kwargs={"change_summary": "x", "touches_behavior": False},
        verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
    )


def test_t165_two_projects_different_workflow_shapes_same_controller(
    tmp_path: Path,
) -> None:
    """MODE-C-015 / T165: different Orca workflow shapes without Phase changes.

    Two projects/tasks hand off through the same Python controller gates. Fake
    Orca returns different ``simulated_workflow_shape`` values. No Aichestra
    Phase/scheduler branch is required for the shape difference.
    """
    project_a = tmp_path / "project_a"
    project_b = tmp_path / "project_b"
    project_a.mkdir()
    project_b.mkdir()
    (project_a / "AGENTS.md").write_text(
        "# Project A agents\nPrefer local research.\n",
        encoding="utf-8",
    )

    orca_a = fake_orca("success")
    orca_b = fake_orca("success")

    wf_a = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_bindings(project_a, prompt="fix typo", orca=orca_a),
    )
    wf_b = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_bindings(
            project_b,
            prompt="refactor across 12 files in multi-package monorepo",
            orca=orca_b,
        ),
    )

    state_a = wf_a.run_all()
    state_b = wf_b.run_all()
    assert not state_a.failed, state_a.failed
    assert not state_b.failed, state_b.failed

    # Same Aichestra control-plane shape (gates + one handoff).
    assert state_a.completed == state_b.completed
    assert GateKind.ORCA_HANDOFF.value in state_a.completed
    roles_a = [(r.role or "") for r in orca_a.sent]
    roles_b = [(r.role or "") for r in orca_b.sent]
    assert roles_a.count(MODE_C_HANDOFF_ROLE) == 1
    assert roles_b.count(MODE_C_HANDOFF_ROLE) == 1
    for forbidden in (
        "research",
        "lead_implement",
        "mode_c_agents",
        "mode_c_writers",
        "lead_review",
    ):
        assert forbidden not in roles_a
        assert forbidden not in roles_b

    handoff_a = next(r for r in orca_a.sent if (r.role or "") == MODE_C_HANDOFF_ROLE)
    handoff_b = next(r for r in orca_b.sent if (r.role or "") == MODE_C_HANDOFF_ROLE)
    assert (handoff_a.context or {}).get("project_context", {}).get("agents_files")
    assert not (handoff_b.context or {}).get("project_context", {}).get("agents_files")
    receipt_a = state_a.metadata.get("orca_mode_c_handoff", {}).get("provider", {})
    receipt_b = state_b.metadata.get("orca_mode_c_handoff", {}).get("provider", {})
    shape_a = (receipt_a.get("metadata") or {}).get("simulated_workflow_shape")
    shape_b = (receipt_b.get("metadata") or {}).get("simulated_workflow_shape")
    assert shape_a == "project_instruction_driven"
    assert shape_b == "speckit_medium"
    assert shape_a != shape_b

    # Prove controller class / gate set did not grow Phase enum members.
    from aichestra.orchestration.workflow import GateKind as GK

    assert not hasattr(GK, "RESEARCH")
    assert not hasattr(GK, "LEAD_IMPLEMENT")
    assert not hasattr(GK, "TEST_WRITER")
    assert not hasattr(GK, "DOC_WRITER")
    assert not hasattr(GK, "LEAD_REVIEW")


def test_t166_project_instructions_discovered_and_authoritative(
    tmp_path: Path,
) -> None:
    """MODE-C-013/014 / T166: project AGENTS + Spec Kit passed to Orca."""
    root = tmp_path / "owned"
    root.mkdir()
    (root / "AGENTS.md").write_text(
        "# Authoritative agents\nNever invent APIs.\n",
        encoding="utf-8",
    )
    (root / ".specify").mkdir()
    (root / "specs").mkdir()
    (root / ".factory").mkdir()

    ctx = discover_project_context(root)
    assert str(root / "AGENTS.md") in ctx.agents_files
    assert ctx.speckit is not None
    assert ctx.speckit.owns_canonical is True
    assert ctx.speckit.compatibility_dir is None
    assert ctx.factory.get("present") is True
    assert "project_instructions" in ctx.precedence

    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=_bindings(root, prompt="implement safely", orca=orca),
    )
    state = wf.run_all()
    assert not state.failed, state.failed

    pc = state.metadata.get("project_context") or {}
    assert pc.get("agents_files")
    assert pc.get("speckit", {}).get("owns_canonical") is True
    assert "Never invent APIs" in " ".join(
        (pc.get("instruction_excerpts") or {}).values()
    )

    handoff = next(r for r in orca.sent if (r.role or "") == MODE_C_HANDOFF_ROLE)
    handed = (handoff.context or {}).get("project_context") or {}
    assert handed.get("agents_files")
    assert handed.get("speckit", {}).get("owns_canonical") is True
    package = state.metadata.get("mode_c_policy_package") or {}
    assert package.get("project_context", {}).get("speckit", {}).get("owns_canonical")

    # Must not create competing Spec Kit under .aichestra/speckit/.
    assert not (root / ".aichestra" / "speckit").exists()
    assert state.metadata.get("speckit_canonical", {}).get(
        "competing_aichestra_speckit_forbidden"
    )
