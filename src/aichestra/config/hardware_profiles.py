"""Capability-tier hardware profiles — no host-identity product profiles.

Named Mac examples (e.g. M4 Pro 24GB) belong in test fixtures only, never in
production configuration or routing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class HardwareProfile:
    id: str
    description: str
    min_memory_gb: float
    recommended_model_class: str
    max_local_workers: int
    normal_context_tokens: int
    max_context_tokens: int


PROFILES: dict[str, HardwareProfile] = {
    "weak": HardwareProfile(
        id="weak",
        description="Weak machine — prefer local disabled",
        min_memory_gb=0.0,
        recommended_model_class="none",
        max_local_workers=0,
        normal_context_tokens=0,
        max_context_tokens=0,
    ),
    "medium": HardwareProfile(
        id="medium",
        description="Medium machine — small/medium local model possible",
        min_memory_gb=8.0,
        recommended_model_class="~7B-8B",
        max_local_workers=1,
        normal_context_tokens=8_000,
        max_context_tokens=16_000,
    ),
    "capable-unified-24plus": HardwareProfile(
        id="capable-unified-24plus",
        description="Capable machine (~24GB+ with GPU) — ~14B-class optional",
        min_memory_gb=24.0,
        recommended_model_class="~14B",
        max_local_workers=1,
        normal_context_tokens=16_000,
        max_context_tokens=32_000,
    ),
    "powerful": HardwareProfile(
        id="powerful",
        description="Powerful GPU workstation — larger model possible",
        min_memory_gb=32.0,
        recommended_model_class="~30B+",
        max_local_workers=1,
        normal_context_tokens=16_000,
        max_context_tokens=32_000,
    ),
}


def suggest_profile(memory_gb: float, *, has_gpu: bool = False) -> HardwareProfile:
    """Capability-based tier selection.

    Order matters: ``powerful`` (>=32 + GPU) MUST be checked before
    ``capable-unified-24plus`` (>=24 + GPU), otherwise 32+ machines never reach
    the powerful tier.
    """
    if memory_gb < 8:
        return PROFILES["weak"]
    if memory_gb < 16:
        return PROFILES["medium"]
    if memory_gb >= 32 and has_gpu:
        return PROFILES["powerful"]
    if memory_gb >= 24 and has_gpu:
        return PROFILES["capable-unified-24plus"]
    return PROFILES["medium"]


def profile_to_dict(profile: HardwareProfile) -> dict[str, Any]:
    return asdict(profile)
