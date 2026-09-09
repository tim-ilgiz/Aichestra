"""Unit tests: doctor and platform path helpers."""

from __future__ import annotations

from pathlib import Path

from aichestra.doctor import CheckStatus, run_doctor
from aichestra.platform_detect import detect_os, platform_info, wsl_required
from aichestra.repo import find_repo_root


def test_doctor_pass_warn_structure(repo_root: Path) -> None:
    report = run_doctor(repo_root=repo_root)
    names = {c.name for c in report.checks}
    assert "aichestra" in names
    assert "machine" in names
    assert "platform" in names
    assert "staging" in names
    assert report.live_validation["windows"] == "NOT VALIDATED"
    assert report.live_validation["linux"] == "NOT VALIDATED"
    assert all(c.status in CheckStatus for c in report.checks)
    # Credentials must not appear in details
    blob = " ".join(c.detail for c in report.checks).lower()
    assert "bearer " not in blob
    assert "password=" not in blob


def test_platform_info_and_wsl_never_required() -> None:
    info = platform_info()
    assert info.os == detect_os()
    assert wsl_required() is False


def test_path_with_spaces_roundtrip(tmp_path_with_spaces: Path) -> None:
    marker = tmp_path_with_spaces / "note.txt"
    marker.write_text("ok", encoding="utf-8")
    assert marker.read_text(encoding="utf-8") == "ok"
    assert " " in str(marker)
