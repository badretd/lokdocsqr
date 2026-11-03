"""LokDocSQR command-line interface (Phase 11).

The CLI is a thin shell around the headless core:

* ``run`` executes one automation through the real ``Runtime``/``Executor``
  pipeline (parse -> validate -> resolve -> build -> execute).
* ``compile`` turns DSL text into the canonical v1.0 JSON automation schema.
* ``validate`` performs the same parse/validate/build steps without executing.
* ``list-nodes`` reports every registered node type.
* ``serve`` hosts watch/cron triggers for one or more automations.
* ``audit show`` / ``audit rollback`` read and reverse the append-only audit log.

The CLI never mutates the filesystem directly. All mutations go through
``core.safe_fs`` by way of the executor/nodes or ``AuditLog.apply_rollback``.

Production safety (F-10A): unless ``--dry-run`` is used, the CLI constructs a
real :class:`PolicyEngine` instead of ``None``. A high-risk action therefore
requires either a policy that allows it or an explicit human opt-in
(``--no-dry-run`` / ``--yes``) that supplies a verify callback.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lokdocsqr import __version__
from lokdocsqr.core import safe_fs
from lokdocsqr.core.audit import AuditLog
from lokdocsqr.core.config import AppConfig, load_config
from lokdocsqr.core.dsl import (
    DSL_EXTENSION,
    compile_dsl,
    document_to_dict,
    parse_dsl,
)
from lokdocsqr.core.errors import (
    GraphParseError,
    GraphValidationError,
    LokError,
)
from lokdocsqr.core.events import EventBus
from lokdocsqr.core.graph import (
    build_graph,
    parse_graph,
    resolve_states,
    validate_graph,
)
from lokdocsqr.core.models import (
    ActionLog,
    ActionSummary,
    RollbackOp,
    RunResult,
    VerifyAction,
    VerifyResult,
    WorkflowDocument,
    WorkspaceScope,
)
from lokdocsqr.core.node_api.registry import registry
from lokdocsqr.core.plugins import discover_plugins
from lokdocsqr.core.policy import PolicyEngine
from lokdocsqr.core.runtime import Runtime
from lokdocsqr.core.scope import ScopeManager

__all__ = ["build_parser", "main"]

_JSON_SUFFIX = ".json"
_AUDIT_FILENAME = "audit.jsonl"


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _add_config_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        metavar="FILE",
        help="application config JSON (base_dir, trash_dir, logs_dir, scope)",
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        metavar="DIR",
        help="workspace base directory (default: config base_dir or CWD)",
    )
    parser.add_argument(
        "--scope",
        action="append",
        default=[],
        metavar="PATH",
        help="allowed (and writable) workspace root; repeatable",
    )


def _add_run_flags(parser: argparse.ArgumentParser) -> None:
    _add_config_flags(parser)
    dry = parser.add_mutually_exclusive_group()
    dry.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="simulate every mutation and write dry_run audit entries",
    )
    dry.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="execute for real and explicitly approve required verification",
    )
    parser.set_defaults(dry_run=None)
    parser.add_argument(
        "--policy",
        type=Path,
        metavar="FILE",
        help="policy JSON for PolicyEngine.from_json (default: built-in policy)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="approve every REQUIRE_VERIFY decision (unattended runs)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the RunResult as JSON instead of a human summary",
    )
    parser.add_argument(
        "--first-error",
        action="store_true",
        help="report only the first validation error (legacy behaviour)",
    )


def _add_automation_argument(parser: argparse.ArgumentParser, *, many: bool = False) -> None:
    parser.add_argument(
        "automation",
        type=Path,
        nargs="+" if many else None,
        metavar="AUTOMATION",
        help="automation .json file or .ldsq DSL file",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="lokdocsqr",
        description=(
            "LokDocSQR local automation engine. Plan, validate, run and audit file automations."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    run_parser = subparsers.add_parser("run", help="execute an automation once")
    _add_automation_argument(run_parser)
    _add_run_flags(run_parser)
    run_parser.add_argument(
        "--automation-id",
        metavar="ID",
        help="override the automation id declared by the document",
    )
    run_parser.add_argument(
        "--explain",
        action="store_true",
        help="show per-node outputs, action logs and errors",
    )
    run_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default=None,
        help="output format (default: text, or json with --json)",
    )
    run_parser.set_defaults(handler=_cmd_run)

    compile_parser = subparsers.add_parser(
        "compile", help="compile a DSL (or JSON) automation to canonical JSON"
    )
    _add_automation_argument(compile_parser)
    compile_parser.add_argument(
        "--output",
        "-o",
        type=Path,
        metavar="FILE",
        help="write the compiled JSON to FILE instead of stdout",
    )
    compile_parser.set_defaults(handler=_cmd_compile)

    validate_parser = subparsers.add_parser(
        "validate", help="parse and validate an automation without executing it"
    )
    _add_automation_argument(validate_parser)
    _add_config_flags(validate_parser)
    validate_parser.add_argument("--json", action="store_true", help="print the result as JSON")
    validate_parser.add_argument(
        "--first-error",
        action="store_true",
        help="report only the first validation error (legacy behaviour)",
    )
    validate_parser.set_defaults(handler=_cmd_validate)

    list_parser = subparsers.add_parser("list-nodes", help="list every registered node type")
    list_parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    list_parser.set_defaults(handler=_cmd_list_nodes)

    serve_parser = subparsers.add_parser("serve", help="run watch/cron triggers until interrupted")
    _add_automation_argument(serve_parser, many=True)
    _add_run_flags(serve_parser)
    serve_parser.set_defaults(handler=_cmd_serve)

    audit_parser = subparsers.add_parser("audit", help="inspect or roll back the audit log")
    audit_sub = audit_parser.add_subparsers(dest="audit_command", metavar="SUBCOMMAND")
    audit_parser.set_defaults(handler=_cmd_audit_help)

    show_parser = audit_sub.add_parser("show", help="show every audit entry for a run id")
    show_parser.add_argument("run_id", metavar="RUN_ID")
    _add_config_flags(show_parser)
    show_parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    show_parser.set_defaults(handler=_cmd_audit_show)

    rollback_parser = audit_sub.add_parser(
        "rollback", help="reverse the rollback-eligible actions of a run"
    )
    rollback_parser.add_argument("run_id", metavar="RUN_ID")
    _add_config_flags(rollback_parser)
    rollback_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show the rollback plan without touching the filesystem",
    )
    rollback_parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    rollback_parser.set_defaults(handler=_cmd_audit_rollback)

    return parser


# ---------------------------------------------------------------------------
# Config / document helpers
# ---------------------------------------------------------------------------


def _resolve_path(base: Path, value: Path | str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _config_from_args(args: argparse.Namespace) -> AppConfig:
    """Build the runtime config from ``--config`` / ``--base-dir`` / ``--scope``."""
    scope_paths = [Path(item) for item in getattr(args, "scope", []) or []]
    explicit_base = getattr(args, "base_dir", None)

    config_path = getattr(args, "config", None)
    if config_path is not None:
        config = load_config(Path(config_path))
    else:
        if explicit_base is not None:
            base = Path(explicit_base).expanduser()
        elif scope_paths:
            # ``--scope`` without a config is the workspace: make relative
            # automation paths resolve there instead of against the CWD.
            base = scope_paths[0].expanduser()
        else:
            base = Path.cwd()
        config = AppConfig(base_dir=base)

    config.base_dir = Path(config.base_dir).expanduser().resolve()

    if explicit_base is not None:
        config.base_dir = Path(explicit_base).expanduser().resolve()
        if config_path is None:
            config.trash_dir = config.base_dir / ".trash"
            config.logs_dir = config.base_dir / ".logs"

    if scope_paths:
        roots = [_resolve_path(config.base_dir, item) for item in scope_paths]
        config.scope_roots = roots

    return config


def _read_automation(path: Path) -> tuple[str, str]:
    """Read an automation file and return ``(suffix, text)``.

    Only the two supported automation extensions are accepted: ``.json`` and
    ``.ldsq``. Anything else is rejected up front instead of being guessed.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in (_JSON_SUFFIX, DSL_EXTENSION):
        shown = suffix or "<none>"
        raise GraphParseError(
            f"{path}: unsupported automation extension {shown!r}; "
            f"use {_JSON_SUFFIX!r} or {DSL_EXTENSION!r}"
        )
    return suffix, path.read_text(encoding="utf-8")


def _load_document(path: Path) -> WorkflowDocument:
    """Load a ``WorkflowDocument`` from a ``.json`` or ``.ldsq`` file."""
    suffix, text = _read_automation(path)
    if suffix == DSL_EXTENSION:
        return parse_dsl(text)

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GraphParseError(f"{path}: invalid automation JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise GraphParseError(f"{path}: automation JSON root must be an object")
    return parse_graph(raw)


def _compile_payload(path: Path) -> dict[str, Any]:
    """Return the canonical JSON automation schema for a ``.json``/``.ldsq`` file."""
    suffix, text = _read_automation(path)
    if suffix == DSL_EXTENSION:
        return compile_dsl(text)

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GraphParseError(f"{path}: invalid automation JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise GraphParseError(f"{path}: automation JSON root must be an object")
    return document_to_dict(parse_graph(raw))


def _apply_scope_override(
    document: WorkflowDocument,
    args: argparse.Namespace,
    base_dir: Path,
) -> None:
    """Let an explicit ``--scope`` replace the document's declared scope."""
    raw_scope = getattr(args, "scope", []) or []
    if not raw_scope:
        return
    roots = [str(_resolve_path(base_dir, item)) for item in raw_scope]
    document.settings.scope.roots = roots
    document.settings.scope.writable = list(roots)


def _policy_from_args(args: argparse.Namespace) -> PolicyEngine:
    policy_path = getattr(args, "policy", None)
    if policy_path is not None:
        return PolicyEngine.from_json(Path(policy_path))
    return PolicyEngine.default()


def _approve_all(proposal: ActionSummary) -> VerifyResult:
    return VerifyResult(
        action=VerifyAction.APPROVE,
        reason=f"CLI opt-in for {proposal.node_id}",
    )


def _verify_callback(
    args: argparse.Namespace,
) -> Callable[[ActionSummary], VerifyResult] | None:
    """Return an approving callback only for an explicit real-run opt-in.

    Defaulting to ``None`` keeps the policy fail-closed: an automation that
    sets ``default_dry_run = false`` cannot silently execute high-risk writes
    unless the operator passes ``--no-dry-run`` or ``--yes``.
    """
    if getattr(args, "yes", False):
        return _approve_all
    if getattr(args, "dry_run", None) is False:
        return _approve_all
    return None


def _make_runtime(
    args: argparse.Namespace,
    config: AppConfig,
    *,
    default_dry_run: bool | None = None,
) -> Runtime:
    return Runtime(
        config,
        policy=_policy_from_args(args),
        events=EventBus(),
        single_instance=False,
        default_dry_run=default_dry_run,
        verify_callback=_verify_callback(args),
    )


def _audit_log(config: AppConfig) -> AuditLog:
    logs_dir = (
        Path(config.logs_dir) if config.logs_dir is not None else Path(config.base_dir) / ".logs"
    )
    return AuditLog(logs_dir / _AUDIT_FILENAME)


def _scope_manager(config: AppConfig) -> ScopeManager:
    roots = [Path(item).resolve() for item in config.scope_roots]
    if not roots:
        roots = [Path(config.base_dir).resolve()]
    return ScopeManager(
        WorkspaceScope(
            roots=list(roots),
            writable=list(roots),
            allow_symlinks=False,
        )
    )


def _ensure_nodes() -> None:
    if not registry.list():
        discover_plugins()


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    document = _load_document(Path(args.automation))
    _apply_scope_override(document, args, config.base_dir)

    warnings: list[str] = []
    validate_graph(document, warnings=warnings)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    runtime = _make_runtime(args, config)
    try:
        automation_id = runtime.register(
            document,
            automation_id=getattr(args, "automation_id", None),
        )
    except GraphValidationError as exc:
        if getattr(args, "first_error", False):
            first = str(exc).split("; ", 1)[0]
            raise GraphValidationError(first) from exc
        raise
    result = runtime.run_once(automation_id, dry_run=args.dry_run)

    output_format = _output_format(args)
    _print_run_result(
        result,
        as_json=output_format == "json",
        explain=bool(getattr(args, "explain", False)),
    )
    return 0 if result.success else 1


def _cmd_compile(args: argparse.Namespace) -> int:
    payload = _compile_payload(Path(args.automation))
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    output = getattr(args, "output", None)
    if output is not None:
        destination = Path(output)
        safe_fs.atomic_write(destination, text)
        print(f"compiled -> {destination}", file=sys.stderr)
    else:
        print(text, end="")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    document = _load_document(Path(args.automation))
    warnings: list[str] = []
    errors = list(validate_graph(document, warnings=warnings))

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    if not errors:
        try:
            _ensure_nodes()
            states = resolve_states(document, registry)
            build_graph(document, states, registry)
        except GraphValidationError as exc:
            errors.append(str(exc))

    if getattr(args, "first_error", False) and errors:
        errors = errors[:1]

    payload = {
        "ok": not errors,
        "automation_id": document.metadata.id,
        "node_count": len(document.nodes),
        "edge_count": len(document.edges),
        "errors": errors,
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
    else:
        print(
            f"OK: {document.metadata.id} ({len(document.nodes)} nodes, {len(document.edges)} edges)"
        )
    return 0 if not errors else 1


def _cmd_list_nodes(args: argparse.Namespace) -> int:
    _ensure_nodes()
    nodes = sorted(registry.list(), key=lambda item: str(item.get("id", "")))
    if args.json:
        print(json.dumps(nodes, indent=2, ensure_ascii=False))
    else:
        for item in nodes:
            inputs = ", ".join(str(name) for name in item.get("inputs", {}))
            outputs = ", ".join(str(name) for name in item.get("outputs", {}))
            print(f"{item.get('id', '')}\t{item.get('title', '')}\tin=[{inputs}] out=[{outputs}]")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    documents: list[WorkflowDocument] = []
    for raw_path in args.automation:
        document = _load_document(Path(raw_path))
        _apply_scope_override(document, args, config.base_dir)
        documents.append(document)

    runtime = _make_runtime(args, config, default_dry_run=args.dry_run)
    for document in documents:
        automation_id = runtime.register(document)
        print(f"registered {automation_id}", flush=True)

    print("serving; press Ctrl+C to stop", flush=True)
    try:
        runtime.serve()
    except KeyboardInterrupt:
        print("stopping", file=sys.stderr)
    finally:
        runtime.stop()
    return 0


def _cmd_audit_help(args: argparse.Namespace) -> int:
    del args
    print("usage: lokdocsqr audit {show,rollback} ...", file=sys.stderr)
    return 2


def _cmd_audit_show(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    audit = _audit_log(config)
    logs = audit.lookup_by_run(args.run_id)

    if args.json:
        print(
            json.dumps(
                [log.to_dict() for log in logs],
                indent=2,
                ensure_ascii=False,
            )
        )
    elif not logs:
        print(f"no audit entries for run {args.run_id!r}", file=sys.stderr)
    else:
        for log in logs:
            print(_format_log(log))
    return 0 if logs else 1


def _cmd_audit_rollback(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    audit = _audit_log(config)
    entries = audit.lookup_by_run(args.run_id)
    if not entries:
        print(f"no audit entries for run {args.run_id!r}", file=sys.stderr)
        return 1

    plan = audit.build_rollback_plan(args.run_id)
    if args.json and args.dry_run:
        print(json.dumps([_rollback_op_to_dict(op) for op in plan], indent=2))
    elif args.dry_run:
        for op in plan:
            print(_format_rollback_op(op))
        print(f"dry-run: {len(plan)} operation(s) not applied")

    if args.dry_run:
        return 0

    if not plan:
        print("nothing to roll back")
        return 0

    scope = _scope_manager(config)
    results = audit.apply_rollback(plan, scope)
    automation_id = entries[0].automation_id
    for log in results:
        # The rollback is itself an auditable action. Keep the original run
        # id so ``audit show`` reports the complete history of the run.
        log.run_id = args.run_id
        log.automation_id = automation_id
        log.node_id = "__rollback__"
        audit.append(log)

    if args.json:
        print(
            json.dumps(
                [log.to_dict() for log in results],
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(f"rolled back {len(results)} operation(s) for {args.run_id}")
    return 0


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _output_format(args: argparse.Namespace) -> str:
    """Resolve ``--format`` / ``--json`` / ``--explain`` precedence."""
    explicit = getattr(args, "format", None)
    if explicit is not None:
        return explicit
    if getattr(args, "json", False):
        return "json"
    return "text"


def _truncate(value: Any, limit: int = 200) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _print_run_result(
    result: RunResult, *, as_json: bool, explain: bool = False
) -> None:
    if as_json:
        print(
            json.dumps(
                result.to_dict(), indent=2, ensure_ascii=False, default=str
            )
        )
        return

    mode = "dry-run" if result.dry_run else "run"
    state = "ok" if result.success else "failed"
    print(f"{result.run_id}: {state} ({mode})")
    for node_id, node_result in result.node_results.items():
        node_state = getattr(node_result.state, "value", str(node_result.state))
        print(f"  node {node_id}: {node_state}")
        if explain:
            if node_result.outputs:
                print("    outputs:")
                for key, value in node_result.outputs.items():
                    print(
                        f"      {key} ({type(value).__name__}): "
                        f"{_truncate(value)}"
                    )
            if "scanned" in node_result.outputs:
                print(
                    f"    {node_id}: scanned "
                    f"{node_result.outputs['scanned']} file(s)"
                )
            for log in node_result.action_logs:
                print(f"    action {_format_log(log)}")
                print(f"      reason: {log.reason or '-'}")
                print(f"      target: {log.target or '-'}")
            for warning in node_result.warnings:
                print(f"    warning: {warning}")
        if node_result.error:
            print(f"    error: {node_result.error}")
    if not explain:
        for log in result.action_logs:
            print(f"  {_format_log(log)}")
    for error in result.errors:
        if not any(node_result.error == error for node_result in result.node_results.values()):
            print(f"  error: {error}")
    for warning in result.warnings:
        print(f"  warning: {warning}", file=sys.stderr)
    if result.rollback_plan:
        print(f"  rollback: {len(result.rollback_plan)} operation(s) available")


def _format_log(log: ActionLog) -> str:
    source = log.source or "-"
    target = log.target or "-"
    return f"{log.node_id} {log.action_type} [{log.status}] {source} -> {target}"


def _format_rollback_op(op: RollbackOp) -> str:
    return f"{op.action}\t{op.source} -> {op.target} (log #{op.original_log_index})"


def _rollback_op_to_dict(op: RollbackOp) -> dict[str, Any]:
    return {
        "action": op.action,
        "source": str(op.source),
        "target": str(op.target),
        "original_log_index": op.original_log_index,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command is None:
        parser.print_help()
        return 0

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0

    try:
        return int(handler(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except (LokError, OSError, ValueError, KeyError) as exc:
        print(f"lokdocsqr: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
