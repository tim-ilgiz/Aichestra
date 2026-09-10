"""Unit coverage for attachment delivery and hardware profile ordering."""

from __future__ import annotations

from pathlib import Path

from aichestra.config.hardware_profiles import suggest_profile
from aichestra.providers.attachments import (
    codex_image_flags,
    orca_attach_flags,
    stage_attachments,
)
from aichestra.providers.codex import CodexProvider
from aichestra.providers.base import ProviderTaskRequest


def test_suggest_profile_powerful_before_capable() -> None:
    assert suggest_profile(64, has_gpu=True).id == "powerful"
    assert suggest_profile(32, has_gpu=True).id == "powerful"
    assert suggest_profile(24, has_gpu=True).id == "capable-unified-24plus"
    assert suggest_profile(16, has_gpu=False).id == "medium"


def test_no_host_identity_profile_in_production() -> None:
    from aichestra.config import hardware_profiles as hp
    from aichestra import config as cfg

    assert not hasattr(hp, "M4_PRO_24GB")
    assert not hasattr(cfg, "M4_PRO_24GB")
    assert "apple-m4-pro-24gb-example" not in hp.PROFILES
    # Fixture-only example may live under tests/, never in production PROFILES.
    assert all("m4" not in pid.lower() for pid in hp.PROFILES)


def test_stage_attachments_copies_bytes(tmp_path: Path) -> None:
    src = tmp_path / "shot.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    dest_root = tmp_path / "project"
    dest_root.mkdir()
    delivery = stage_attachments([src], dest_root)
    assert delivery.bytes_delivered is True
    assert delivery.staged
    staged = Path(delivery.staged[0])
    assert staged.is_file()
    assert staged.read_bytes().startswith(b"\x89PNG")


def test_codex_and_orca_attach_flags(tmp_path: Path) -> None:
    img = tmp_path / "a.png"
    img.write_bytes(b"x")
    log = tmp_path / "out.log"
    log.write_text("log", encoding="utf-8")
    assert codex_image_flags([img, log]) == ["-i", str(img.resolve())]
    flags = orca_attach_flags([img, log])
    assert flags == [
        "--attach",
        str(img.resolve()),
        "--attach",
        str(log.resolve()),
    ]


def test_codex_provider_appends_image_after_prompt(tmp_path: Path, monkeypatch) -> None:
    img = tmp_path / "ui.png"
    img.write_bytes(b"png")
    captured: dict = {}

    def fake_run_cli_task(*, binary, argv, session, request, unavailable_detail):
        captured["argv"] = list(argv)
        from aichestra.providers.base import FailureClass, ProviderTaskResult

        return ProviderTaskResult(
            ok=True,
            failure=FailureClass.NONE,
            detail="ok",
            session_id=session.session_id,
            metadata={},
        )

    monkeypatch.setattr(
        "aichestra.providers.codex.run_cli_task",
        fake_run_cli_task,
    )
    monkeypatch.setattr(
        CodexProvider,
        "probe",
        lambda self: type(
            "S",
            (),
            {
                "available": True,
                "binary_path": "/usr/bin/codex",
                "detail": "",
            },
        )(),
    )
    provider = CodexProvider()
    session = provider.start_session(role="lead_implement")
    result = provider.send(
        session,
        ProviderTaskRequest(
            prompt="describe",
            role="lead_implement",
            attachments=(str(img),),
        ),
    )
    assert result.ok
    argv = captured["argv"]
    assert "describe" in argv or any("describe" in str(a) for a in argv)
    # prompt before -i
    prompt_idx = next(i for i, a in enumerate(argv) if "describe" in str(a))
    img_idx = argv.index("-i")
    assert prompt_idx < img_idx
    assert result.metadata.get("bytes_delivered") is True
