"""Native attachment delivery helpers (FR-058).

Path-only prompt text is not delivery. These helpers resolve files, stage bytes
into a workspace inbox, and build provider CLI flags that pass real file inputs
(Codex ``--image`` / ``-i``, Orca ``--attach`` when the installed CLI still
advertises that worker-start flag).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

# binary path -> supports worker-start --attach (MODE-C-010 capability probe).
_ORCA_ATTACH_SUPPORT: dict[str, bool] = {}

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


def resolve_attachment_paths(
    paths: Iterable[Path | str] | None,
) -> tuple[list[Path], list[str]]:
    """Return ``(resolved_files, missing_raw_paths)``.

    Absolute existing files only; preserves input order, skips duplicates.
    """
    out: list[Path] = []
    missing: list[str] = []
    seen: set[str] = set()
    for raw in paths or ():
        raw_s = str(raw)
        try:
            path = Path(raw).expanduser().resolve()
        except OSError:
            missing.append(raw_s)
            continue
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            out.append(path)
        else:
            missing.append(raw_s)
    return out, missing


def _unique_stage_name(inbox: Path, src: Path, used: set[str]) -> str:
    """Avoid basename collisions by disambiguating when names clash."""
    name = src.name
    if name not in used and not (inbox / name).exists():
        used.add(name)
        return name
    stem = src.stem
    suffix = src.suffix
    # Stable disambiguation from parent dir + stem.
    parent_token = src.parent.name.replace(" ", "_")[:24] or "src"
    candidate = f"{stem}__{parent_token}{suffix}"
    n = 2
    while candidate in used or (inbox / candidate).exists():
        candidate = f"{stem}__{parent_token}_{n}{suffix}"
        n += 1
    used.add(candidate)
    return candidate


def stage_attachments(
    paths: Sequence[Path | str],
    dest_root: Path | str | None,
    *,
    inbox_dirname: str = ".aichestra/attachments",
) -> AttachmentDelivery:
    """Copy attachment bytes into ``dest_root`` inbox so workers can read them.

    Missing paths are reported (callers should fail closed). Same basenames from
    different directories are disambiguated instead of overwriting.
    """
    resolved, missing = resolve_attachment_paths(paths)
    images = tuple(str(p) for p in resolved if is_image_path(p))
    vision = tuple(str(p) for p in resolved if is_vision_path(p))

    if not resolved:
        return AttachmentDelivery(
            resolved=(),
            staged=(),
            missing=tuple(missing or [str(p) for p in (paths or ())]),
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
    used_names: set[str] = set()
    for src in resolved:
        dest_name = _unique_stage_name(inbox, src, used_names)
        dest = inbox / dest_name
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
    resolved, _missing = resolve_attachment_paths(paths)
    for path in resolved:
        if is_image_path(path):
            flags.extend(["-i", str(path)])
    return flags


def clear_orca_attach_support_cache() -> None:
    """Test helper: drop cached Orca ``--attach`` capability probes."""
    _ORCA_ATTACH_SUPPORT.clear()


def orca_worker_start_supports_attach(binary: str | None) -> bool:
    """Return True only when this Orca CLI advertises worker-start ``--attach``.

    Orca 1.4.200+ removed ``--attach`` from ``orchestration worker-start``.
    MODE-C-010 / FR-058 require a real attachment primitive — never invent one
    or treat prompt path lists as delivery. Unknown/unprobeable → False.
    """
    path = (binary or "").strip()
    if not path:
        return False
    cached = _ORCA_ATTACH_SUPPORT.get(path)
    if cached is not None:
        return cached

    supported = False
    try:
        proc = subprocess.run(
            [path, "agent-context", "--json"],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        text = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        start = text.find("{")
        found_worker_start = False
        if proc.returncode == 0 and start >= 0:
            data = json.loads(text[start:])
            for cmd in data.get("commands") or []:
                if not isinstance(cmd, dict):
                    continue
                if cmd.get("command") != "orchestration worker-start":
                    continue
                flags = cmd.get("flags") or []
                supported = "attach" in {str(f).strip() for f in flags}
                found_worker_start = True
                break
        if not found_worker_start:
            help_proc = subprocess.run(
                [path, "orchestration", "worker-start", "--help"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            help_text = f"{help_proc.stdout or ''}\n{help_proc.stderr or ''}"
            supported = "--attach" in help_text
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, TypeError):
        supported = False

    _ORCA_ATTACH_SUPPORT[path] = supported
    return supported


def orca_attach_unsupported_detail(binary: str | None = None) -> str:
    """Honest fail-closed message when Mode C attachments cannot be delivered."""
    version_hint = ""
    path = (binary or "").strip()
    if path:
        version_hint = f" (binary={path})"
    return (
        "Mode C FAIL CLOSED: this Orca build does not support "
        "`orchestration worker-start --attach`"
        f"{version_hint}. Attachments cannot be forwarded through a supported "
        "Orca file primitive (MODE-C-010 / FR-058). Omit --attach, or use an "
        "Orca version that advertises worker-start --attach; do not treat "
        "prompt path lists as delivery."
    )


def orca_attach_flags(paths: Sequence[Path | str]) -> list[str]:
    """Orca worker-start: repeated ``--attach PATH`` for each existing file.

    Callers MUST gate on ``orca_worker_start_supports_attach`` before emitting
    these flags; unsupported CLIs reject unknown ``--attach``.
    """
    flags: list[str] = []
    resolved, _missing = resolve_attachment_paths(paths)
    for path in resolved:
        flags.extend(["--attach", str(path)])
    return flags


def cursor_image_flags(paths: Sequence[Path | str]) -> list[str]:
    """Best-effort cursor-agent image flags (``--image`` when present)."""
    flags: list[str] = []
    resolved, _missing = resolve_attachment_paths(paths)
    for path in resolved:
        if is_image_path(path):
            flags.extend(["--image", str(path)])
    return flags
