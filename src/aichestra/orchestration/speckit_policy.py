"""Spec Kit proportionality — SMALL / MEDIUM / LARGE-HIGH-RISK (FR-028/063)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SpecKitScale(str, Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE_HIGH_RISK = "large_high_risk"


@dataclass(frozen=True)
class SpecKitPath:
    scale: SpecKitScale
    steps: tuple[str, ...]
    rationale: str


_SMALL_STEPS = (
    "direct_implementation",
    "maintenance_review",
    "verification",
)
_MEDIUM_STEPS = (
    "short_brief",
    "research",
    "plan",
    "implementation",
    "maintenance_review",
)
_LARGE_STEPS = (
    "specify",
    "clarify",
    "plan",
    "tasks",
    "implementation",
    "maintenance_review",
    "verification",
)


def classify_speckit_scale(
    *,
    risk: str = "low",
    touches_security: bool = False,
    touches_money: bool = False,
    multi_package: bool = False,
    estimated_files: int = 1,
    user_requested_full_sdd: bool = False,
) -> SpecKitPath:
    """Choose proportional Spec Kit path; do not force full SDD for tiny changes."""
    risk_l = risk.lower().strip()
    if user_requested_full_sdd or touches_security or touches_money or risk_l in {
        "high",
        "critical",
        "large",
    }:
        return SpecKitPath(
            scale=SpecKitScale.LARGE_HIGH_RISK,
            steps=_LARGE_STEPS,
            rationale="high-risk/security/money or explicit full SDD request",
        )
    if multi_package or estimated_files >= 8 or risk_l == "medium":
        return SpecKitPath(
            scale=SpecKitScale.MEDIUM,
            steps=_MEDIUM_STEPS,
            rationale="medium scope — brief → research → plan → implement → review",
        )
    return SpecKitPath(
        scale=SpecKitScale.SMALL,
        steps=_SMALL_STEPS,
        rationale="small change — direct implementation path",
    )


def ai_factory_required_for_aichestra_v1() -> bool:
    """AI Factory is not added to Aichestra v1 (FR-063)."""
    return False
