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
    handoff_p.add_argument("--json", action="store_true", default=True)

    orch_p = sub.add_parser(
        "orchestrate",
        help="Start Mode C orchestrated workflow (opt-in; does not wrap native CLIs)",
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
        default=None,
        help="Target project root (.aichestra/project.json + verify commands)",
    )
    orch_p.add_argument(
        "--attach",
        action="append",
        default=[],
        metavar="PATH",
        help="Attachment path for media routing (repeatable)",
    )
    orch_p.add_argument("--no-research", action="store_true")
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
    )

    packet = build_handoff_packet(
        original_request=args.prompt,
        repo_path=str(args.repo.resolve()) if args.repo else "",
        worktree_path=str(args.worktree.resolve()) if args.worktree else "",
        workflow_phase=args.phase or "",
        next_action=args.next_action or "",
        accepted_decisions=list(args.decision or []),
        completed_work=list(args.done or []),
        remaining_work=list(args.remaining or []),
        known_failures=list(args.failure or []),
        git_status=args.git_status or "",
        git_diff=args.git_diff or "",
    )
    payload = prepare_manual_handoff(packet)
    sys.stdout.write(json.dumps(payload, indent=2, default=str) + "\n")
    return 0


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
    from aichestra.config.layering import local_enabled, resolve_config
    from aichestra.orchestration.modes import Mode
    from aichestra.orchestration.roles import select_lead
    from aichestra.orchestration.verification import verification_commands_from_config
    from aichestra.orchestration.workflow import (
        OrchestratedWorkflow,
        WorkflowBindings,
        bound_writer_from_lead,
    )
    from aichestra.providers.codex import CodexProvider
    from aichestra.providers.cursor import CursorProvider
    from aichestra.providers.discovery import discover_providers
    from aichestra.providers.fakes import (
        fake_codex_lead,
        fake_cursor_lead,
        fake_local_worker,
        fake_orca,
    )
    from aichestra.providers.local_worker import LocalWorkerProvider
    from aichestra.providers.orca import OrcaProvider
    from aichestra.providers.quota_guard import real_provider_execution_blocked

    repo_root = Path(args.repo_root).resolve() if args.repo_root else find_repo_root()
    project_root = Path(args.project_root).resolve() if args.project_root else None
    cfg = resolve_config(repo_root=repo_root, project_root=project_root)
    local_cfg = cfg.get("local") if isinstance(cfg.get("local"), dict) else {}
    ollama_host = local_cfg.get("ollama_host") or local_cfg.get("endpoint")
    preferred_ids = _local_cfg_list(local_cfg, "preferred_models", "preferred_ids")
    allowed_ids = _local_cfg_list(local_cfg, "allowed_models", "allowed_ids")
    enabled_local = local_enabled(cfg)

    use_fakes = real_provider_execution_blocked()
    providers = discover_providers(
        local_enabled=enabled_local,
        ollama_host=str(ollama_host) if ollama_host else None,
    )
    selection = select_lead(providers)
    attachments = tuple(str(p) for p in (args.attach or []))

    if use_fakes:
        orca = fake_orca()
        local = fake_local_worker(enabled=enabled_local)
        lead: object | None = None
        if selection.lead and selection.lead.value == "codex":
            lead = fake_codex_lead()
        elif selection.lead and selection.lead.value == "cursor":
            lead = fake_cursor_lead()
        else:
            lead = fake_codex_lead()
    else:
        orca = OrcaProvider()
        local = LocalWorkerProvider(
            local_enabled=enabled_local,
            ollama_host=str(ollama_host) if ollama_host else None,
            preferred_ids=preferred_ids,
            allowed_ids=allowed_ids,
            config=cfg,
        )
        lead = None
        if selection.lead and selection.lead.value == "codex":
            lead = CodexProvider()
        elif selection.lead and selection.lead.value == "cursor":
            lead = CursorProvider()

    # Prefer local_worker for writers when available; else lead (review P2#12).
    writer_adapter: object | None = None
    if local is not None:
        try:
            if local.probe().available:
                writer_adapter = local
        except Exception:
            writer_adapter = None
    if writer_adapter is None and lead is not None:
        writer_adapter = lead

    bindings = WorkflowBindings(
        orca=orca,  # type: ignore[arg-type]
        lead=lead,  # type: ignore[arg-type]
        local_worker=local,  # type: ignore[arg-type]
        providers=providers,
        project_root=str(project_root) if project_root else None,
        task_prompt=args.prompt,
        research_query=args.query,
        attachments=attachments,
        verification_commands=verification_commands_from_config(cfg),
        writer_fn=(
            bound_writer_from_lead(
                writer_adapter,  # type: ignore[arg-type]
                project_root=str(project_root) if project_root else None,
                attachments=attachments,
            )
            if writer_adapter is not None
            else None
        ),
    )
    wf = OrchestratedWorkflow(
        mode=Mode.ORCHESTRATED,
        research_useful=not args.no_research,
        bindings=bindings,
    )
    state = wf.run_all()
    payload = state.to_dict()
    # manual_handoff already flows via state.metadata when the workflow sets it.
    payload["config_roots"] = {
        "repo_root": str(repo_root),
        "project_root": str(project_root) if project_root else None,
        "local_enabled": enabled_local,
        "fake_providers": use_fakes,
        "verification_commands": bindings.verification_commands,
        "attachments": list(attachments),
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
