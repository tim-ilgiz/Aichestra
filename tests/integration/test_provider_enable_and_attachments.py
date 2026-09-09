"""Provider enable flags and preferred_lead wiring."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from aichestra.cli import main
from aichestra.config.layering import (
    apply_provider_enable_overrides,
    preferred_lead_name,
    provider_enabled,
    resolve_config,
)
from aichestra.orchestration.modes import Mode
from aichestra.orchestration.workflow import (
    MODE_C_HANDOFF_ROLE,
    ModeCRunController,
    WorkflowBindings,
)
from aichestra.providers.base import ProviderKind
from aichestra.providers.discovery import discover_providers
from tests.fakes.providers import fake_codex, fake_orca


def test_provider_enabled_defaults_and_overrides(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "policies").mkdir(parents=True)
    (root / "policies" / "defaults.json").write_text(
        json.dumps(
            {
                "local": {"enabled": False},
                "providers": {
                    "preferred_lead": "cursor",
                    "fallback_lead": "codex",
                    "codex": {"enabled": False},
                    "cursor": {"enabled": True},
                    "orca": {"enabled": True},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cfg = resolve_config(repo_root=root)
    assert preferred_lead_name(cfg) == "cursor"
    assert provider_enabled(cfg, "codex") is False
    assert provider_enabled(cfg, "cursor") is True
    assert provider_enabled(cfg, "local-worker") is False
    merged = apply_provider_enable_overrides(cfg, no_cursor=True)
    assert provider_enabled(merged, "cursor") is False


def test_discover_respects_enabled_map(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AICHESTRA_FAKE_PROVIDERS", "1")
    statuses = discover_providers(
        local_enabled=False,
        enabled={"orca": True, "codex": False, "cursor": True, "local-worker": False},
    )
    by_kind = {s.kind: s for s in statuses}
    assert by_kind[ProviderKind.CODEX].available is False
    assert "disabled" in (by_kind[ProviderKind.CODEX].detail or "")
    assert by_kind[ProviderKind.CURSOR].available is True


def test_vision_attachment_delivered_and_wired(tmp_path: Path) -> None:
    img = tmp_path / "screen.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    orca = fake_orca("success")
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=False,
        bindings=WorkflowBindings(
            orca=orca,
            providers=[(fake_codex("success")).probe()],
            project_root=str(tmp_path),
            task_prompt="fix UI from screenshot",
            attachments=(str(img),),
            maintenance_kwargs={"touches_behavior": False},
            verification_commands=[[sys.executable, "-c", "import sys; sys.exit(0)"]],
        ),
    )
    state = wf.run_all()
    assert not state.failed, state.failed
    routing = state.metadata["media_routing"]
    assert routing["vision_required"] is True
    assert routing["bytes_delivered"] is True
    assert routing.get("staged_outside_parent") is True
    assert not (tmp_path / ".aichestra" / "attachments").exists()
    handoff = next(r for r in orca.sent if (r.role or "") == MODE_C_HANDOFF_ROLE)
    assert handoff.attachments
    assert Path(handoff.attachments[0]).name.endswith(".png") or "screen" in Path(
        handoff.attachments[0]
    ).name
    # Staging cleaned after run_all; request still recorded the staged paths.
    assert len(handoff.attachments) >= 1


def test_orchestrate_honors_preferred_lead_and_no_codex(
    fake_aichestra_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AICHESTRA_FAKE_PROVIDERS", "1")
    # Point machine-local preferred lead at cursor and disable codex via flag.
    local = fake_aichestra_root / ".local"
    local.mkdir(parents=True, exist_ok=True)
    (local / "machine.local.json").write_text(
        json.dumps({"providers": {"preferred_lead": "cursor", "fallback_lead": "codex"}})
        + "\n",
        encoding="utf-8",
    )
    project = tmp_path / "app"
    project.mkdir()
    (project / ".aichestra").mkdir()
    (project / ".aichestra" / "project.json").write_text(
        json.dumps(
            {
                "project_id": "app",
                "verify": [sys.executable, "-c", "import sys; sys.exit(0)"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    code = main(
        [
            "orchestrate",
            "--repo-root",
            str(fake_aichestra_root),
            "--project-root",
            str(project),
            "--prompt",
            "noop typo",
            "--no-research",
            "--no-codex",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0, payload
    assert payload["config_roots"]["preferred_lead"] == "cursor"
    assert payload["config_roots"]["providers_enabled"]["codex"] is False


def test_orchestrate_requires_project_root(
    fake_aichestra_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AICHESTRA_FAKE_PROVIDERS", "1")
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "orchestrate",
                "--repo-root",
                str(fake_aichestra_root),
                "--prompt",
                "no root",
            ]
        )
    assert exc.value.code == 2
