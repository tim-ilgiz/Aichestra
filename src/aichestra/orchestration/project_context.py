"""Bounded ProjectContext discovery for Mode C (FR-078–080, MODE-C-013/014).

Aichestra discovers project-owned instructions and tooling, then hands a
bounded snapshot to Orca. Detection alone is insufficient — the context must
be explicit policy/context payload.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aichestra.orchestration.factory_preserve import (
    FactoryDetection,
    detect_factory_tooling,
)

# Precedence (highest → lowest), mirrored in ProjectContext.precedence:
# 1. global security invariants
# 2. explicit runtime/user overrides
# 3. target-project authoritative instructions
# 4. machine-local provider/capability policy
# 5. portable Aichestra defaults
PRECEDENCE_ORDER = (
    "global_security",
    "runtime_overrides",
    "project_instructions",
    "machine_local_policy",
    "aichestra_defaults",
)

_AGENTS_CANDIDATES = (
    "AGENTS.md",
    "agents.md",
    ".cursor/rules",
    ".cursorrules",
    "CLAUDE.md",
    ".opencode/AGENTS.md",
    "opencode.json",
)

_SPEC_KIT_MARKERS = (
    ".specify",
    "specs",
    ".specify/memory/constitution.md",
)

_BUILD_META_FILES = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "*.sln",
    "*.csproj",
    "Makefile",
    "CMakeLists.txt",
)


@dataclass(frozen=True)
class SpecKitDiscovery:
    """Where Spec Kit lives for this target project (if anywhere)."""

    present: bool
    canonical_root: str | None
    markers: tuple[str, ...]
    owns_canonical: bool
    """True when the project already has Spec Kit — do not create competing tree."""
    compatibility_dir: str | None
    """Only set when no project Spec Kit exists; optional Aichestra fallback path."""
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "present": self.present,
            "canonical_root": self.canonical_root,
            "markers": list(self.markers),
            "owns_canonical": self.owns_canonical,
            "compatibility_dir": self.compatibility_dir,
            "reason": self.reason,
        }


@dataclass
class ProjectContext:
    """Bounded project snapshot handed to Orca for Mode C."""

    project_root: str
    agents_files: tuple[str, ...] = ()
    instruction_excerpts: dict[str, str] = field(default_factory=dict)
    speckit: SpecKitDiscovery | None = None
    factory: dict[str, Any] = field(default_factory=dict)
    project_config: dict[str, Any] = field(default_factory=dict)
    build_metadata: dict[str, Any] = field(default_factory=dict)
    verification_hints: tuple[str, ...] = ()
    docs_conventions: tuple[str, ...] = ()
    git: dict[str, Any] = field(default_factory=dict)
    precedence: tuple[str, ...] = PRECEDENCE_ORDER
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_root": self.project_root,
            "agents_files": list(self.agents_files),
            "instruction_excerpts": dict(self.instruction_excerpts),
            "speckit": self.speckit.to_dict() if self.speckit else None,
            "factory": dict(self.factory),
            "project_config": dict(self.project_config),
            "build_metadata": dict(self.build_metadata),
            "verification_hints": list(self.verification_hints),
            "docs_conventions": list(self.docs_conventions),
            "git": dict(self.git),
            "precedence": list(self.precedence),
            "extras": dict(self.extras),
        }

    def authoritative_instructions(self) -> list[str]:
        """Paths that Orca must treat as project-authoritative (MODE-C-014)."""
        return list(self.agents_files)


def discover_project_context(
    project_root: Path | str,
    *,
    excerpt_chars: int = 4000,
) -> ProjectContext:
    """Inspect the target project and build a bounded ProjectContext."""
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {root}")

    agents = _discover_agents_files(root)
    excerpts = _read_excerpts(agents, limit=excerpt_chars)
    speckit = discover_speckit(root)
    factory = detect_factory_tooling(root)
    project_cfg = _load_project_json(root)
    build_meta = _discover_build_metadata(root)
    verify_hints = _verification_hints(project_cfg, build_meta)
    docs = _docs_conventions(root)
    git_meta = _git_snapshot(root)

    return ProjectContext(
        project_root=str(root),
        agents_files=tuple(str(p) for p in agents),
        instruction_excerpts=excerpts,
        speckit=speckit,
        factory=_factory_dict(factory),
        project_config=project_cfg,
        build_metadata=build_meta,
        verification_hints=tuple(verify_hints),
        docs_conventions=tuple(docs),
        git=git_meta,
        precedence=PRECEDENCE_ORDER,
        extras={
            "discovery": "aichestra.project_context",
            "instruction_authority": "project_owned",
        },
    )


def discover_speckit(project_root: Path | str) -> SpecKitDiscovery:
    """Locate project-owned Spec Kit; refuse competing `.aichestra/speckit/`."""
    root = Path(project_root).resolve()
    markers: list[str] = []
    canonical: Path | None = None

    specify = root / ".specify"
    specs = root / "specs"
    if specify.is_dir():
        markers.append(".specify")
        canonical = specify
    if specs.is_dir():
        markers.append("specs")
        if canonical is None:
            canonical = specs
    constitution = root / ".specify" / "memory" / "constitution.md"
    if constitution.is_file():
        markers.append(".specify/memory/constitution.md")

    # Nested feature specs are still project-owned Spec Kit.
    if specs.is_dir() and "specs" not in markers:
        markers.append("specs")

    if markers:
        return SpecKitDiscovery(
            present=True,
            canonical_root=str(canonical) if canonical else str(root / ".specify"),
            markers=tuple(dict.fromkeys(markers)),
            owns_canonical=True,
            compatibility_dir=None,
            reason=(
                "target project owns Spec Kit; "
                "do not create competing .aichestra/speckit/ canonical tree"
            ),
        )

    compat = root / ".aichestra" / "speckit"
    return SpecKitDiscovery(
        present=False,
        canonical_root=None,
        markers=(),
        owns_canonical=False,
        compatibility_dir=str(compat),
        reason=(
            "no project Spec Kit detected; "
            ".aichestra/speckit/ may be used only as compatibility fallback"
        ),
    )


def _factory_dict(factory: FactoryDetection) -> dict[str, Any]:
    return {
        "present": factory.present,
        "markers": list(factory.markers),
        "may_delete": factory.may_delete,
        "may_migrate": factory.may_migrate,
        "reason": factory.reason,
    }


def _discover_agents_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for rel in _AGENTS_CANDIDATES:
        path = root / rel
        if path.is_file():
            found.append(path.resolve())
        elif path.is_dir():
            # Collect shallow rule files under .cursor/rules
            try:
                for child in sorted(path.iterdir()):
                    if child.is_file() and child.suffix.lower() in {
                        ".md",
                        ".mdc",
                        ".txt",
                    }:
                        found.append(child.resolve())
            except OSError:
                pass
    excluded = {".git", "node_modules", ".venv", "venv", "__pycache__", "target", "dist", "build"}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in excluded and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            if name.lower() == "agents.md":
                path = Path(directory) / name
                if not path.is_symlink():
                    found.append(path.resolve())
    return list(dict.fromkeys(p for p in found if p.is_relative_to(root)))


def _read_excerpts(paths: list[Path], *, limit: int) -> dict[str, str]:
    out: dict[str, str] = {}
    remaining = max(500, limit)
    per_file = max(400, remaining // max(1, len(paths))) if paths else 0
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        excerpt = text[:per_file]
        out[str(path)] = excerpt
        remaining -= len(excerpt)
        if remaining <= 0:
            break
    return out


def _load_project_json(root: Path) -> dict[str, Any]:
    path = root / ".aichestra" / "project.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"path": str(path), "error": "unreadable"}
    if isinstance(data, dict):
        return data
    return {"path": str(path), "error": "not_object"}


def _discover_build_metadata(root: Path) -> dict[str, Any]:
    meta: dict[str, Any] = {"markers": []}
    markers: list[str] = []
    for name in (
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "Makefile",
        "CMakeLists.txt",
    ):
        if (root / name).is_file():
            markers.append(name)
    try:
        for child in root.iterdir():
            if child.suffix.lower() in {".sln", ".csproj"} and child.is_file():
                markers.append(child.name)
    except OSError:
        pass
    meta["markers"] = markers
    if "pyproject.toml" in markers:
        meta["ecosystem"] = "python"
    elif "package.json" in markers:
        meta["ecosystem"] = "node"
    elif "Cargo.toml" in markers:
        meta["ecosystem"] = "rust"
    elif "go.mod" in markers:
        meta["ecosystem"] = "go"
    elif any(m.endswith((".sln", ".csproj")) for m in markers):
        meta["ecosystem"] = "dotnet"
    else:
        meta["ecosystem"] = "unknown"
    return meta


def _verification_hints(
    project_cfg: dict[str, Any], build_meta: dict[str, Any]
) -> list[str]:
    hints: list[str] = []
    verify = project_cfg.get("verify") or project_cfg.get("verification")
    if isinstance(verify, list):
        hints.extend(str(x) for x in verify)
    elif isinstance(verify, dict) and isinstance(verify.get("commands"), list):
        hints.extend(str(x) for x in verify["commands"])
    eco = build_meta.get("ecosystem")
    if eco == "python":
        hints.append("pytest")
    elif eco == "node":
        hints.append("npm test")
    return hints


def _docs_conventions(root: Path) -> list[str]:
    found: list[str] = []
    for name in ("README.md", "docs", "CONTRIBUTING.md", "AGENTS.md"):
        if (root / name).exists():
            found.append(name)
    return found


def _git_snapshot(root: Path) -> dict[str, Any]:
    git_dir = root / ".git"
    if not git_dir.exists():
        return {"is_git": False}
    head = ""
    branch = ""
    try:
        head_file = git_dir / "HEAD"
        if head_file.is_file():
            head = head_file.read_text(encoding="utf-8", errors="replace").strip()
            if head.startswith("ref: "):
                branch = head[5:].strip()
    except OSError:
        pass
    return {"is_git": True, "head": head, "branch": branch}
