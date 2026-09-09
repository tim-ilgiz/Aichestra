"""Codex→Cursor handoff with bounded context.

Automatic quota fallback policy (FR-036)
----------------------------------------
``automatic_quota_fallback_reliable`` is **False** in v1.

Current Orca primitives do not yet make automatic Codex→Cursor quota fallback
reliably detectable without fragile stderr matching. Therefore Aichestra
implements a **one-action manual handoff** by default: the operator triggers a
single handoff action that transfers a bounded packet (not a full transcript)
to Cursor.

If future Orca primitives make automatic fallback reliable, this flag may be
revisited — do not hard-code brittle quota-string matching alone.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_handoff_packet(**kwargs: Any) -> HandoffPacket:
    """Build a bounded handoff packet; strips oversized transcript fields."""
    # Reject accidental full-transcript dumps.
    for key in ("full_transcript", "raw_transcript", "messages"):
        kwargs.pop(key, None)
    packet = HandoffPacket(**{
        k: v for k, v in kwargs.items() if k in HandoffPacket.__dataclass_fields__
    })
    # Bound large text fields.
    packet.git_diff = _truncate(packet.git_diff, 50_000)
    packet.git_status = _truncate(packet.git_status, 10_000)
    return packet


def prepare_manual_handoff(packet: HandoffPacket) -> dict[str, Any]:
    """Return a one-action manual handoff payload for the operator.

    Prefer Orca's native full-handoff primitive when Orca is available::

        orca worktree create --name <task> --no-parent --agent cursor --prompt <brief> --json

    The bounded packet is serialized into the prompt brief — not a full transcript.
    """
    brief = _format_handoff_brief(packet)
    suggested = (
        "orca worktree create --name aichestra-handoff --no-parent "
        "--agent cursor --prompt <bounded-brief> --json"
    )
    return {
        "mode": "manual_one_action",
        "automatic_quota_fallback_reliable": automatic_quota_fallback_reliable,
        "instruction": (
            "One-action handoff: create an Orca worktree/terminal for Cursor with "
            "the bounded brief, then stop monitoring (full handoff). Automatic "
            "quota fallback is not enabled in v1."
        ),
        "suggested_orca_command": suggested,
        "bounded_brief": brief,
        "packet": packet.to_dict(),
    }


def _format_handoff_brief(packet: HandoffPacket) -> str:
    parts = [
        f"ORIGINAL_REQUEST: {packet.original_request}",
        f"PHASE: {packet.workflow_phase}",
        f"REPO: {packet.repo_path}",
        f"WORKTREE: {packet.worktree_path}",
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


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…[truncated for handoff bound]"
