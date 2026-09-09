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
    assert load_json(path)["providers"]["custom"] == "keep-me"
