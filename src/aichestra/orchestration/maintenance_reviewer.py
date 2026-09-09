"""maintenance-reviewer structured gate (tests/docs/ADR/spec)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class TestDecision(str, Enum):
    NONE = "none"
    UPDATE_EXISTING = "update_existing"
    ADD_MINIMAL = "add_minimal"
    REQUIRED = "required"


class DocDecision(str, Enum):
    NONE = "none"
    UPDATE_CANONICAL = "update_canonical"
    ADD_MINIMAL = "add_minimal"
    REQUIRED = "required"


@dataclass
class MaintenanceReviewDecision:
    """Structured maintenance-reviewer output (FR-055).

    Explicit ``none`` / ``none`` for tests and docs is first-class and valid.
    """

    TEST_DECISION: str = TestDecision.NONE.value
    TEST_SCOPE: list[str] = field(default_factory=list)
    DOC_DECISION: str = DocDecision.NONE.value
    DOC_TARGETS: list[str] = field(default_factory=list)
    ADR_REQUIRED: bool = False
    SPEC_UPDATE: bool = False
    RATIONALE: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def needs_tests(self) -> bool:
        return self.TEST_DECISION != TestDecision.NONE.value

    @property
    def needs_docs(self) -> bool:
        return self.DOC_DECISION != DocDecision.NONE.value


def review_change(
    *,
    change_summary: str,
    touches_behavior: bool = False,
    touches_public_api: bool = False,
    existing_tests_cover: bool = False,
    docs_stale: bool = False,
    canonical_doc: str | None = None,
    risk: str = "low",
    rationale: str | None = None,
) -> MaintenanceReviewDecision:
    """Produce structured TEST/DOC/ADR/SPEC decisions; may choose none/none."""
    test_decision = TestDecision.NONE
    test_scope: list[str] = []
    if touches_behavior and not existing_tests_cover:
        test_decision = (
            TestDecision.REQUIRED if risk in {"high", "security"} else TestDecision.ADD_MINIMAL
        )
        test_scope = ["affected behavior"]
    elif touches_behavior and existing_tests_cover:
        test_decision = TestDecision.UPDATE_EXISTING
        test_scope = ["extend existing coverage"]

    doc_decision = DocDecision.NONE
    doc_targets: list[str] = []
    if docs_stale or touches_public_api:
        if canonical_doc:
            doc_decision = DocDecision.UPDATE_CANONICAL
            doc_targets = [canonical_doc]
        else:
            doc_decision = DocDecision.ADD_MINIMAL if touches_public_api else DocDecision.NONE

    adr = risk in {"high", "architecture"} and touches_public_api
    spec_update = risk in {"high", "large"} 

    text = rationale or _default_rationale(
        test_decision, doc_decision, change_summary
    )
    return MaintenanceReviewDecision(
        TEST_DECISION=test_decision.value,
        TEST_SCOPE=test_scope,
        DOC_DECISION=doc_decision.value,
        DOC_TARGETS=doc_targets,
        ADR_REQUIRED=adr,
        SPEC_UPDATE=spec_update,
        RATIONALE=text,
    )


def parse_decision(data: dict[str, Any]) -> MaintenanceReviewDecision:
    return MaintenanceReviewDecision(
        TEST_DECISION=str(data.get("TEST_DECISION", TestDecision.NONE.value)),
        TEST_SCOPE=list(data.get("TEST_SCOPE") or []),
        DOC_DECISION=str(data.get("DOC_DECISION", DocDecision.NONE.value)),
        DOC_TARGETS=list(data.get("DOC_TARGETS") or []),
        ADR_REQUIRED=bool(data.get("ADR_REQUIRED", False)),
        SPEC_UPDATE=bool(data.get("SPEC_UPDATE", False)),
        RATIONALE=str(data.get("RATIONALE") or ""),
    )


def _default_rationale(
    test: TestDecision, doc: DocDecision, summary: str
) -> str:
    return (
        f"Change: {summary[:200]}. "
        f"TEST_DECISION={test.value}; DOC_DECISION={doc.value}."
    )
