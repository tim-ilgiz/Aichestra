"""Mode C entry: project context + one Orca Run + deterministic gates.

Canonical Mode C lifecycle (``ModeCRunController.run_all``):

```text
task + project root
→ ProjectContext discovery
→ provider/capability policy
→ bounded context + policy package
→ exactly one Orca Run
→ handoff objective/context to Orca (mode_c_handoff)
→ Orca owns tasks/workers/dependencies/handoffs/worktrees
→ deterministic Aichestra gates (maintenance, verification)
→ Mode C result from Orca Run state + gate results
```

Aichestra MUST NOT hard-code research → implement → writers → review.
Aichestra MUST NOT call Codex/Cursor/local-worker ``execute_task`` for Mode C
agent roles, and MUST NOT create additional Orca Runs or synthesize run ids.
"""

from __future__ import annotations

from aichestra.orchestration.workflow import (
    MODE_C_HANDOFF_ROLE,
    GateKind,
    GateOutcome,
    GateStatus,
    ModeCPolicyPackage,
    ModeCRunController,
    Phase,
    PhaseOutcome,
    PhaseStatus,
    WorkflowBindings,
    WorkflowState,
    default_phases,
    phases_for_scale,
)

__all__ = [
    "MODE_C_HANDOFF_ROLE",
    "GateKind",
    "GateOutcome",
    "GateStatus",
    "ModeCPolicyPackage",
    "ModeCRunController",
    "Phase",
    "PhaseOutcome",
    "PhaseStatus",
    "WorkflowBindings",
    "WorkflowState",
    "default_phases",
    "phases_for_scale",
]
