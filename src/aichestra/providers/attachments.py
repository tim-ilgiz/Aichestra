"""Native attachment delivery helpers (FR-058).

Path-only prompt text is not delivery. These helpers resolve files, stage bytes
into a workspace inbox, and build provider CLI flags that pass real file inputs
(Codex ``--image`` / ``-i``, Orca ``--attach``).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

_IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}
_VISION_SUFFIXES = _IMAGE_SUFFIXES | {".pdf"}


@dataclass(frozen=True)
class AttachmentDelivery:
    """Result of staging / flag construction for one request."""

    resolved: tuple[str, ...]
    staged: tuple[str, ...]
    missing: tuple[str, ...]
    image_paths: tuple[str, ...]
    vision_paths: tuple[str, ...]
    bytes_delivered: bool
    delivery_mode: str
    detail: str

    def to_dict(self) -> dict:
        return {
            "resolved": list(self.resolved),
            "staged": list(self.staged),
            "missing": list(self.missing),
            "image_paths": list(self.image_paths),
            "vision_paths": list(self.vision_paths),
            "bytes_delivered": self.bytes_delivered,
            "delivery_mode": self.delivery_mode,
            "detail": self.detail,
        }


def is_image_path(path: Path | str) -> bool:
    return Path(path).suffix.lower() in _IMAGE_SUFFIXES


def is_vision_path(path: Path | str) -> bool:
    return Path(path).suffix.lower() in _VISION_SUFFIXES


def resolve_attachment_paths(paths: Iterable[Path | str] | None) -> list[Path]:
    """Absolute existing files only; preserves input order, skips duplicates."""
    out: list[Path] = []
    seen: set[str] = set()
    for raw in paths or ():
        try:
            path = Path(raw).expanduser().resolve()
        except OSError:
            continue
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            out.append(path)
    return out


def stage_attachments(
    paths: Sequence[Path | str],
    dest_root: Path | str | None,
    *,
    inbox_dirname: str = ".aichestra/attachments",
) -> AttachmentDelivery:
    """Copy attachment bytes into ``dest_root`` inbox so workers can read them."""
    resolved = resolve_attachment_paths(paths)
    requested = [str(p) for p in (paths or ())]
    missing = [
        p
        for p in requested
        if str(Path(p).expanduser().resolve()) not in {str(r) for r in resolved}
    ]
    images = tuple(str(p) for p in resolved if is_image_path(p))
    vision = tuple(str(p) for p in resolved if is_vision_path(p))

    if not resolved:
        return AttachmentDelivery(
            resolved=(),
            staged=(),
            missing=tuple(missing or requested),
            image_paths=(),
            vision_paths=(),
            bytes_delivered=False,
            delivery_mode="none",
            detail="no resolvable attachment files",
        )

    if dest_root is None:
        # Still count resolved absolute paths as deliverable for CLI flags.
        return AttachmentDelivery(
            resolved=tuple(str(p) for p in resolved),
            staged=(),
            missing=tuple(missing),
            image_paths=images,
            vision_paths=vision,
            bytes_delivered=True,
            delivery_mode="absolute_paths",
            detail="attachments resolved; no staging root (CLI flags only)",
        )

    inbox = Path(dest_root).resolve() / inbox_dirname
    inbox.mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    for src in resolved:
        dest = inbox / src.name
        if dest.resolve() != src.resolve():
            shutil.copy2(src, dest)
        staged.append(str(dest.resolve()))

    return AttachmentDelivery(
        resolved=tuple(str(p) for p in resolved),
        staged=tuple(staged),
        missing=tuple(missing),
        image_paths=images,
        vision_paths=vision,
        bytes_delivered=True,
        delivery_mode="staged_inbox",
        detail=f"staged {len(staged)} file(s) under {inbox}",
    )


def codex_image_flags(paths: Sequence[Path | str]) -> list[str]:
    """Codex exec: prompt first, then repeated ``-i`` (avoids greedy --image swallow)."""
    flags: list[str] = []
    for path in resolve_attachment_paths(paths):
        if is_image_path(path):
            flags.extend(["-i", str(path)])
    return flags


def orca_attach_flags(paths: Sequence[Path | str]) -> list[str]:
    """Orca worker-start: repeated ``--attach PATH`` for each existing file."""
    flags: list[str] = []
    for path in resolve_attachment_paths(paths):
        flags.extend(["--attach", str(path)])
    return flags


def cursor_image_flags(paths: Sequence[Path | str]) -> list[str]:
    """Best-effort cursor-agent image flags (``--image`` when present)."""
    flags: list[str] = []
    for path in resolve_attachment_paths(paths):
        if is_image_path(path):
            flags.extend(["--image", str(path)])
    return flags
