"""Local inference resource policy — prefer one worker; never kill user apps."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ResourcePressure(str, Enum):
    OK = "ok"
    ELEVATED = "elevated"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class ResourcePolicy:
    max_local_workers: int = 1
    unload_idle_models: bool = True
    may_terminate_user_apps: bool = False  # always False by design
    disable_local_under_critical_pressure: bool = True


@dataclass(frozen=True)
class ResourceAssessment:
    pressure: ResourcePressure
    recommended_workers: int
    local_recommended: bool
    actions: tuple[str, ...]
    policy: ResourcePolicy

    def to_dict(self) -> dict[str, Any]:
        return {
            "pressure": self.pressure.value,
            "recommended_workers": self.recommended_workers,
            "local_recommended": self.local_recommended,
            "actions": list(self.actions),
            "policy": {
                "max_local_workers": self.policy.max_local_workers,
                "unload_idle_models": self.policy.unload_idle_models,
                "may_terminate_user_apps": self.policy.may_terminate_user_apps,
                "disable_local_under_critical_pressure": (
                    self.policy.disable_local_under_critical_pressure
                ),
            },
        }


def assess_resources(
    *,
    memory_total_gb: float,
    memory_available_gb: float | None = None,
    policy: ResourcePolicy | None = None,
    active_local_workers: int = 0,
) -> ResourceAssessment:
    """Recommend local-worker limits under memory pressure.

    Never suggests terminating unrelated user applications.
    """
    pol = policy or ResourcePolicy()
    # Structural invariant: never kill user apps.
    pol = ResourcePolicy(
        max_local_workers=max(0, min(pol.max_local_workers, 1)),
        unload_idle_models=pol.unload_idle_models,
        may_terminate_user_apps=False,
        disable_local_under_critical_pressure=pol.disable_local_under_critical_pressure,
    )

    actions: list[str] = []
    if memory_available_gb is None:
        # Without availability, use total as a coarse guide.
        if memory_total_gb < 8:
            pressure = ResourcePressure.CRITICAL
        elif memory_total_gb < 16:
            pressure = ResourcePressure.ELEVATED
        else:
            pressure = ResourcePressure.OK
    else:
        ratio = (
            memory_available_gb / memory_total_gb if memory_total_gb > 0 else 0.0
        )
        if memory_available_gb < 2 or ratio < 0.1:
            pressure = ResourcePressure.CRITICAL
        elif memory_available_gb < 4 or ratio < 0.2:
            pressure = ResourcePressure.HIGH
        elif memory_available_gb < 8 or ratio < 0.35:
            pressure = ResourcePressure.ELEVATED
        else:
            pressure = ResourcePressure.OK

    if pressure in {ResourcePressure.HIGH, ResourcePressure.CRITICAL}:
        if pol.unload_idle_models:
            actions.append("unload_idle_local_models")
        actions.append("prefer_cloud_or_disable_local_worker")

    if pressure == ResourcePressure.CRITICAL and pol.disable_local_under_critical_pressure:
        recommended_workers = 0
        local_recommended = False
        actions.append("disable_local_worker_under_pressure")
    else:
        recommended_workers = 0 if pol.max_local_workers <= 0 else 1
        if active_local_workers > recommended_workers:
            actions.append("refuse_additional_local_workers")
        local_recommended = recommended_workers > 0 and pressure != ResourcePressure.HIGH

    actions.append("never_terminate_unrelated_user_apps")
    return ResourceAssessment(
        pressure=pressure,
        recommended_workers=recommended_workers,
        local_recommended=local_recommended,
        actions=tuple(dict.fromkeys(actions)),
        policy=pol,
    )
