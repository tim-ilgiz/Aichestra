"""Infer maintenance-reviewer inputs from actual working-tree changes."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any


_CODE_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".cs",
    ".rb",
    ".php",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
}
_DOC_SUFFIXES = {".md", ".rst", ".adoc", ".txt"}
_TEST_DIR_SEGMENTS = frozenset(
    {"tests", "test", "__tests__", "testing", "spec", "specs"}
)
_API_HINTS = (
    "api/",
    "/api.",
    "public/",
    "openapi",
    "swagger",
    "__init__.py",
    "interface",
    "contract",
)
_HIGH_RISK_HINTS = (
    "auth",
    "security",
    "crypto",
    "payment",
    "billing",
    "money",
    "ssh",
    "secret",
    "token",
    "permission",
)


def list_changed_paths(project_root: Path | str | None) -> list[str]:
    """Return changed paths under the target project (git when available).

    Paths are constrained to ``project_root``. If git resolves a parent
    repository (nested fixture / subdirectory), only entries that remain under
    the project root are returned — never the parent repo's full dirty tree.
    """
    if project_root is None:
        return []
    root = Path(project_root).resolve()
    if not root.is_dir():
        return []
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if top.returncode != 0:
        return []
    toplevel = Path((top.stdout or "").strip()).resolve()
    if not toplevel.exists():
        return []
    try:
        root.relative_to(toplevel)
    except ValueError:
        return []

    paths: list[str] = []
    for args in (
        ["git", "-C", str(root), "diff", "--name-only", "HEAD"],
        ["git", "-C", str(root), "diff", "--name-only", "--cached"],
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
    ):
        try:
            completed = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode != 0:
            continue
        for line in (completed.stdout or "").splitlines():
            item = line.strip().replace("\\", "/")
            if not item:
                continue
            abs_path = (toplevel / item).resolve()
            try:
                rel = abs_path.relative_to(root).as_posix()
            except ValueError:
                continue
            if rel not in paths:
                paths.append(rel)
    return paths


def read_implementation_diff(
    project_root: Path | str | None,
    *,
    max_chars: int = 40_000,
) -> str:
    """Return a bounded unified diff for maintenance-reviewer input (FR-023)."""
    if project_root is None:
        return ""
    root = Path(project_root).resolve()
    if not root.is_dir():
        return ""
    chunks: list[str] = []
    for args in (
        ["git", "-C", str(root), "diff", "HEAD"],
        ["git", "-C", str(root), "diff", "--cached"],
    ):
        try:
            completed = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode != 0:
            continue
        text = (completed.stdout or "").strip()
        if text:
            chunks.append(text)
    if not chunks:
        return ""
    blob = "\n\n".join(chunks)
    if len(blob) <= max_chars:
        return blob
    return blob[: max_chars - 20] + "\n…[diff truncated]"


def infer_change_signals(
    *,
    project_root: Path | str | None = None,
    change_summary: str = "",
    changed_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Derive maintenance-reviewer kwargs from performed file changes + summary."""
    paths = list(changed_paths) if changed_paths is not None else list_changed_paths(
        project_root
    )
    summary = (change_summary or "").lower()
    lowered_paths = [p.lower() for p in paths]

    code_paths = [
        p
        for p in lowered_paths
        if Path(p).suffix.lower() in _CODE_SUFFIXES and not _is_test_path(p)
    ]
    test_paths = [p for p in lowered_paths if _is_test_path(p)]
    doc_paths = [
        p
        for p in lowered_paths
        if Path(p).suffix.lower() in _DOC_SUFFIXES or p.endswith("agents.md")
    ]

    touches_behavior = bool(code_paths) or _summary_mentions_behavior(summary)
    touches_public_api = any(_looks_like_api(p) for p in code_paths) or bool(
        re.search(r"\b(public\s+api|openapi|breaking\s+change)\b", summary)
    )
    # Conservative: path presence alone is not proof of coverage (FR-023/055).
    # Callers may override via maintenance_kwargs after reviewing the diff.
    existing_tests_cover = False
    docs_stale = touches_behavior or touches_public_api
    if doc_paths and not touches_public_api:
        # Docs already updated in-tree for a non-API change — not stale.
        docs_stale = False
    if touches_public_api and not doc_paths:
        docs_stale = True

    risk = "low"
    blob = " ".join(lowered_paths) + " " + summary
    if any(hint in blob for hint in _HIGH_RISK_HINTS):
        risk = "high"
    elif touches_public_api or len(code_paths) >= 8:
        risk = "medium"

    canonical_doc = None
    for candidate in ("README.md", "AGENTS.md", "docs/README.md"):
        if any(p.endswith(candidate.lower()) for p in doc_paths):
            canonical_doc = candidate
            break
    if touches_public_api and canonical_doc is None:
        canonical_doc = "README.md"

    return {
        "change_summary": change_summary or ("changed: " + ", ".join(paths[:12])),
        "touches_behavior": touches_behavior,
        "touches_public_api": touches_public_api,
        "existing_tests_cover": existing_tests_cover,
        "docs_stale": docs_stale,
        "canonical_doc": canonical_doc,
        "risk": risk,
        "changed_paths": paths,
        "implementation_diff": read_implementation_diff(project_root),
        "test_paths_touched": test_paths,
    }


def _is_test_path(path: str) -> bool:
    """True for test dirs / test filename patterns — not substring 'test' in names.

    ``src/latest.py`` must not match (``latest`` contains the letters ``test``).
    """
    normalized = path.replace("\\", "/").lower()
    parts = Path(normalized).parts
    name = parts[-1] if parts else Path(normalized).name

    if any(seg in _TEST_DIR_SEGMENTS for seg in parts[:-1]):
        return True

    if name.startswith("test_") or name.startswith("test-"):
        return True
    if name.endswith(("_test.py", "_test.go", "_test.ts", "_test.js", "_test.rb")):
        return True
    if ".test." in name or ".spec." in name:
        return True
    if name.endswith((".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx", "_spec.rb")):
        return True
    return False


def _looks_like_api(path: str) -> bool:
    return any(hint in path for hint in _API_HINTS)


def _summary_mentions_behavior(summary: str) -> bool:
    return bool(
        re.search(
            r"\b(implement|fix|auth|login|behavior|feature|bug|refactor)\b",
            summary,
        )
    )
