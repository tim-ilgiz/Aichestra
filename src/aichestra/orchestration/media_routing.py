"""Files / vision capability routing (FR-058)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from aichestra.local_runtime.base import LocalModel, ModelCapability
from aichestra.orchestration.research_compact import summarize_large_text


class MediaKind(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    PDF = "pdf"
    LOG = "log"
    OTHER = "other"


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
_PDF_SUFFIXES = {".pdf"}
_LOG_SUFFIXES = {".log", ".out", ".err"}


@dataclass(frozen=True)
class RoutingDecision:
    vision_required: bool
    allowed: bool
    provider_hint: str
    reason: str
    summarized_text: str | None = None


def classify_path(path: Path | str) -> MediaKind:
    suffix = Path(path).suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return MediaKind.IMAGE
    if suffix in _PDF_SUFFIXES:
        return MediaKind.PDF
    if suffix in _LOG_SUFFIXES:
        return MediaKind.LOG
    if suffix in {".txt", ".md", ".json", ".yaml", ".yml", ".py", ".ts", ".js"}:
        return MediaKind.TEXT
    return MediaKind.OTHER


def route_media(
    paths: Iterable[Path | str] | None = None,
    *,
    text: str | None = None,
    local_model: LocalModel | None = None,
    local_enabled: bool = False,
    prefer_orca_attachments: bool = True,
    large_text_threshold: int = 8_000,
) -> RoutingDecision:
    """Route files/vision; refuse text-only local models for vision tasks."""
    kinds = [classify_path(p) for p in (paths or [])]
    vision_required = MediaKind.IMAGE in kinds or MediaKind.PDF in kinds

    summarized = None
    if text and len(text) > large_text_threshold:
        summarized = summarize_large_text(text, max_chars=2_000)

    if vision_required:
        if local_enabled and local_model and local_model.has_capability(ModelCapability.VISION):
            return RoutingDecision(
                vision_required=True,
                allowed=True,
                provider_hint="local-worker",
                reason="vision-capable local model selected",
                summarized_text=summarized,
            )
        if prefer_orca_attachments:
            return RoutingDecision(
                vision_required=True,
                allowed=True,
                provider_hint="orca/cloud-vision",
                reason=(
                    "vision required; prefer Orca native attachments / "
                    "vision-capable cloud provider (text-only local refused)"
                ),
                summarized_text=summarized,
            )
        return RoutingDecision(
            vision_required=True,
            allowed=False,
            provider_hint="none",
            reason="vision required but no vision-capable provider available",
            summarized_text=summarized,
        )

    if local_enabled and local_model and local_model.has_capability(ModelCapability.TEXT):
        return RoutingDecision(
            vision_required=False,
            allowed=True,
            provider_hint="local-worker",
            reason="text task routable to local model",
            summarized_text=summarized,
        )
    return RoutingDecision(
        vision_required=False,
        allowed=True,
        provider_hint="cloud-lead",
        reason="text/log task; cloud lead or Orca attachments",
        summarized_text=summarized,
    )
