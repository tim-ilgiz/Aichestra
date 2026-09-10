"""Repository researcher + research compaction before cloud lead handoff."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from aichestra.providers.base import (
    ProviderAdapter,
    ProviderTaskRequest,
    ProviderTaskResult,
)


@dataclass
class ResearchSummary:
    """Compact structured research summary (FR-053)."""

    SUMMARY: str
    KEY_FILES: list[str] = field(default_factory=list)
    FINDINGS: list[str] = field(default_factory=list)
    RISKS: list[str] = field(default_factory=list)
    OPEN_QUESTIONS: list[str] = field(default_factory=list)
    RECOMMENDED_NEXT: str = ""
    READ_ONLY: bool = True
    PROVIDER: str = "filesystem"
    QUERY: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


LocalResearchRunner = Callable[[Path, str], ProviderTaskResult]


def compact_research(
    *,
    summary: str,
    key_files: Iterable[str] | None = None,
    findings: Iterable[str] | None = None,
    risks: Iterable[str] | None = None,
    open_questions: Iterable[str] | None = None,
    recommended_next: str = "",
    provider: str = "filesystem",
    query: str = "",
    max_summary_chars: int = 4_000,
) -> ResearchSummary:
    """Compact research into a bounded SUMMARY packet for cloud handoff."""
    return ResearchSummary(
        SUMMARY=_truncate(" ".join(summary.split()), max_summary_chars),
        KEY_FILES=_dedupe_limit(key_files, 40),
        FINDINGS=_dedupe_limit(findings, 30),
        RISKS=_dedupe_limit(risks, 20),
        OPEN_QUESTIONS=_dedupe_limit(open_questions, 20),
        RECOMMENDED_NEXT=_truncate(recommended_next, 500),
        READ_ONLY=True,
        PROVIDER=provider,
        QUERY=query.strip(),
    )


_DEFAULT_RESEARCH_PATTERNS: tuple[str, ...] = (
    "README*",
    "AGENTS.md",
    "pyproject.toml",
    "package.json",
    "**/*auth*",
    "src/**/*.py",
    "lib/**/*.py",
    "**/README*",
)


def research_paths(
    project_root: Path | str,
    *,
    query: str = "",
    patterns: tuple[str, ...] = _DEFAULT_RESEARCH_PATTERNS,
    max_files: int = 20,
    prefer_local_worker: bool = True,
    local_worker_available: bool | None = None,
    local_worker: ProviderAdapter | None = None,
    local_research_runner: LocalResearchRunner | None = None,
) -> ResearchSummary:
    """Read-only repository research (FR-021/053).

    Labels ``PROVIDER=local-worker`` only when a local worker is actually
    invoked and returns successfully. Otherwise uses ``filesystem``.
    ``query`` participates in both local-worker and filesystem research.
    """
    root = Path(project_root)
    q = (query or "").strip()

    if prefer_local_worker:
        worker_result = _try_local_worker_research(
            root,
            query=q,
            local_worker=local_worker,
            local_worker_available=local_worker_available,
            local_research_runner=local_research_runner,
        )
        if worker_result is not None and worker_result.ok:
            return compact_research(
                summary=_truncate(worker_result.output or f"local-worker research: {q}", 4_000),
                findings=[f"local-worker: {worker_result.detail or 'ok'}"],
                open_questions=[q] if q else [],
                recommended_next="Hand compacted SUMMARY to lead for implementation.",
                provider="local-worker",
                query=q,
            )

    return _filesystem_research(
        root,
        query=q,
        patterns=patterns,
        max_files=max_files,
    )


def _try_local_worker_research(
    root: Path,
    *,
    query: str,
    local_worker: ProviderAdapter | None,
    local_worker_available: bool | None,
    local_research_runner: LocalResearchRunner | None,
) -> ProviderTaskResult | None:
    if local_research_runner is not None:
        return local_research_runner(root, query)

    if local_worker is not None:
        status = local_worker.probe()
        if not status.available:
            return None
        prompt = (
            "Read-only repository research. Do not modify files. "
            f"Project: {root}. Query: {query or '(general overview)'}. "
            "Return a compact summary of key files, findings, risks, and next steps."
        )
        return local_worker.execute_task(
            ProviderTaskRequest(
                prompt=prompt,
                role="repository-researcher",
                context={"project_root": str(root), "query": query},
                read_only=True,
                cwd=str(root),
                timeout_seconds=180.0,
            )
        )

    # Availability flag alone must not claim a provider ran.
    if local_worker_available:
        return None

    if local_worker_available is None:
        # Auto-discover: only invoke when we can construct a real adapter.
        try:
            from aichestra.config.layering import local_enabled, resolve_config
            from aichestra.providers.local_worker import LocalWorkerProvider

            cfg = resolve_config()
            worker = LocalWorkerProvider(
                local_enabled=local_enabled(cfg),
                config=cfg,
            )
            if not worker.probe().available:
                return None
            return _try_local_worker_research(
                root,
                query=query,
                local_worker=worker,
                local_worker_available=True,
                local_research_runner=None,
            )
        except Exception:
            return None
    return None


def _filesystem_research(
    root: Path,
    *,
    query: str,
    patterns: tuple[str, ...],
    max_files: int,
) -> ResearchSummary:
    found: list[str] = []
    findings: list[str] = []
    query_l = query.lower()
    remaining = max_files
    for pattern in patterns:
        if remaining <= 0:
            break
        for path in _glob_capped(root, pattern, limit=remaining):
            if not path.is_file():
                continue
            rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
            if rel in found:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if query_l and query_l not in rel.lower() and query_l not in text.lower():
                continue
            found.append(rel)
            first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            if first:
                findings.append(f"{rel}: {first[:160]}")
            remaining = max_files - len(found)
            if remaining <= 0:
                break

    if query:
        summary = (
            f"Read-only filesystem scan of {root.name} for query {query!r}: "
            f"{len(found)} key files located."
        )
    else:
        summary = (
            f"Read-only scan of {root.name}: {len(found)} key files located."
            if found
            else f"Read-only scan of {root.name}: no matching key files."
        )
    open_q = [f"Unresolved aspects of query: {query}"] if query and not found else (
        [query] if query else []
    )
    return compact_research(
        summary=summary,
        key_files=found,
        findings=findings,
        open_questions=open_q,
        recommended_next="Hand compacted SUMMARY to lead for implementation.",
        provider="filesystem",
        query=query,
    )


def _glob_capped(root: Path, pattern: str, *, limit: int) -> list[Path]:
    """Match files under root with an early stop so recursive globs stay bounded."""
    if limit <= 0:
        return []
    out: list[Path] = []
    try:
        # Path.glob already supports **; iterate without materializing the full tree.
        for path in root.glob(pattern):
            if path.is_file():
                out.append(path)
                if len(out) >= limit:
                    break
    except OSError:
        return out
    return out


def summarize_large_text(text: str, *, max_chars: int = 2_000) -> str:
    """Locally summarize/truncate large text before cloud handoff (FR-058)."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    return _truncate(cleaned, max_chars)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _dedupe_limit(items: Iterable[str] | None, limit: int) -> list[str]:
    if not items:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = str(item).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
        if len(out) >= limit:
            break
    return out
