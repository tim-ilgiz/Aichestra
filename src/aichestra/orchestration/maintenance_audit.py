"""Maintenance-audit — analysis-only classifications for docs/tests (FR-042)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


class DocClass(str, Enum):
    KEEP_CANONICAL = "KEEP_CANONICAL"
    MERGE = "MERGE"
    UPDATE = "UPDATE"
    ARCHIVE_HISTORY = "ARCHIVE_HISTORY"
    GENERATED_OR_DERIVABLE = "GENERATED_OR_DERIVABLE"
    OBSOLETE = "OBSOLETE"
    DELETE_CANDIDATE = "DELETE_CANDIDATE"


class TestClass(str, Enum):
    HIGH_VALUE = "HIGH_VALUE"
    BUSINESS_INVARIANT = "BUSINESS_INVARIANT"
    INTEGRATION_BEHAVIOR = "INTEGRATION_BEHAVIOR"
    CONTRACT = "CONTRACT"
    SECURITY_OR_MONEY_CRITICAL = "SECURITY_OR_MONEY_CRITICAL"
    DUPLICATE = "DUPLICATE"
    IMPLEMENTATION_DETAIL = "IMPLEMENTATION_DETAIL"
    TRIVIAL = "TRIVIAL"
    OVER_MOCKED = "OVER_MOCKED"
    REDUNDANT_PARAMETER_VARIATION = "REDUNDANT_PARAMETER_VARIATION"
    FLAKY = "FLAKY"
    OBSOLETE = "OBSOLETE"
    CONSOLIDATION_CANDIDATE = "CONSOLIDATION_CANDIDATE"


@dataclass
class AuditItem:
    path: str
    classification: str
    rationale: str
    kind: str  # doc | test


@dataclass
class MaintenanceAuditReport:
    """Analysis-only; cleanup is a separate reviewable step."""

    items: list[AuditItem] = field(default_factory=list)
    analysis_only: bool = True
    cleanup_performed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_only": self.analysis_only,
            "cleanup_performed": self.cleanup_performed,
            "items": [
                {
                    "path": i.path,
                    "classification": i.classification,
                    "rationale": i.rationale,
                    "kind": i.kind,
                }
                for i in self.items
            ],
        }


def classify_doc(path: str, *, text_sample: str = "") -> AuditItem:
    name = Path(path).name.lower()
    sample = text_sample.lower()
    if name in {"agents.md", "readme.md", "constitution.md"}:
        cls = DocClass.KEEP_CANONICAL
        why = "canonical process/entry document"
    elif "implementation-summary" in name or "final-report" in name:
        cls = DocClass.GENERATED_OR_DERIVABLE
        why = "generated report-style document"
    elif "obsolete" in sample or "deprecated" in sample:
        cls = DocClass.OBSOLETE
        why = "marked obsolete/deprecated"
    elif name.endswith(".md") and "archive" in path.lower():
        cls = DocClass.ARCHIVE_HISTORY
        why = "appears historical/archive"
    else:
        cls = DocClass.UPDATE
        why = "review for currency; analysis only"
    return AuditItem(path=path, classification=cls.value, rationale=why, kind="doc")


def classify_test(path: str, *, text_sample: str = "") -> AuditItem:
    sample = text_sample.lower()
    name = Path(path).name.lower()
    if "security" in name or "money" in name or "idempoten" in sample:
        cls = TestClass.SECURITY_OR_MONEY_CRITICAL
        why = "security/money/idempotency signal"
    elif "contract" in name:
        cls = TestClass.CONTRACT
        why = "contract test naming"
    elif "integration" in path.lower():
        cls = TestClass.INTEGRATION_BEHAVIOR
        why = "integration behavior coverage"
    elif sample.count("assert ") <= 1 and "getter" in sample:
        cls = TestClass.TRIVIAL
        why = "likely trivial assertion"
    elif sample.count("mock") >= 5 and "assert_called" in sample:
        cls = TestClass.OVER_MOCKED
        why = "mock-call-count heavy"
    elif "parametrize" in sample and sample.count("@pytest.mark.parametrize") > 3:
        cls = TestClass.REDUNDANT_PARAMETER_VARIATION
        why = "many parameter variations — check redundancy"
    else:
        cls = TestClass.HIGH_VALUE
        why = "default high-value until proven otherwise"
    return AuditItem(path=path, classification=cls.value, rationale=why, kind="test")


def run_maintenance_audit(
    *,
    doc_paths: Iterable[str] | None = None,
    test_paths: Iterable[str] | None = None,
    read_text: bool = False,
) -> MaintenanceAuditReport:
    """Produce analysis-only classifications; does not delete or rewrite files."""
    report = MaintenanceAuditReport()
    for path in doc_paths or []:
        sample = _safe_read(path) if read_text else ""
        report.items.append(classify_doc(path, text_sample=sample))
    for path in test_paths or []:
        sample = _safe_read(path) if read_text else ""
        report.items.append(classify_test(path, text_sample=sample))
    return report


def _safe_read(path: str, limit: int = 4000) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""
