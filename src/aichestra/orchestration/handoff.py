"""Codex→Cursor handoff with bounded context inside an existing Orca Run.

Automatic quota fallback policy (FR-036)
----------------------------------------
``automatic_quota_fallback_reliable`` is **False** in v1.

Current Orca primitives do not yet make automatic Codex→Cursor quota fallback
reliably detectable without fragile stderr matching. Therefore Aichestra
implements a **one-action manual handoff** by default: the operator triggers a
single handoff action that continues Cursor under the **same** Orca Run when a
``run_id`` is known — not a detached ``worktree create --no-parent`` that
escapes the Mode C Run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Documented v1 policy: manual one-action handoff (FR-036).
automatic_quota_fallback_reliable: bool = False


@dataclass
class HandoffPacket:
    """Bounded continuation context for Codex→Cursor handoff (FR-035)."""

    original_request: str
    accepted_decisions: list[str] = field(default_factory=list)
    repo_path: str = ""
    worktree_path: str = ""
    git_status: str = ""
    git_diff: str = ""
    workflow_phase: str = ""
    completed_work: list[str] = field(default_factory=list)
    remaining_work: list[str] = field(default_factory=list)
    known_failures: list[str] = field(default_factory=list)
    next_action: str = ""
    compacted_research: dict[str, Any] = field(default_factory=dict)
    source_lead: str = "codex"
    target_lead: str = "cursor"
    orca_run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_handoff_packet(**kwargs: Any) -> HandoffPacket:
    """Build a bounded handoff packet; strips oversized transcript fields."""
    for key in ("full_transcript", "raw_transcript", "messages"):
        kwargs.pop(key, None)
    packet = HandoffPacket(
        **{k: v for k, v in kwargs.items() if k in HandoffPacket.__dataclass_fields__}
    )
    packet.git_diff = _truncate(packet.git_diff, 50_000)
    packet.git_status = _truncate(packet.git_status, 10_000)
    return packet


def prepare_manual_handoff(
    packet: HandoffPacket,
    *,
    execute: bool = False,
    orca_binary: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Return a one-action handoff payload; optionally continue inside the Run.

    When ``run_id`` (or ``packet.orca_run_id``) is known, the suggested command
    reuses that Orca Run via ``run-use`` + ``task-create`` + ``worker-start``
    with ``--agent cursor``. Detached ``worktree create --no-parent`` is only a
    last-resort hint when no Run id exists (non-Mode-C / recovery).
    """
    brief = _format_handoff_brief(packet)
    resolved_run = (run_id or packet.orca_run_id or "").strip()
    worktree = (packet.worktree_path or "current").strip() or "current"
    name = "aichestra-handoff"

    if resolved_run:
        suggested = (
            f"orca orchestration run-use --id {resolved_run} --json && "
            f"orca orchestration task-create --spec <bounded-brief> "
            f"--task-title {name} --run {resolved_run} --json && "
            f"orca orchestration worker-start --task <task-id> "
            f"--worktree {worktree} --name {name} --agent cursor "
            f"--setup skip --json"
        )
        instruction = (
            "One-action handoff inside the existing Orca Run: run-use the Mode C "
            "run_id, create a Cursor handoff task bound with --run, worker-start "
            "with --agent cursor. Do not create a detached --no-parent worktree "
            "that leaves the Run. Automatic quota fallback is not enabled in v1."
        )
    else:
        suggested = (
            "orca orchestration run-use --id <run_id> --json && "
            "orca orchestration task-create --spec <bounded-brief> "
            f"--task-title {name} --run <run_id> --json && "
            "orca orchestration worker-start --task <task-id> "
            f"--worktree {worktree} --name {name} --agent cursor "
            "--setup skip --json"
        )
        instruction = (
            "One-action handoff requires an Orca run_id to stay inside Mode C. "
            "Supply --run-id / packet.orca_run_id (or rely on recent Mode C run "
            "auto-resolve) before execute. Automatic quota fallback is not "
            "enabled in v1."
        )

    payload: dict[str, Any] = {
        "mode": "manual_one_action",
        "automatic_quota_fallback_reliable": automatic_quota_fallback_reliable,
        "instruction": instruction,
        "suggested_orca_command": suggested,
        "bounded_brief": brief,
        "packet": packet.to_dict(),
        "orca_run_id": resolved_run or None,
        "preserves_orca_run": bool(resolved_run),
        "executed": False,
    }
    if not execute:
        return payload

    if not resolved_run:
        payload["execute_error"] = (
            "handoff execute requires orca_run_id to continue inside the Mode C Run"
        )
        return payload

    binary = orca_binary
    if not binary:
        try:
            from aichestra.providers.orca import resolve_orca_binary

            binary = resolve_orca_binary()
        except Exception:  # noqa: BLE001
            binary = None
    if not binary:
        payload["execute_error"] = "Orca binary not found; handoff packet prepared only"
        return payload

    import subprocess

    def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )

    use = _run([binary, "orchestration", "run-use", "--id", resolved_run, "--json"])
    if use.returncode != 0:
        payload["executed"] = False
        payload["orca_exit_code"] = use.returncode
        payload["orca_stderr"] = (use.stderr or "")[:2000]
        payload["execute_error"] = f"orca run-use exited {use.returncode}"
        return payload

    create = _run(
        [
            binary,
            "orchestration",
            "task-create",
            "--spec",
            brief,
            "--task-title",
            name,
            "--run",
            resolved_run,
            "--json",
        ]
    )
    payload["orca_stdout"] = (create.stdout or "")[:4000]
    payload["orca_stderr"] = (create.stderr or "")[:2000]
    payload["orca_exit_code"] = create.returncode
    if create.returncode != 0:
        payload["executed"] = False
        payload["execute_error"] = (
            f"orca task-create exited {create.returncode} "
            f"(fail-closed same-run binding via --run {resolved_run})"
        )
        return payload

    task_id = _extract_id(create.stdout or "")
    if not task_id:
        payload["executed"] = False
        payload["execute_error"] = "orca task-create returned no task id"
        return payload

    start = _run(
        [
            binary,
            "orchestration",
            "worker-start",
            "--task",
            task_id,
            "--worktree",
            worktree,
            "--name",
            name,
            "--agent",
            "cursor",
            "--setup",
            "skip",
            "--json",
        ]
    )
    payload["orca_argv"] = [
        "orchestration",
        "run-use",
        "--id",
        resolved_run,
        "&&",
        "task-create",
        "--task-title",
        name,
        "&&",
        "worker-start",
        "--agent",
        "cursor",
        "--task",
        task_id,
    ]
    payload["orca_stdout"] = (start.stdout or create.stdout or "")[:4000]
    payload["orca_stderr"] = (start.stderr or "")[:2000]
    payload["orca_exit_code"] = start.returncode
    payload["task_id"] = task_id
    payload["executed"] = start.returncode == 0
    if start.returncode != 0:
        payload["execute_error"] = f"orca worker-start exited {start.returncode}"
    return payload


def _extract_id(stdout: str) -> str | None:
    import json
    import re

    text = (stdout or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("id", "taskId", "task_id"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            result = data.get("result")
            if isinstance(result, dict):
                for key in ("id", "taskId", "task_id"):
                    value = result.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
    except json.JSONDecodeError:
        pass
    match = re.search(r'"(?:id|taskId|task_id)"\s*:\s*"([^"]+)"', text)
    if match:
        return match.group(1)
    return None


def _format_handoff_brief(packet: HandoffPacket) -> str:
    parts = [
        f"ORIGINAL_REQUEST: {packet.original_request}",
        f"PHASE: {packet.workflow_phase}",
        f"REPO: {packet.repo_path}",
        f"WORKTREE: {packet.worktree_path}",
        f"ORCA_RUN_ID: {packet.orca_run_id}",
        f"NEXT: {packet.next_action}",
    ]
    if packet.accepted_decisions:
        parts.append("DECISIONS: " + "; ".join(packet.accepted_decisions[:20]))
    if packet.completed_work:
        parts.append("DONE: " + "; ".join(packet.completed_work[:20]))
    if packet.remaining_work:
        parts.append("REMAINING: " + "; ".join(packet.remaining_work[:20]))
    if packet.known_failures:
        parts.append("FAILURES: " + "; ".join(packet.known_failures[:20]))
    if packet.git_status:
        parts.append("GIT_STATUS:\n" + _truncate(packet.git_status, 4000))
    if packet.git_diff:
        parts.append("GIT_DIFF:\n" + _truncate(packet.git_diff, 8000))
    if packet.compacted_research:
        parts.append("RESEARCH: " + str(packet.compacted_research)[:4000])
    return "\n".join(parts)


def should_auto_fallback_on_quota() -> bool:
    """Automatic Codex→Cursor quota fallback is disabled unless reliable."""
    return automatic_quota_fallback_reliable


def resolve_recent_mode_c_run_id(
    *,
    project_root: Path | str | None = None,
    max_age_seconds: float = 86_400.0,
) -> str | None:
    """Best-effort resolve of a recent Mode C run_id from project metadata.

    Looks for ``.aichestra/last_mode_c_run.json`` written by Mode C completion.
    Returns None when missing, stale, or unreadable (caller must require
    ``--run-id`` or fail closed on execute).
    """
    import json
    import time

    if project_root is None:
        return None
    path = Path(project_root) / ".aichestra" / "last_mode_c_run.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    run_id = data.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return None
    ts = data.get("updated_at")
    if isinstance(ts, (int, float)):
        if time.time() - float(ts) > max_age_seconds:
            return None
    return run_id.strip()


def record_mode_c_run_id(project_root: Path | str | None, run_id: str) -> None:
    """Persist last Mode C run_id for reliable handoff auto-resolve."""
    import json
    import time

    if not project_root or not run_id.strip():
        return
    root = Path(project_root)
    dest = root / ".aichestra" / "last_mode_c_run.json"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            json.dumps(
                {"run_id": run_id.strip(), "updated_at": time.time()},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…[truncated for handoff bound]"
