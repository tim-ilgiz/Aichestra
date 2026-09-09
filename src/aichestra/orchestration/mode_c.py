"""Mode C entry: thin Orca-Run controller + deterministic gates.

Canonical Mode C lifecycle:

```text
validate project root + Orca
→ classify / Spec Kit policy (local)
→ ensure exactly ONE Orca Run
→ dispatch agent work as Orca tasks under that run_id
→ maintenance-reviewer + verification (local gates)
→ feed results / final review via the same Run
```

Aichestra MUST NOT call Codex/Cursor/local-worker ``execute_task`` for Mode C
agent roles, and MUST NOT create additional Orca Runs for phase reporting.
"""

from __future__ import annotations

from aichestra.orchestration.workflow import (
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
