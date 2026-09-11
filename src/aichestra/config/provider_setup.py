"""Machine-local registration helpers for Agents & Models setup.

Writes only to untracked machine-local config. Aichestra records policy
selection facts (runtime / local endpoint ids) — never API keys, tokens, or
``api_key_env`` names. Authenticated cloud availability comes from Orca.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

from aichestra.config.layering import (
    deep_merge,
    load_json,
    machine_local_path,
    save_machine_local,
)
from aichestra.execution.runtimes import DEFAULT_RUNTIME_BINARIES

_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_RUNTIME_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def validate_provider_id(raw: str) -> str:
    text = str(raw or "").strip().lower()
    if not _PROVIDER_ID.fullmatch(text):
        raise ValueError(
            "provider id must be lowercase alphanumeric/underscore/hyphen "
            "(start with a letter), max 64 chars"
        )
    return text


def validate_runtime_id(raw: str) -> str:
    text = str(raw or "").strip().lower()
    if not _RUNTIME_ID.fullmatch(text):
        raise ValueError(
            "runtime id must be lowercase alphanumeric/underscore/hyphen "
            "(start with a letter), max 64 chars"
        )
    return text


def load_machine_local(repo_root: Path | None = None) -> dict[str, Any]:
    return load_json(machine_local_path(repo_root))


def merge_machine_local(
    patch: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    current = load_machine_local(repo_root)
    merged = deep_merge(current, dict(patch))
    save_machine_local(merged, repo_root=repo_root)
    return merged


def register_ollama_provider(
    *,
    endpoint: str = "http://127.0.0.1:11434",
    enable_local: bool = True,
    pair_opencode: bool = True,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    endpoint = str(endpoint).strip().rstrip("/")
    if not endpoint:
        raise ValueError("ollama endpoint must be non-empty")
    patch: dict[str, Any] = {
        "local": {"enabled": bool(enable_local), "ollama_host": endpoint},
        "execution": {
            "model_providers": {
                "ollama": {
                    "enabled": True,
                    "endpoint": endpoint,
                    "locality": "local",
                }
            }
        },
    }
    merged = merge_machine_local(patch, repo_root=repo_root)
    if pair_opencode:
        merged = apply_opencode_binding(merged, "ollama")
        save_machine_local(merged, repo_root=repo_root)
    return merged


def register_openai_compatible_provider(
    provider_id: str,
    *,
    endpoint: str,
    pair_opencode: bool = True,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Register a local OpenAI-compatible inference backend (no credentials).

    Intended for LM Studio / local vLLM / similar. Authenticated remote
    providers (OpenRouter, OpenAI, …) belong to Orca discovery — do not pass
    API keys or env-var names here.
    """
    pid = validate_provider_id(provider_id)
    endpoint = str(endpoint).strip().rstrip("/")
    if not endpoint:
        raise ValueError("endpoint must be non-empty")
    entry: dict[str, Any] = {
        "enabled": True,
        "endpoint": endpoint,
        "api_style": "openai",
        "configured": True,
        "locality": "local",
    }
    patch: dict[str, Any] = {
        "execution": {"model_providers": {pid: entry}},
    }
    merged = merge_machine_local(patch, repo_root=repo_root)
    if pair_opencode:
        merged = apply_opencode_binding(merged, pid)
        save_machine_local(merged, repo_root=repo_root)
    return merged


def register_agent_runtime(
    runtime_id: str,
    *,
    binaries: list[str] | tuple[str, ...] | None = None,
    enabled: bool = True,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    rid = validate_runtime_id(runtime_id)
    cmds = list(binaries) if binaries is not None else list(
        DEFAULT_RUNTIME_BINARIES.get(rid, (rid,))
    )
    cmds = [str(c).strip() for c in cmds if str(c).strip()]
    if not cmds:
        raise ValueError("runtime binaries must be non-empty")
    patch = {
        "execution": {
            "runtimes": {
                rid: {
                    "enabled": bool(enabled),
                    "binaries": cmds,
                }
            }
        }
    }
    return merge_machine_local(patch, repo_root=repo_root)


def list_registered_providers(config: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    execution = (config or {}).get("execution") if isinstance(config, Mapping) else {}
    raw = execution.get("model_providers") if isinstance(execution, Mapping) else {}
    if not isinstance(raw, Mapping):
        return []
    out: list[dict[str, Any]] = []
    for pid, entry in raw.items():
        if not isinstance(entry, Mapping):
            continue
        out.append(
            {
                "id": pid,
                "endpoint": entry.get("endpoint"),
                "enabled": entry.get("enabled", True),
                "api_style": entry.get("api_style") or (
                    "ollama" if pid == "ollama" else None
                ),
                "locality": entry.get("locality"),
            }
        )
    return out


def list_registered_runtimes(config: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    execution = (config or {}).get("execution") if isinstance(config, Mapping) else {}
    raw = execution.get("runtimes") if isinstance(execution, Mapping) else {}
    if not isinstance(raw, Mapping):
        return []
    out: list[dict[str, Any]] = []
    for rid, entry in raw.items():
        if not isinstance(entry, Mapping):
            continue
        out.append(
            {
                "id": rid,
                "enabled": entry.get("enabled", True),
                "binaries": list(entry.get("binaries") or ()),
            }
        )
    return out


def discover_orca_agent_hints(
    *,
    binary: str | None = None,
    timeout_seconds: float = 8,
) -> list[dict[str, Any]]:
    """Best-effort Orca account/rate-limit signals → runtime suggestions.

    Never copies credentials. Missing Orca returns an empty list.
    """
    from aichestra.providers.orca import resolve_orca_binary

    cli = binary or resolve_orca_binary()
    if not cli:
        return []
    try:
        completed = subprocess.run(
            [cli, "account", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0 or not (completed.stdout or "").strip():
        return []
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    result = payload.get("result") if isinstance(payload, Mapping) else None
    if not isinstance(result, Mapping):
        return []

    hints: dict[str, dict[str, Any]] = {}

    def _mark(runtime_id: str, *, available: bool, detail: str) -> None:
        rid = runtime_id.strip().lower()
        if not rid:
            return
        # Map Orca provider nicknames onto Aichestra runtime ids when obvious.
        aliases = {
            "claude": "claude",
            "codex": "codex",
            "gemini": "gemini",
            "opencode-go": "opencode",
            "opencodego": "opencode",
            "cursor": "cursor",
        }
        mapped = aliases.get(rid, rid if rid in DEFAULT_RUNTIME_BINARIES else None)
        if mapped is None:
            return
        prev = hints.get(mapped)
        if prev and prev.get("available") and not available:
            return
        hints[mapped] = {
            "runtime": mapped,
            "available": bool(available),
            "detail": detail,
            "source": "orca.account.list",
        }

    for kind in ("claude", "codex"):
        block = result.get(kind)
        if not isinstance(block, Mapping):
            continue
        system = block.get("systemDefault")
        accounts = block.get("accounts") if isinstance(block.get("accounts"), list) else []
        has_system = isinstance(system, Mapping) and bool(system.get("hasAuth"))
        has_accounts = bool(accounts)
        if has_system or has_accounts:
            email = ""
            if isinstance(system, Mapping):
                email = str(system.get("email") or "")
            _mark(
                kind,
                available=True,
                detail=email or f"{kind} account present in Orca",
            )
        else:
            _mark(kind, available=False, detail=f"no {kind} account in Orca")

    rates = result.get("rateLimits")
    if isinstance(rates, Mapping):
        for key, block in rates.items():
            if not isinstance(block, Mapping):
                continue
            provider = str(block.get("provider") or key).strip()
            status = str(block.get("status") or "").strip().lower()
            error = str(block.get("error") or "").strip()
            _mark(
                provider,
                available=status == "ok",
                detail=error or status or "seen in Orca rate limits",
            )

    return [hints[k] for k in sorted(hints)]


def merge_bindings(
    existing: Any,
    new_binding: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Append binding if missing; preserve prior rows."""
    rows: list[dict[str, Any]] = []
    if isinstance(existing, list):
        for row in existing:
            if isinstance(row, Mapping):
                rows.append(dict(row))
    runtime = new_binding.get("runtime")
    provider = new_binding.get("provider")
    model = new_binding.get("model")
    for row in rows:
        if (
            row.get("runtime") == runtime
            and row.get("provider") == provider
            and (row.get("model") or None) == (model or None)
        ):
            return rows
    rows.append(dict(new_binding))
    return rows


def apply_opencode_binding(
    machine: Mapping[str, Any],
    provider_id: str,
) -> dict[str, Any]:
    """Return a deep-copied machine config with OpenCode↔provider binding."""
    data = copy.deepcopy(dict(machine))
    execution = data.setdefault("execution", {})
    if not isinstance(execution, dict):
        raise ValueError("execution must be an object")
    execution["bindings"] = merge_bindings(
        execution.get("bindings"),
        {"runtime": "opencode", "provider": provider_id},
    )
    runtimes = execution.setdefault("runtimes", {})
    if not isinstance(runtimes, dict):
        raise ValueError("execution.runtimes must be an object")
    runtimes.setdefault(
        "opencode",
        {
            "enabled": True,
            "binaries": list(DEFAULT_RUNTIME_BINARIES.get("opencode", ("opencode",))),
        },
    )
    return data
