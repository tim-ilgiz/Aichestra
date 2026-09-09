"""Integration: verification-runner and research compaction."""

from __future__ import annotations

import sys
from pathlib import Path

from aichestra.orchestration.research_compact import compact_research, research_paths
from aichestra.orchestration.verification import run_command, run_verification


def test_verification_exit_codes_authoritative(fixture_project_a: Path) -> None:
    ok = run_command(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=fixture_project_a,
        timeout=120,
    )
    assert ok.exit_code == 0
    assert ok.ok

    fail = run_command(
        [sys.executable, "-c", "import sys; sys.exit(7)"],
        cwd=fixture_project_a,
    )
    assert fail.exit_code == 7
    assert not fail.ok


def test_verification_report_stop_on_failure(fixture_project_b: Path) -> None:
    report = run_verification(
        [
            [sys.executable, "-c", "import sys; sys.exit(3)"],
            [sys.executable, "-c", "print('should not run')"],
        ],
        cwd=fixture_project_b,
        stop_on_failure=True,
    )
    assert report.exit_code == 3
    assert len(report.results) == 1


def test_node_fixture_verification(fixture_project_b: Path) -> None:
    result = run_command(["node", "test.js"], cwd=fixture_project_b, timeout=30)
    # node may be absent on some CI images — treat missing binary as skip-like warn
    if result.exit_code == 127:
        return
    assert result.ok, result.stderr


def test_research_compaction_bounds() -> None:
    summary = compact_research(
        summary="word " * 5000,
        key_files=["a.py", "a.py", "b.py"],
        findings=["f1"],
    )
    assert len(summary.SUMMARY) <= 4000
    assert summary.KEY_FILES == ["a.py", "b.py"]
    assert summary.READ_ONLY is True
    assert "SUMMARY" in summary.to_dict()


def test_research_paths_on_fixture(fixture_project_a: Path) -> None:
    result = research_paths(fixture_project_a)
    assert result.READ_ONLY is True
    assert any("README" in f or "pyproject" in f for f in result.KEY_FILES)
