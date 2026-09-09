"""Repository researcher + research compaction before cloud lead handoff."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


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
    PROVIDER: str = "local-worker"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compact_research(
    *,
    summary: str,
    key_files: Iterable[str] | None = None,
    findings: Iterable[str] | None = None,
    risks: Iterable[str] | None = None,
    open_questions: Iterable[str] | None = None,
    recommended_next: str = "",
    provider: str = "local-worker",
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
    )


def research_paths(
    project_root: Path | str,
    *,
    patterns: tuple[str, ...] = ("README*", "AGENTS.md", "pyproject.toml", "package.json"),
    max_files: int = 20,
    prefer_local_worker: bool = True,
    local_worker_available: bool | None = None,
) -> ResearchSummary:
    """Lightweight read-only filesystem research (no production code writes).

    Prefers ``local-worker`` when available; otherwise labels provider honestly
    as ``filesystem`` so cloud leads are not told a local worker ran (FR-053).
    """
    root = Path(project_root)
    found: list[str] = []
    findings: list[str] = []
    for pattern in patterns:
        for path in sorted(root.glob(pattern))[:max_files]:
            if path.is_file():
                rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
                found.append(rel)
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
                if first:
                    findings.append(f"{rel}: {first[:160]}")
        if len(found) >= max_files:
            break
    summary = (
        f"Read-only scan of {root.name}: {len(found)} key files located."
        if found
        else f"Read-only scan of {root.name}: no matching key files."
    )
    if local_worker_available is None and prefer_local_worker:
        try:
            from aichestra.config.layering import local_enabled, resolve_config
            from aichestra.providers.discovery import discover_providers_report

            cfg = resolve_config()
            report = discover_providers_report(local_enabled=local_enabled(cfg))
            local_worker_available = bool(report.get("local_worker_available"))
        except Exception:
            local_worker_available = False

    if prefer_local_worker and local_worker_available:
        provider = "local-worker"
    else:
        provider = "filesystem"

    return compact_research(
        summary=summary,
        key_files=found,
        findings=findings,
        recommended_next="Hand compacted SUMMARY to lead for implementation.",
        provider=provider,
    )


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
