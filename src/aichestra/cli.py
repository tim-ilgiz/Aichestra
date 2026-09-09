"""Aichestra CLI — doctor / profile / bootstrap / update."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

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
    research_p.add_argument("--query", default="")
    research_p.add_argument("--json", action="store_true", default=True)

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

        summary = research_paths(args.project_root)
        payload = summary.to_dict()
        if args.query:
            payload["query"] = args.query
        sys.stdout.write(json.dumps(payload, indent=2, default=str) + "\n")
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


def _print_result(data: dict, *, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(data, indent=2) + "\n")
        return
    for key, value in data.items():
        sys.stdout.write(f"{key}: {value}\n")


if __name__ == "__main__":
    raise SystemExit(main())
