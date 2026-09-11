"""Deterministic research_artifact policy for Mode C POLICY_PACKAGE."""

from __future__ import annotations

from aichestra.orchestration.research_compact import (
    RESEARCH_ARTIFACT_COMPACT_CHARS,
    RESEARCH_ARTIFACT_FILE_CHARS,
    RESEARCH_NOTES_PATH,
    resolve_research_artifact,
)
from aichestra.orchestration.speckit_policy import classify_speckit_scale


def test_small_single_step_without_query_is_none() -> None:
    path = classify_speckit_scale(risk="low", estimated_files=1)
    assert (
        resolve_research_artifact(
            speckit_scale=path.scale.value,
            speckit_steps=path.steps,
            explicit_research_query=False,
        )
        == "none"
    )


def test_explicit_query_is_file() -> None:
    path = classify_speckit_scale(risk="low", estimated_files=1)
    assert (
        resolve_research_artifact(
            speckit_scale=path.scale.value,
            speckit_steps=path.steps,
            explicit_research_query=True,
        )
        == "file"
    )


def test_medium_or_large_is_file() -> None:
    medium = classify_speckit_scale(risk="medium")
    large = classify_speckit_scale(touches_security=True)
    assert (
        resolve_research_artifact(
            speckit_scale=medium.scale.value, speckit_steps=medium.steps
        )
        == "file"
    )
    assert (
        resolve_research_artifact(
            speckit_scale=large.scale.value, speckit_steps=large.steps
        )
        == "file"
    )


def test_resume_or_audit_is_file() -> None:
    assert (
        resolve_research_artifact(speckit_scale="small", resume_or_audit=True) == "file"
    )


def test_summary_thresholds() -> None:
    assert (
        resolve_research_artifact(
            speckit_scale="small",
            expected_summary_chars=RESEARCH_ARTIFACT_FILE_CHARS,
        )
        == "file"
    )
    assert (
        resolve_research_artifact(
            speckit_scale="small",
            expected_summary_chars=RESEARCH_ARTIFACT_COMPACT_CHARS,
        )
        == "compacted"
    )


def test_cloud_handoff_wins_over_small() -> None:
    assert (
        resolve_research_artifact(speckit_scale="small", cloud_handoff=True)
        == "compacted"
    )


def test_cloud_handoff_signal_requires_local_research_path() -> None:
    """local_enabled alone must not imply local→cloud research handoff."""
    from dataclasses import replace

    from aichestra.execution.domain import Locality
    from aichestra.orchestration.workflow import Mode, ModeCRunController, WorkflowBindings
    from tests.fakes.providers import fake_execution_targets

    cloud = fake_execution_targets()[0]
    local = replace(cloud, locality=Locality.LOCAL)
    ctrl = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        bindings=WorkflowBindings(
            execution_targets=fake_execution_targets(),
            local_enabled=True,
            task_prompt="fix one-line typo",
        ),
    )
    ctrl._coordinator_target = cloud

    # Capability + cloud coordinator, no research role → not a handoff.
    assert ctrl._research_cloud_handoff_signal() is False

    # Local research bound but SMALL without query → research not expected.
    ctrl._resolved_roles = {"research": local, "implement": cloud}
    assert ctrl._research_expected_for_task() is False
    assert ctrl._research_cloud_handoff_signal() is False

    # Explicit research path + local research + cloud consumer → compacted.
    ctrl.bindings.research_query = "auth module"
    assert ctrl._research_expected_for_task() is True
    assert ctrl._research_cloud_handoff_signal() is True
    assert (
        resolve_research_artifact(
            speckit_scale="small",
            explicit_research_query=True,
            cloud_handoff=True,
        )
        == "compacted"
    )

    # Cloud research consumer path is not a local→cloud handoff.
    ctrl._resolved_roles = {"research": cloud, "implement": cloud}
    assert ctrl._research_cloud_handoff_signal() is False


def test_research_disabled_forces_none() -> None:
    assert (
        resolve_research_artifact(
            speckit_scale="medium",
            explicit_research_query=True,
            cloud_handoff=True,
            research_useful=False,
        )
        == "none"
    )


def test_configured_role_bindings_alone_do_not_force_file() -> None:
    """Defaults always list research+implement; that is not multi-dispatch."""
    path = classify_speckit_scale(risk="low", estimated_files=1)
    assert "research" not in path.steps
    assert (
        resolve_research_artifact(
            speckit_scale=path.scale.value,
            speckit_steps=path.steps,
            multi_worker_roles=False,
        )
        == "none"
    )


def test_notes_path_constant() -> None:
    assert RESEARCH_NOTES_PATH == ".aichestra/research_notes.md"
