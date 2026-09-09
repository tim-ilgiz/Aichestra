"""Mode C entry: thin Orca-Run coordinator + deterministic gates.

Canonical Mode C lifecycle (``ModeCRunController.run_all``):

```text
validate project root + Orca
→ classify / Spec Kit policy + real artifacts (local)
→ ensure exactly ONE Orca Run
→ ONE mode_c_agents Orca handoff (research+implement under that run_id)
→ maintenance-reviewer (local)
→ optional writers via Orca (same run)
→ verification (local; config or safe auto-detect)
→ final lead review via Orca (same run)
```

``run_phase`` may run local gates / early-fail checks only. It refuses
per-phase agent worker scheduling (anti dual-orchestrator).

Aichestra MUST NOT call Codex/Cursor/local-worker ``execute_task`` for Mode C
agent roles, and MUST NOT create additional Orca Runs or synthesize run ids.
"""

from __future__ import annotations

from aichestra.orchestration.workflow import (
    ModeCPolicyPackage,
    ModeCRunController,
    OrchestratedWorkflow,
    Phase,
    PhaseOutcome,
    PhaseStatus,
    WorkflowBindings,
    WorkflowState,
    default_phases,
    phases_for_scale,
)

__all__ = [
    "ModeCPolicyPackage",
    "ModeCRunController",
    "OrchestratedWorkflow",
    "Phase",
    "PhaseOutcome",
    "PhaseStatus",
    "WorkflowBindings",
    "WorkflowState",
    "default_phases",
    "phases_for_scale",
]
