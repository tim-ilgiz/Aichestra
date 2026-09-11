"""Zero-dependency terminal chrome for interactive CLI output.

Pretty boxes/steps when stdout is a TTY; ASCII + no color otherwise.
Machine-readable ``--json`` paths must not use this module.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, Sequence, TextIO


def stdout_is_tty(stream: TextIO | None = None) -> bool:
    target = stream if stream is not None else sys.stdout
    return bool(getattr(target, "isatty", lambda: False)())


def supports_unicode(stream: TextIO | None = None) -> bool:
    target = stream if stream is not None else sys.stdout
    encoding = getattr(target, "encoding", None) or "utf-8"
    sample = "╭─╮│╰╯●◇▲✓✗"
    try:
        sample.encode(encoding)
        return True
    except (LookupError, UnicodeEncodeError):
        return False


def use_color(stream: TextIO | None = None) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR") in {"1", "true", "TRUE", "yes", "YES"}:
        return True
    target = stream if stream is not None else sys.stdout
    if not stdout_is_tty(target):
        return False
    term = os.environ.get("TERM", "")
    if term in {"", "dumb"}:
        return False
    return True


def use_pretty(stream: TextIO | None = None) -> bool:
    """Interactive chrome for humans; off when piped / CI-friendly plain logs."""
    return stdout_is_tty(stream)


def _term_width(stream: TextIO | None = None, *, default: int = 72) -> int:
    target = stream if stream is not None else sys.stdout
    try:
        width = shutil.get_terminal_size(fallback=(default, 24)).columns
    except OSError:
        width = default
    # Keep boxes readable on narrow terminals without wrapping mid-glyph.
    return max(48, min(width, 88))


class _Style:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def cyan(self, text: str) -> str:
        return self._wrap("36", text)


def _glyphs(*, unicode: bool) -> dict[str, str]:
    if unicode:
        return {
            "tl": "╭",
            "tr": "╮",
            "bl": "╰",
            "br": "╯",
            "h": "─",
            "v": "│",
            "progress": "●",
            "done": "◇",
            "warn": "▲",
            "info": "◆",
            "check": "✓",
            "cross": "✗",
        }
    return {
        "tl": "+",
        "tr": "+",
        "bl": "+",
        "br": "+",
        "h": "-",
        "v": "|",
        "progress": "*",
        "done": "o",
        "warn": "!",
        "info": "*",
        "check": "[ok]",
        "cross": "[x]",
    }


def _visible_len(text: str) -> int:
    """Length without ANSI CSI sequences."""
    out = 0
    i = 0
    while i < len(text):
        if text[i] == "\033" and i + 1 < len(text) and text[i + 1] == "[":
            j = i + 2
            while j < len(text) and text[j] != "m":
                j += 1
            i = j + 1
            continue
        out += 1
        i += 1
    return out


def _pad_line(text: str, inner: int) -> str:
    pad = max(0, inner - _visible_len(text))
    return text + (" " * pad)


def box(
    lines: Sequence[str],
    *,
    title: str | None = None,
    stream: TextIO | None = None,
    width: int | None = None,
) -> str:
    """Render a rounded (or ASCII) panel. ``lines`` may contain ANSI."""
    g = _glyphs(unicode=supports_unicode(stream))
    style = _Style(use_color(stream))
    total = width if width is not None else _term_width(stream)
    inner = max(20, total - 4)

    def row(content: str) -> str:
        return f"{g['v']} {_pad_line(content, inner)} {g['v']}"

    if title:
        # Match BMAD-style: ╭─Title──────╮
        label = f"{g['h']} {style.bold(title)} "
        label_len = _visible_len(label)
        fill = max(0, total - 2 - label_len)
        top = f"{g['tl']}{label}{g['h'] * fill}{g['tr']}"
    else:
        top = f"{g['tl']}{g['h'] * (total - 2)}{g['tr']}"
    body = [row(line) for line in lines]
    bottom = f"{g['bl']}{g['h'] * (total - 2)}{g['br']}"
    return "\n".join([top, *body, bottom])


def banner(
    *,
    product: str = "Aichestra",
    tagline: str = "Portable AI development orchestration",
    version: str | None = None,
    stream: TextIO | None = None,
) -> str:
    style = _Style(use_color(stream))
    lines = [
        style.bold(product),
        style.dim(tagline),
    ]
    if version:
        lines.append(style.dim(f"v{version}"))
    return box(lines, stream=stream)


def step(
    kind: str,
    message: str,
    *,
    stream: TextIO | None = None,
) -> str:
    """One status line: progress | done | warn | info | fail."""
    g = _glyphs(unicode=supports_unicode(stream))
    style = _Style(use_color(stream))
    kind = kind.lower()
    if kind == "progress":
        mark = style.cyan(g["progress"])
    elif kind == "done":
        mark = style.green(g["done"])
    elif kind == "warn":
        mark = style.yellow(g["warn"])
    elif kind == "fail":
        mark = style.red(g["cross"])
    else:
        mark = style.cyan(g["info"])
    return f"{mark}  {message}"


def checklist(
    title: str,
    items: Sequence[tuple[bool, str]],
    *,
    next_steps: Sequence[str] | None = None,
    stream: TextIO | None = None,
) -> str:
    g = _glyphs(unicode=supports_unicode(stream))
    style = _Style(use_color(stream))
    lines: list[str] = []
    for ok, label in items:
        mark = style.green(g["check"]) if ok else style.yellow(g["warn"])
        lines.append(f"{mark}  {label}")
    if next_steps:
        lines.append("")
        lines.append(style.dim("Next:"))
        for item in next_steps:
            lines.append(f"  {item}")
    return box(lines, title=title, stream=stream)


def render_lines(parts: Iterable[str | None]) -> str:
    chunks = [p for p in parts if p]
    return "\n".join(chunks) + ("\n" if chunks else "")


def format_init_report(
    result: dict,
    *,
    version: str | None = None,
    stream: TextIO | None = None,
    include_banner: bool = True,
) -> str:
    """Human summary after ``aichestra init`` (non-JSON)."""
    created = bool(result.get("created"))
    path = str(result.get("path") or "")
    root = str(result.get("project_root") or "")
    if not root and path:
        # path is .../.aichestra/project.json
        root = str(Path(path).resolve().parent.parent)

    action = "Created" if created else "Updated existing"
    parts: list[str | None] = []
    if include_banner:
        parts.extend(
            [
                banner(version=version, stream=stream),
                "",
                step("progress", f"Project root: {root or '(cwd)'}", stream=stream),
            ]
        )
    parts.extend(
        [
            step("done", f"{action} project config: {path}", stream=stream),
            step(
                "done",
                "Ensured .gitignore ignore for user.local.json",
                stream=stream,
            ),
            "",
            checklist(
                "Aichestra is ready",
                [
                    (True, "init"),
                    (True, ".aichestra/project.json"),
                ],
                next_steps=[
                    "aichestra doctor",
                    "aichestra settings",
                    "aichestra bootstrap   # machine-local platform config",
                ],
                stream=stream,
            ),
        ]
    )
    return render_lines(parts)


def format_bootstrap_report(
    data: dict,
    *,
    command: str = "bootstrap",
    version: str | None = None,
    stream: TextIO | None = None,
) -> str:
    """Human summary after ``aichestra bootstrap`` / ``update`` (non-JSON)."""
    ok = bool(data.get("ok"))
    actions = [str(a) for a in (data.get("actions") or [])]
    remaining = [str(s) for s in (data.get("remaining_steps") or [])]
    profile = data.get("profile_summary") if isinstance(data.get("profile_summary"), dict) else {}

    parts: list[str | None] = [
        banner(version=version, stream=stream),
        "",
        step("progress", f"{command} root: {data.get('repo_root')}", stream=stream),
    ]
    if data.get("created_machine_local"):
        parts.append(step("done", "Created machine.local.json", stream=stream))
    elif data.get("preserved_machine_local"):
        parts.append(step("done", "Preserved machine.local.json", stream=stream))

    local_enabled = bool(data.get("local_enabled"))
    parts.append(
        step(
            "done" if local_enabled else "info",
            f"Local worker: {'enabled' if local_enabled else 'disabled'}",
            stream=stream,
        )
    )
    if profile:
        os_name = profile.get("os")
        arch = profile.get("architecture")
        mem = profile.get("memory_gb")
        detail = " · ".join(
            str(x)
            for x in (
                os_name,
                arch,
                f"{mem} GiB" if mem is not None else None,
            )
            if x
        )
        if detail:
            parts.append(step("info", f"Profile: {detail}", stream=stream))

    doctor_ok = data.get("doctor_ok")
    if doctor_ok is True:
        parts.append(step("done", "Doctor: PASS", stream=stream))
    elif doctor_ok is False:
        parts.append(step("warn", "Doctor: FAIL (see aichestra doctor)", stream=stream))

    if actions:
        parts.append(
            step("info", f"Recorded {len(actions)} bootstrap action(s)", stream=stream)
        )

    for item in remaining:
        parts.append(step("warn", item, stream=stream))

    title = "Bootstrap complete" if ok else "Bootstrap needs attention"
    items = [
        (ok, command),
        (doctor_ok is not False, "doctor"),
        (not remaining, "remaining steps cleared" if not remaining else "remaining steps"),
    ]
    next_steps: list[str] = []
    if remaining:
        next_steps.extend(remaining[:5])
    else:
        next_steps.extend(["aichestra doctor", "aichestra init   # in a target project"])

    parts.extend(
        [
            "",
            checklist(title, items, next_steps=next_steps, stream=stream),
        ]
    )
    return render_lines(parts)


def format_doctor_pretty(
    report: object,
    *,
    version: str | None = None,
    stream: TextIO | None = None,
) -> str:
    """Pretty doctor report; ``report`` is a DoctorReport-like object."""
    checks = getattr(report, "checks", ()) or ()
    live = getattr(report, "live_validation", {}) or {}
    ok = bool(getattr(report, "ok", False))
    style = _Style(use_color(stream))

    parts: list[str | None] = [
        banner(version=version, stream=stream),
        "",
        step("progress", "Running health checks", stream=stream),
        "",
    ]
    for check in checks:
        status = getattr(check.status, "value", str(getattr(check, "status", "")))
        name = getattr(check, "name", "check")
        detail = getattr(check, "detail", "")
        if status == "pass":
            parts.append(step("done", f"{name}: {detail}", stream=stream))
        elif status == "warn":
            parts.append(step("warn", f"{name}: {detail}", stream=stream))
        else:
            parts.append(step("fail", f"{name}: {detail}", stream=stream))

    live_lines = [style.dim("Live validation:")]
    for os_name, status in live.items():
        live_lines.append(f"  - {os_name}: {status}")

    summary_items = [
        (ok, "overall PASS" if ok else "overall FAIL"),
        (True, f"{len(checks)} checks"),
    ]
    parts.extend(
        [
            "",
            box(live_lines, title="Platforms", stream=stream),
            "",
            checklist(
                "Doctor",
                summary_items,
                next_steps=None if ok else ["Fix failing checks, then re-run aichestra doctor"],
                stream=stream,
            ),
        ]
    )
    return render_lines(parts)
