"""Named hardware profiles — examples only, never mandatory product requirements."""

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


# Example validation profile for the current Mac — NOT a global product requirement.
M4_PRO_24GB = HardwareProfile(
    id="apple-m4-pro-24gb-example",
    description=(
        "Example profile for Apple M4 Pro / 24 GB unified memory validation. "
        "Not required globally."
    ),
    min_memory_gb=24.0,
    recommended_model_class="~14B",
    max_local_workers=1,
    normal_context_tokens=16_000,
    max_context_tokens=32_000,
)

PROFILES: dict[str, HardwareProfile] = {
    M4_PRO_24GB.id: M4_PRO_24GB,
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
    if memory_gb < 8:
        return PROFILES["weak"]
    if memory_gb < 16:
        return PROFILES["medium"]
    if memory_gb >= 24 and has_gpu:
        # Prefer example M4 profile only as a recommendation class, not identity.
        return HardwareProfile(
            id="capable-unified-24plus",
            description="Capable machine (~24GB+) — ~14B-class optional",
            min_memory_gb=24.0,
            recommended_model_class="~14B",
            max_local_workers=1,
            normal_context_tokens=16_000,
            max_context_tokens=32_000,
        )
    if memory_gb >= 32 and has_gpu:
        return PROFILES["powerful"]
    return PROFILES["medium"]


def profile_to_dict(profile: HardwareProfile) -> dict[str, Any]:
    return asdict(profile)
