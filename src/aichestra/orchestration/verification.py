"""verification-runner — real subprocess commands; exit codes are authoritative."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class VerificationResult:
    command: tuple[str, ...]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": list(self.command),
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "ok": self.ok,
        }


@dataclass
class VerificationReport:
    results: list[VerificationResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.results) and all(r.ok for r in self.results)

    @property
    def exit_code(self) -> int:
        if not self.results:
            return 1
        for result in self.results:
            if result.exit_code != 0:
                return result.exit_code
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "results": [r.to_dict() for r in self.results],
        }


def run_command(
    command: Sequence[str],
    *,
    cwd: Path | str | None = None,
    timeout: float | None = 600.0,
    env: dict[str, str] | None = None,
) -> VerificationResult:
    """Execute a real command; LLM opinion is never used for pass/fail."""
    workdir = str(Path(cwd).resolve()) if cwd else str(Path.cwd())
    cmd = tuple(str(c) for c in command)
    try:
        completed = subprocess.run(
            list(cmd),
            cwd=workdir,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return VerificationResult(
            command=cmd,
            cwd=workdir,
            exit_code=124,
            stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
            stderr=(exc.stderr or "") if isinstance(exc.stderr, str) else "timed out",
            timed_out=True,
        )
    except OSError as exc:
        return VerificationResult(
            command=cmd,
            cwd=workdir,
            exit_code=127,
            stdout="",
            stderr=str(exc),
        )
    return VerificationResult(
        command=cmd,
        cwd=workdir,
        exit_code=int(completed.returncode),
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def run_verification(
    commands: Sequence[Sequence[str]],
    *,
    cwd: Path | str | None = None,
    timeout: float | None = 600.0,
    stop_on_failure: bool = True,
) -> VerificationReport:
    """Run target-repo build/test commands; aggregate by exit code (FR-056)."""
    report = VerificationReport()
    for command in commands:
        result = run_command(command, cwd=cwd, timeout=timeout)
        report.results.append(result)
        if stop_on_failure and not result.ok:
            break
    return report


def verification_enabled(config: Mapping[str, Any] | None) -> bool:
    """Single on/off switch for Mode C verification (default: off).

    Canonical key: ``verification.enabled`` in layered / project config.
    Only the JSON boolean ``true`` enables verification — string typos such as
    ``"flase"`` MUST NOT fail-open via ``bool(str)``. Turn on with
    ``aichestra settings set verification.enabled=true`` and configure
    ``verify``.
    """
    if not config:
        return False
    block = config.get("verification")
    if isinstance(block, Mapping) and "enabled" in block:
        return block.get("enabled") is True
    # Legacy: bare verify list present does not auto-enable; require the toggle.
    return False


def require_verification_toggle(config: Mapping[str, Any] | None) -> None:
    """Reject non-boolean ``verification.enabled`` (settings / load validation)."""
    if not config:
        return
    block = config.get("verification")
    if not isinstance(block, Mapping) or "enabled" not in block:
        return
    if not isinstance(block.get("enabled"), bool):
        raise ValueError("verification.enabled must be boolean")


def verification_commands_from_config(config: Mapping[str, Any] | None) -> list[list[str]]:
    """Normalize project ``verify`` config into argv lists for the runner.

    Accepted shapes:
    - ``["python", "-m", "pytest"]`` — one command
    - ``[["npm", "test"], ["npm", "run", "lint"]]`` — multiple commands
    - ``"pytest -q"`` — split with shlex (POSIX-friendly; Windows paths quoted)

    Returns [] when ``verification.enabled`` is false.
    """
    if not config or not verification_enabled(config):
        return []
    raw = config.get("verify")
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = shlex.split(raw, posix=os.name != "nt")
        return [parts] if parts else []
    if not isinstance(raw, list) or not raw:
        return []
    if all(isinstance(item, str) for item in raw):
        return [[str(item) for item in raw]]
    commands: list[list[str]] = []
    for item in raw:
        if isinstance(item, str):
            parts = shlex.split(item, posix=os.name != "nt")
            if parts:
                commands.append(parts)
        elif isinstance(item, (list, tuple)) and item:
            commands.append([str(part) for part in item])
    return commands


def detect_verification_commands(project_root: Path | str | None) -> list[list[str]]:
    """Safe fallback verify commands when ``.aichestra/project.json`` has none.

    Preference order for callers:
    1. Explicit ``verify`` from project / layered config
    2. This detector (.NET / Python / Node heuristics)

    Never invents destructive commands; only common build/test entrypoints.
    """
    if project_root is None:
        return []
    root = Path(project_root)
    if not root.is_dir():
        return []

    # Explicit project config wins when present with verify keys.
    project_cfg = root / ".aichestra" / "project.json"
    if project_cfg.is_file():
        try:
            data = json.loads(project_cfg.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                explicit = verification_commands_from_config(data)
                if explicit:
                    return explicit
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    # .NET
    if any(root.glob("*.sln")) or any(root.glob("*.csproj")):
        cmds: list[list[str]] = [["dotnet", "build"]]
        # Prefer test when test projects likely exist.
        if any(root.rglob("*Test*.csproj")) or any(root.rglob("*Tests*.csproj")):
            cmds.append(["dotnet", "test", "--no-build"])
        else:
            cmds.append(["dotnet", "test"])
        return cmds

    # Python
    py_markers = (
        "pyproject.toml",
        "pytest.ini",
        "setup.cfg",
        "tox.ini",
    )
    if any((root / m).is_file() for m in py_markers) or (root / "tests").is_dir():
        return [["python", "-m", "pytest"]]

    # Node
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            scripts = data.get("scripts") if isinstance(data, dict) else None
            if isinstance(scripts, dict):
                cmds = []
                if "test" in scripts:
                    cmds.append(["npm", "test", "--", "--watchAll=false"])
                if "build" in scripts:
                    cmds.append(["npm", "run", "build"])
                if cmds:
                    return cmds
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        return [["npm", "test"]]

    return []
