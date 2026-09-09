"""Integration: bootstrap idempotency and machine-local preservation."""

from __future__ import annotations

import json
from pathlib import Path

from aichestra.bootstrap.core import bootstrap, update
from aichestra.config.layering import load_json, machine_local_path


def test_bootstrap_idempotent_preserves_machine_local(fake_aichestra_root: Path) -> None:
    first = bootstrap(repo_root=fake_aichestra_root, enable_local=False)
    assert first.ok
    path = machine_local_path(fake_aichestra_root)
    assert path.is_file()
    original = load_json(path)
    original["providers"]["custom"] = "keep-me"
    path.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")

    second = bootstrap(repo_root=fake_aichestra_root)
    assert second.preserved_machine_local is True
    assert second.created_machine_local is False
    kept = load_json(path)
    assert kept["providers"]["custom"] == "keep-me"
    assert kept["local"]["enabled"] is False

    third = update(repo_root=fake_aichestra_root)
    assert third.ok
    assert "update_via_bootstrap" in third.actions


def test_bootstrap_complete_when_doctor_ok_no_blockers(
    fake_aichestra_root: Path, monkeypatch
) -> None:
    """bootstrap_complete=True when doctor ok and remaining steps empty."""
    from aichestra import doctor as doctor_mod

    class _Ok:
        ok = True
        checks: list = []

    monkeypatch.setattr(doctor_mod, "run_doctor", lambda **kwargs: _Ok())
    monkeypatch.setenv("AICHESTRA_FAKE_PROVIDERS", "1")
    result = bootstrap(repo_root=fake_aichestra_root, enable_local=False)
    assert result.ok
    notes = load_json(machine_local_path(fake_aichestra_root)).get("notes") or {}
    # With fake providers, orca may still be "missing" depending on discovery —
    # complete only when no blocking remaining steps.
    if not result.remaining_steps and result.doctor_ok:
        assert notes.get("bootstrap_complete") is True
        assert "bootstrap_complete" in result.actions
    else:
        # Honest remaining list — never forever-pending advisories only.
        assert "configure_orca_provider_integration_if_needed" not in result.remaining_steps
        assert "run_safe_smoke_tests_on_this_machine" not in result.remaining_steps
