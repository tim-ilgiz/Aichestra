"""Aichestra CLI — doctor / profile / bootstrap / update / handoff / orchestrate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from aichestra import __version__
from aichestra.bootstrap.core import bootstrap, update
from aichestra.doctor import doctor_json, format_doctor_report, run_doctor
from aichestra.machine_profiler import profile_machine
from aichestra.repo import find_repo_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aichestra",
        description="Portable AI development orchestration toolkit",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor_p = sub.add_parser("doctor", help="Cross-platform health check")
    doctor_p.add_argument("--repo-root", type=Path, default=None)
    doctor_p.add_argument("--project-root", type=Path, default=None)
    doctor_p.add_argument("--json", action="store_true")

    profile_p = sub.add_parser("profile", help="Print deterministic machine profile")
    profile_p.add_argument("--repo-root", type=Path, default=None)
    profile_p.add_argument("--json", action="store_true", default=True)

    boot_p = sub.add_parser("bootstrap", help="Idempotent bootstrap")
    boot_p.add_argument("--repo-root", type=Path, default=None)
    boot_p.add_argument("--enable-local", action="store_true", default=False)
    boot_p.add_argument(
        "--approve-model-download",
        action="store_true",
        default=False,
        help="Record explicit approval for large model downloads (does not download)",
    )
    boot_p.add_argument("--json", action="store_true")

    update_p = sub.add_parser("update", help="Idempotent update after git pull")
    update_p.add_argument("--repo-root", type=Path, default=None)
    update_p.add_argument("--json", action="store_true")

    staging_p = sub.add_parser(
        "staging",
        help="Run a typed STAGING diagnostic (alias + allowlist; no production SSH)",
    )
    staging_p.add_argument("alias", help="Machine-local staging alias name")
    staging_p.add_argument(
        "--op",
        default="uptime",
        help="Typed op kind (uptime, disk_usage, process_list, …)",
    )
    staging_p.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    staging_p.add_argument("--dry-run", action="store_true")
    staging_p.add_argument("--repo-root", type=Path, default=None)
    staging_p.add_argument("--json", action="store_true")

    research_p = sub.add_parser(
        "research",
        help="Run repository-researcher compaction against a target project",
    )
    research_p.add_argument("project_root", type=Path)
    research_p.add_argument(
        "--query",
        default="",
        help="Research focus query (participates in local-worker and filesystem scan)",
    )
    research_p.add_argument("--json", action="store_true", default=True)

    handoff_p = sub.add_parser(
        "handoff",
        help="Build a bounded Codex→Cursor manual handoff packet (JSON)",
    )
    handoff_p.add_argument("--prompt", required=True, help="Original request / brief")
    handoff_p.add_argument("--repo", type=Path, default=None, help="Repository path")
    handoff_p.add_argument("--worktree", type=Path, default=None, help="Worktree path")
    handoff_p.add_argument("--phase", default="", help="Workflow phase name")
    handoff_p.add_argument("--next-action", default="", help="Suggested next action")
    handoff_p.add_argument(
        "--decision",
        action="append",
        default=[],
        help="Accepted decision (repeatable)",
    )
    handoff_p.add_argument(
        "--done",
        action="append",
        default=[],
        help="Completed work item (repeatable)",
    )
    handoff_p.add_argument(
        "--remaining",
        action="append",
        default=[],
        help="Remaining work item (repeatable)",
    )
    handoff_p.add_argument(
        "--failure",
        action="append",
        default=[],
        help="Known failure (repeatable)",
    )
    handoff_p.add_argument("--git-status", default="", help="Bounded git status text")
    handoff_p.add_argument("--git-diff", default="", help="Bounded git diff text")
    handoff_p.add_argument(
        "--run-id",
        default="",
        help=(
            "Existing Orca run_id so handoff stays inside the Mode C Run "
            "(required for --execute unless a recent Mode C run can be "
            "auto-resolved from --repo /.aichestra/last_mode_c_run.json)"
        ),
    )
    handoff_p.add_argument(
        "--prepare-only",
        action="store_true",
        help="Only emit the bounded packet / suggested command (do not run Orca)",
    )
    handoff_p.add_argument("--json", action="store_true", default=True)

    orch_p = sub.add_parser(
        "orchestrate",
        help=(
            "Start Mode C: create/resume one Orca Run + policy/gates "
            "(requires Orca; does not wrap native CLIs)"
        ),
    )
    orch_p.add_argument("--prompt", default="Mode C task", help="Task prompt for leads")
    orch_p.add_argument("--query", default="", help="Optional research query")
    orch_p.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Aichestra repository root (machine-local + tracked policies)",
    )
    orch_p.add_argument(
        "--project-root",
        type=Path,
        required=True,
        help="Target project root (required for Mode C; verify commands + write isolation)",
    )
    orch_p.add_argument(
        "--attach",
        action="append",
        default=[],
        metavar="PATH",
        help="Attachment path for native media delivery (repeatable)",
    )
    orch_p.add_argument(
        "--resume-run-id",
        default=None,
        help="Resume an existing Orca Run id (Mode C single-run lifecycle)",
    )
    orch_p.add_argument("--no-research", action="store_true")
    orch_p.add_argument(
        "--no-orca",
        action="store_true",
        help="Disable Orca (Mode C will fail closed)",
    )
    orch_p.add_argument("--no-codex", action="store_true", help="Disable Codex lead")
    orch_p.add_argument("--no-cursor", action="store_true", help="Disable Cursor lead")
    orch_p.add_argument(
        "--no-local",
        action="store_true",
        help=(
            "Forbid all locality=local ExecutionTargets for this invocation "
            "(also sets legacy local.enabled=false)"
        ),
    )
    orch_p.add_argument("--json", action="store_true", default=True)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "doctor":
        report = run_doctor(
            repo_root=args.repo_root,
            project_root=args.project_root,
        )
        if args.json:
            sys.stdout.write(doctor_json(report))
        else:
            sys.stdout.write(format_doctor_report(report) + "\n")
        return 0 if report.ok else 1

    if args.command == "profile":
        root = args.repo_root or find_repo_root()
        profile = profile_machine(root=Path(root))
        sys.stdout.write(json.dumps(profile.to_dict(), indent=2) + "\n")
        return 0

    if args.command == "bootstrap":
        result = bootstrap(
            repo_root=args.repo_root,
            enable_local=True if args.enable_local else None,
            approve_model_download=args.approve_model_download,
        )
        _print_result(result.to_dict(), as_json=args.json)
        return 0 if result.ok else 1

    if args.command == "update":
        result = update(repo_root=args.repo_root)
        _print_result(result.to_dict(), as_json=args.json)
        return 0 if result.ok else 1

    if args.command == "staging":
        from aichestra.security.staging_ssh import (
            UnknownStagingAliasError,
            run_staging_diagnostic,
        )

        params = {}
        for item in args.param:
            if "=" not in item:
                parser.error(f"--param must be KEY=VALUE, got {item!r}")
            key, value = item.split("=", 1)
            params[key] = value
        try:
            result = run_staging_diagnostic(
                alias_name=args.alias,
                op_kind=args.op,
                op_params=params,
                repo_root=args.repo_root,
                dry_run=args.dry_run,
            )
        except UnknownStagingAliasError as exc:
            sys.stderr.write(str(exc) + "\n")
            return 2
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
        return 0 if result.ok or args.dry_run else 1

    if args.command == "research":
        from aichestra.orchestration.research_compact import research_paths

        summary = research_paths(args.project_root, query=args.query)
        payload = summary.to_dict()
        sys.stdout.write(json.dumps(payload, indent=2, default=str) + "\n")
        return 0

    if args.command == "handoff":
        return _cmd_handoff(args)

    if args.command == "orchestrate":
        return _cmd_orchestrate(args)

    parser.error(f"unknown command: {args.command}")
    return 2


def _cmd_handoff(args: argparse.Namespace) -> int:
    from aichestra.orchestration.handoff import (
        build_handoff_packet,
        prepare_manual_handoff,
        resolve_recent_mode_c_run_id,
    )

    run_id = str(getattr(args, "run_id", "") or "").strip()
    repo = Path(args.repo).resolve() if args.repo else None
    if not run_id and repo is not None:
        run_id = resolve_recent_mode_c_run_id(project_root=repo) or ""

    execute = not bool(getattr(args, "prepare_only", False))
    if execute and not run_id:
        sys.stderr.write(
            "handoff execute requires --run-id or a recent Mode C run under "
            "--repo/.aichestra/last_mode_c_run.json (use --prepare-only for "
            "packet-only)\n"
        )
        return 2

    packet = build_handoff_packet(
        original_request=args.prompt,
        repo_path=str(repo) if repo else "",
        worktree_path=str(args.worktree.resolve()) if args.worktree else "",
        workflow_phase=args.phase or "",
        next_action=args.next_action or "",
        accepted_decisions=list(args.decision or []),
        completed_work=list(args.done or []),
        remaining_work=list(args.remaining or []),
        known_failures=list(args.failure or []),
        git_status=args.git_status or "",
        git_diff=args.git_diff or "",
        orca_run_id=run_id,
    )
    payload = prepare_manual_handoff(
        packet,
        execute=execute,
        run_id=run_id or None,
        project_root=str(repo) if repo else None,
    )
    sys.stdout.write(json.dumps(payload, indent=2, default=str) + "\n")
    if payload.get("executed"):
        return 0
    if getattr(args, "prepare_only", False):
        return 0
    # Attempted execute but Orca missing / failed — still emit packet; non-zero.
    return 1 if payload.get("execute_error") else 0


def _local_cfg_list(local_cfg: dict[str, Any], *keys: str) -> list[str] | None:
    for key in keys:
        raw = local_cfg.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, (list, tuple)):
            return [str(item) for item in raw]
    return None


def _cmd_orchestrate(args: argparse.Namespace) -> int:
    from aichestra.config.layering import (
        apply_provider_enable_overrides,
        fallback_lead_name,
        local_enabled,
        preferred_lead_name,
        resolve_config,
    )
    from aichestra.execution import (
        EXECUTION_TARGET_CONTRACT_VERSION,
        resolve_mode_c_execution,
    )
    from aichestra.orchestration.modes import Mode
    from aichestra.orchestration.roles import select_lead
    from aichestra.orchestration.verification import verification_commands_from_config
    from aichestra.orchestration.workflow import ModeCRunController, WorkflowBindings
    from aichestra.providers.discovery import discover_providers, enabled_map_from_config
    from aichestra.providers.fakes import fake_orca
    from aichestra.providers.orca import OrcaProvider
    from aichestra.providers.quota_guard import real_provider_execution_blocked

    # Mode C must never write into ambient cwd — project-root is mandatory.
    if not args.project_root:
        sys.stderr.write(
            "Mode C requires --project-root before any provider execution\n"
        )
        return 2
    project_root = Path(args.project_root).resolve()
    if not project_root.is_dir():
        sys.stderr.write(f"Mode C --project-root is not a directory: {project_root}\n")
        return 2

    repo_root = Path(args.repo_root).resolve() if args.repo_root else find_repo_root()
    cfg = resolve_config(repo_root=repo_root, project_root=project_root)
    cfg = apply_provider_enable_overrides(
        cfg,
        no_orca=bool(getattr(args, "no_orca", False)),
        no_codex=bool(getattr(args, "no_codex", False)),
        no_cursor=bool(getattr(args, "no_cursor", False)),
        no_local=bool(getattr(args, "no_local", False)),
    )
    # CI/fake mode never probes or launches real runtimes.
    use_fakes = real_provider_execution_blocked()
    execution_targets, execution_policy, _facts = resolve_mode_c_execution(
        cfg, known_launches=() if use_fakes else None)
    local_cfg = cfg.get("local") if isinstance(cfg.get("local"), dict) else {}
    ollama_host = local_cfg.get("ollama_host") or local_cfg.get("endpoint")
    preferred_ids = _local_cfg_list(local_cfg, "preferred_models", "preferred_ids")
    allowed_ids = _local_cfg_list(local_cfg, "allowed_models", "allowed_ids")
    enabled_local = local_enabled(cfg)
    preferred = preferred_lead_name(cfg)
    fallback = fallback_lead_name(cfg)
    enabled = enabled_map_from_config(cfg)

    providers = discover_providers(
        local_enabled=enabled_local,
        ollama_host=str(ollama_host) if ollama_host else None,
        enabled=enabled,
    )
    if use_fakes:
        from aichestra.providers.fakes import fake_execution_targets
        execution_targets = fake_execution_targets(providers, execution_policy)
    # Policy resolution for observability / config_roots — not adapter construction.
    selection = select_lead(providers, preferred=preferred, fallback=fallback)
    attachments = tuple(str(Path(p).expanduser().resolve()) for p in (args.attach or []))

    orca = (fake_orca() if use_fakes else OrcaProvider()) if enabled.get("orca", True) else None

    # Model discovery supplies policy facts; Mode C never constructs worker adapters.
    selected_model = None
    local_model_ref = None
    local_capabilities: tuple[str, ...] = ()
    installed_models: tuple[dict, ...] = ()
    if enabled_local:
        try:
            from aichestra.local_runtime.discovery import discover_local_runtime_report
            from aichestra.local_runtime.model_selector import select_model
            from aichestra.local_runtime.base import LocalModel, ModelCapability

            report = discover_local_runtime_report(
                ollama_host=str(ollama_host) if ollama_host else None,
                local_enabled=True,
            )
            models: list = []
            installed_list: list[dict] = []
            for runtime in report.get("runtimes") or []:
                if not isinstance(runtime, dict):
                    continue
                for raw in runtime.get("models") or []:
                    if not isinstance(raw, dict):
                        continue
                    installed_list.append(dict(raw))
                    try:
                        cap_set = frozenset(
                            ModelCapability(c)
                            if c in ModelCapability._value2member_map_
                            else ModelCapability.TEXT
                            for c in (raw.get("capabilities") or ("text",))
                        )
                        models.append(
                            LocalModel(
                                id=str(raw.get("id") or ""),
                                name=str(raw.get("name") or raw.get("id") or ""),
                                runtime=str(raw.get("runtime") or "ollama"),
                                installed=bool(raw.get("installed", True)),
                                capabilities=cap_set,
                                parameter_size=raw.get("parameter_size"),
                            )
                        )
                    except Exception:  # noqa: BLE001
                        continue
            installed_models = tuple(installed_list)
            model_selection = select_model(
                models,
                local_enabled=True,
                preferred_ids=preferred_ids,
                allowed_ids=allowed_ids,
            )
            if model_selection.model is not None:
                selected_model = model_selection.model
                local_model_ref = model_selection.model.id
                local_capabilities = tuple(
                    c.value for c in model_selection.model.capabilities
                )
        except Exception:  # noqa: BLE001
            pass

    bindings = WorkflowBindings(
        orca=orca,  # type: ignore[arg-type]
        providers=providers,
        project_root=str(project_root),
        task_prompt=args.prompt,
        research_query=args.query,
        attachments=attachments,
        resume_run_id=getattr(args, "resume_run_id", None),
        verification_commands=verification_commands_from_config(cfg),
        preferred_lead=preferred,
        fallback_lead=fallback,
        local_enabled=enabled_local,
        local_endpoint=str(ollama_host) if ollama_host else None,
        local_model=selected_model,
        local_model_ref=local_model_ref,
        local_capabilities=local_capabilities,
        installed_models=installed_models,
        execution_targets=execution_targets,
        execution_policy=execution_policy,
    )
    wf = ModeCRunController(
        mode=Mode.ORCHESTRATED,
        research_useful=not args.no_research,
        bindings=bindings,
    )
    state = wf.run_all()
    payload = state.to_dict()
    # manual_handoff already flows via state.metadata when the workflow sets it.
    payload["config_roots"] = {
        "repo_root": str(repo_root),
        "project_root": str(project_root),
        "local_enabled": enabled_local,
        "preferred_lead": preferred,
        "fallback_lead": fallback,
        "lead_policy": selection.lead.value if selection.lead else None,
        "providers_enabled": enabled,
        "fake_providers": use_fakes,
        "verification_commands": bindings.verification_commands,
        "attachments": list(attachments),
        "orca_run_id": state.metadata.get("orca_run_id"),
        "resume_run_id": bindings.resume_run_id,
        "execution_target_count": len(execution_targets),
        "execution_target_contract_version": EXECUTION_TARGET_CONTRACT_VERSION,
    }
    sys.stdout.write(json.dumps(payload, indent=2, default=str) + "\n")
    return 0 if not state.failed and not state.stopped else 1


def _print_result(data: dict, *, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(data, indent=2) + "\n")
        return
    for key, value in data.items():
        sys.stdout.write(f"{key}: {value}\n")


if __name__ == "__main__":
    raise SystemExit(main())
