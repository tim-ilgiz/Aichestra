"""Contract: tracked policies contain no machine home paths."""

from __future__ import annotations

import re
from pathlib import Path

_HOMEISH = re.compile(
    r"(/Users/[^\s\"']+)|(/home/[^\s\"']+)|(C:\\\\Users\\\\)|(C:/Users/)",
    re.IGNORECASE,
)


def test_tracked_policies_have_no_home_paths(repo_root: Path) -> None:
    policies = repo_root / "policies"
    assert policies.is_dir()
    for path in policies.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert not _HOMEISH.search(text), f"home path found in {path}"


def test_defaults_prefer_codex(repo_root: Path) -> None:
    text = (repo_root / "policies" / "defaults.json").read_text(encoding="utf-8")
    assert '"preferred_lead": "codex"' in text
    assert '"enabled": false' in text.lower().replace(" ", "") or '"enabled": false' in text
