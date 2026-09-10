"""Capability-based local model selection (AVAILABLE/CAPABLE/ALLOWED/PREFERRED)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from aichestra.local_runtime.base import LocalModel, ModelCapability
from aichestra.local_runtime.ollama import parse_parameter_billions


class SelectionAxis(str, Enum):
    AVAILABLE = "AVAILABLE"
    CAPABLE = "CAPABLE"
    ALLOWED = "ALLOWED"
    PREFERRED = "PREFERRED"


@dataclass(frozen=True)
class ModelSelection:
    model: LocalModel | None
    axes: dict[str, bool]
    reason: str
    requires_download_approval: bool = False
    candidates: tuple[LocalModel, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.model is not None and not self.requires_download_approval


ApproveDownload = Callable[[str], bool]


def select_model(
    models: list[LocalModel],
    *,
    required_capability: ModelCapability | str = ModelCapability.TEXT,
    local_enabled: bool = False,
    preferred_ids: list[str] | None = None,
    allowed_ids: list[str] | None = None,
    max_parameter_billions: float | None = None,
    allow_download: bool = False,
    approve_download: ApproveDownload | None = None,
    download_candidate_id: str | None = None,
) -> ModelSelection:
    """Pick an installed capable model; never auto-download without approval.

    Selection evaluates AVAILABLE → CAPABLE → ALLOWED → PREFERRED.
    Installed models are always preferred over downloads.
    """
    required = (
        ModelCapability(required_capability)
        if isinstance(required_capability, str)
        else required_capability
    )
    preferred = {p.lower() for p in (preferred_ids or [])}
    allowed = {a.lower() for a in (allowed_ids or [])} if allowed_ids else None

    if not local_enabled:
        return ModelSelection(
            model=None,
            axes={
                SelectionAxis.AVAILABLE.value: False,
                SelectionAxis.CAPABLE.value: False,
                SelectionAxis.ALLOWED.value: False,
                SelectionAxis.PREFERRED.value: False,
            },
            reason="local.enabled=false; cloud-only mode remains valid",
            candidates=tuple(models),
        )

    installed = [m for m in models if m.installed]
    available = bool(installed)
    capable = [
        m
        for m in installed
        if m.has_capability(required)
        and _within_size(m, max_parameter_billions)
    ]
    if allowed is not None:
        allowed_models = [m for m in capable if m.id.lower() in allowed]
    else:
        allowed_models = list(capable)

    preferred_models = [
        m for m in allowed_models if m.id.lower() in preferred or m.name.lower() in preferred
    ]
    # Without a preferred match, pick the smallest capable model (unknown sizes last).
    if preferred_models:
        chosen = preferred_models[0]
    elif allowed_models:
        chosen = min(allowed_models, key=_size_sort_key)
    else:
        chosen = None

    axes = {
        SelectionAxis.AVAILABLE.value: available,
        SelectionAxis.CAPABLE.value: bool(capable),
        SelectionAxis.ALLOWED.value: bool(allowed_models),
        SelectionAxis.PREFERRED.value: bool(preferred_models) or (
            chosen is not None and not preferred
        ),
    }

    if chosen is not None:
        return ModelSelection(
            model=chosen,
            axes=axes,
            reason="reusing installed capable model",
            candidates=tuple(allowed_models),
        )

    # No suitable installed model — download path requires explicit approval.
    if download_candidate_id:
        approved = bool(allow_download)
        if approve_download is not None:
            approved = bool(approve_download(download_candidate_id))
        if not approved:
            return ModelSelection(
                model=None,
                axes=axes,
                reason=(
                    "no suitable installed model; download requires explicit approval"
                ),
                requires_download_approval=True,
                candidates=tuple(installed),
                metadata={"download_candidate_id": download_candidate_id},
            )
        synthetic = LocalModel(
            id=download_candidate_id,
            name=download_candidate_id,
            runtime="pending-download",
            capabilities=frozenset({required}),
            installed=False,
        )
        return ModelSelection(
            model=synthetic,
            axes=axes,
            reason="download explicitly approved",
            requires_download_approval=False,
            candidates=tuple(installed),
            metadata={"download_approved": True},
        )

    reason = "no capable installed model"
    if available and not capable:
        oversized = [
            m
            for m in installed
            if m.has_capability(required) and not _within_size(m, max_parameter_billions)
        ]
        if oversized and max_parameter_billions is not None:
            reason = (
                f"installed capable models exceed max_parameter_billions="
                f"{max_parameter_billions}"
            )
        else:
            reason = f"installed models lack capability {required.value}"
    elif capable and allowed is not None and not allowed_models:
        reason = "no capable model allowed by policy"
    return ModelSelection(
        model=None,
        axes=axes,
        reason=reason,
        candidates=tuple(installed),
    )


def _within_size(model: LocalModel, max_b: float | None) -> bool:
    if max_b is None:
        return True
    size = parse_parameter_billions(model.parameter_size)
    if size is None:
        return True
    return size <= max_b


def _size_sort_key(model: LocalModel) -> tuple[int, float]:
    """Sort key: known sizes ascending; unknown parameter sizes last."""
    size = parse_parameter_billions(model.parameter_size)
    if size is None:
        return (1, float("inf"))
    return (0, size)
