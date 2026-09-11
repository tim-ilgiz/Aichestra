"""Unit: zero-dep console chrome for init/bootstrap/doctor."""

from __future__ import annotations

from aichestra.console_ui import (
    banner,
    box,
    format_bootstrap_report,
    format_init_report,
    step,
    supports_unicode,
    use_color,
    use_pretty,
)


class _FakeStream:
    def __init__(self, *, tty: bool, encoding: str = "utf-8") -> None:
        self._tty = tty
        self.encoding = encoding

    def isatty(self) -> bool:
        return self._tty


def test_use_pretty_and_color_respect_tty_and_no_color(monkeypatch) -> None:
    tty = _FakeStream(tty=True)
    pipe = _FakeStream(tty=False)
    assert use_pretty(tty) is True
    assert use_pretty(pipe) is False

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert use_color(tty) is True
    assert use_color(pipe) is False

    monkeypatch.setenv("NO_COLOR", "1")
    assert use_color(tty) is False


def test_ascii_fallback_when_encoding_cannot_encode_boxes() -> None:
    ascii_stream = _FakeStream(tty=True, encoding="ascii")
    assert supports_unicode(ascii_stream) is False
    rendered = box(["hello"], title="T", stream=ascii_stream, width=40)
    assert "+" in rendered
    assert "╭" not in rendered
    assert "hello" in rendered


def test_banner_and_steps_render_stable_markers() -> None:
    stream = _FakeStream(tty=False, encoding="utf-8")
    text = banner(version="0.1.0", stream=stream)
    assert "Aichestra" in text
    assert "v0.1.0" in text
    assert "╭" in text or "+" in text
    assert "●" in step("progress", "go", stream=stream) or "*" in step(
        "progress", "go", stream=stream
    )


def test_format_init_report_includes_path_and_next_steps() -> None:
    stream = _FakeStream(tty=False)
    out = format_init_report(
        {
            "ok": True,
            "created": True,
            "path": "/tmp/demo/.aichestra/project.json",
            "project_root": "/tmp/demo",
        },
        version="0.1.0",
        stream=stream,
    )
    assert "/tmp/demo/.aichestra/project.json" in out
    assert "aichestra doctor" in out
    assert "Aichestra is ready" in out


def test_format_bootstrap_report_flags_remaining_steps() -> None:
    stream = _FakeStream(tty=False)
    out = format_bootstrap_report(
        {
            "ok": False,
            "repo_root": "/tmp/root",
            "created_machine_local": True,
            "preserved_machine_local": False,
            "local_enabled": False,
            "actions": ["ensure_.local", "create_machine_local"],
            "profile_summary": {"os": "darwin", "architecture": "arm64", "memory_gb": 16},
            "remaining_steps": ["authenticate Orca"],
            "doctor_ok": True,
        },
        stream=stream,
    )
    assert "Created machine.local.json" in out
    assert "authenticate Orca" in out
    assert "Bootstrap needs attention" in out
