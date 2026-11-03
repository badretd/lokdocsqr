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
import contextlib
import json
import signal
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lokdocsqr import __version__
from lokdocsqr.core import safe_fs
from lokdocsqr.core.audit import AuditLog
from lokdocsqr.core.compat import normalize_document_data
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
from lokdocsqr.core.events import (
    EVENT_AUDIT_LOGGED,
    EVENT_NODE_FINISHED,
    EVENT_NODE_STARTED,
    EVENT_RUN_FINISHED,
    EVENT_RUN_STARTED,
    EventBus,
)
from lokdocsqr.core.graph import (
    build_graph,
    parse_graph,
    resolve_states,
    validate_graph,
)
from lokdocsqr.core.logging import setup_logging
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
from lokdocsqr.core.paths import expand_user_path
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
    parser.add_argument(
        "--daemon",
        action="store_true",
        help=(
            "daemon mode: run once, keep serving declared watch/cron "
            "triggers, and log every run to the console"
        ),
    )


def _add_include_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "register an additional automation file or directory of "
            ".ldsq/.json files before running (repeatable)"
        ),
    )


def _add_automation_argument(parser: argparse.ArgumentParser, *, many: bool = False) -> None:
    help_text = (
        "automation .json/.ldsq file or directory of automation files"
        if many
        else "automation .json file or .ldsq DSL file"
    )
    parser.add_argument(
        "automation",
        type=Path,
        nargs="+" if many else None,
        metavar="AUTOMATION",
        help=help_text,
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
    _add_include_flags(run_parser)
    run_parser.add_argument(
        "--catalog-fallback",
        action="store_true",
        help=(
            "when a graph_id is missing from the catalog, try "
            "<base_dir>/<graph_id>.ldsq then .json"
        ),
    )
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
    compile_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="json",
        help="output format (default: json)",
    )
    compile_parser.set_defaults(handler=_cmd_compile)

    validate_parser = subparsers.add_parser(
        "validate", help="parse and validate an automation without executing it"
    )
    _add_automation_argument(validate_parser)
    _add_config_flags(validate_parser)
    _add_include_flags(validate_parser)
    validate_parser.add_argument("--json", action="store_true", help="print the result as JSON")
    validate_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default=None,
        help="output format (default: text, or json with --json)",
    )
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
    _add_include_flags(serve_parser)
    serve_parser.add_argument(
        "--explain",
        action="store_true",
        help="show per-node outputs, action logs and errors",
    )
    serve_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default=None,
        help="console log format (default: text, or json with --json)",
    )
    serve_parser.add_argument(
        "--catalog-fallback",
        action="store_true",
        help=(
            "when a graph_id is missing from the catalog, try "
            "<base_dir>/<graph_id>.ldsq then .json"
        ),
    )
    serve_parser.set_defaults(handler=_cmd_serve)

    daemon_parser = subparsers.add_parser(
        "daemon",
        help="run automations once, then serve their triggers until stopped",
    )
    _add_automation_argument(daemon_parser, many=True)
    _add_run_flags(daemon_parser)
    _add_include_flags(daemon_parser)
    daemon_parser.add_argument(
        "--explain",
        action="store_true",
        help="show per-node outputs, action logs and errors",
    )
    daemon_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default=None,
        help="console log format (default: text, or json with --json)",
    )
    daemon_parser.add_argument(
        "--catalog-fallback",
        action="store_true",
        help=(
            "when a graph_id is missing from the catalog, try "
            "<base_dir>/<graph_id>.ldsq then .json"
        ),
    )
    daemon_parser.set_defaults(handler=_cmd_serve, daemon=True)

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
    """Build the runtime config from ``--config`` / ``--base-dir`` / ``--scope``.

    Path semantics:

    * a relative ``base_dir`` in a config file is resolved against the
      config file's directory (not the process CWD);
    * relative ``trash_dir`` / ``logs_dir`` entries are resolved against
      the effective ``base_dir``;
    * ``~`` is expanded everywhere;
    * ``--base-dir`` overrides the config base and re-bases the default
      trash/log directories so a run never splits output and audit across
      two workspaces.
    """
    scope_paths = [
        expand_user_path(item) for item in getattr(args, "scope", []) or []
    ]
    explicit_base = getattr(args, "base_dir", None)
    config_path = getattr(args, "config", None)

    raw_config: dict[str, Any] = {}
    config_dir = Path.cwd()
    config: AppConfig
    if config_path is not None:
        config_file = expand_user_path(config_path).resolve()
        config = load_config(config_file)
        config_dir = config_file.parent
        try:
            loaded = json.loads(config_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            raw_config = loaded
    else:
        if explicit_base is not None:
            initial_base = expand_user_path(explicit_base)
        elif scope_paths:
            # ``--scope`` without a config is the workspace.
            initial_base = scope_paths[0]
        else:
            initial_base = Path.cwd()
        config = AppConfig(base_dir=initial_base)

    # Resolve the base directory first. Config-relative paths are anchored
    # at the config file, CLI-relative paths at the process CWD.
    raw_base = raw_config.get("base_dir", config.base_dir)
    base = expand_user_path(raw_base)
    if not base.is_absolute():
        base = config_dir / base
    if explicit_base is not None:
        base = expand_user_path(explicit_base)
    base = base.resolve()
    config.base_dir = base

    def _resolve_config_path(key: str, default_name: str) -> Path:
        raw = raw_config.get(key)
        if raw is None or raw == "":
            return (base / default_name).resolve()
        value = expand_user_path(raw)
        if not value.is_absolute():
            value = base / value
        return value.resolve()

    # If --base-dir was supplied, a config's omitted trash/log paths follow
    # the new base. Explicit config values are still honoured (absolute, or
    # relative to the effective base).
    config.trash_dir = _resolve_config_path("trash_dir", ".trash")
    config.logs_dir = _resolve_config_path("logs_dir", ".logs")

    if scope_paths:
        roots = [_resolve_path(base, item) for item in scope_paths]
        config.scope_roots = roots
    else:
        config.scope_roots = [
            _resolve_path(base, item) for item in config.scope_roots
        ]

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
    path = Path(path)
    suffix, text = _read_automation(path)
    if suffix == DSL_EXTENSION:
        try:
            return parse_dsl(text)
        except GraphParseError as exc:
            raise GraphParseError(f"{path}: {exc}") from exc

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GraphParseError(f"{path}: invalid automation JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise GraphParseError(f"{path}: automation JSON root must be an object")

    normalized = normalize_document_data(
        raw, default_id=path.stem or None
    )
    try:
        return parse_graph(normalized)
    except GraphParseError as exc:
        raise GraphParseError(f"{path}: {exc}") from exc


def _iter_automation_paths(raw: str | Path) -> list[Path]:
    """Expand an ``--include`` file or directory into automation files."""
    path = Path(raw)
    if path.is_dir():
        return sorted(
            item
            for item in path.iterdir()
            if item.is_file()
            and item.suffix.lower() in (_JSON_SUFFIX, DSL_EXTENSION)
        )
    return [path]


def _load_included_documents(paths: list[str] | None) -> list[WorkflowDocument]:
    documents: list[WorkflowDocument] = []
    for raw in paths or []:
        for path in _iter_automation_paths(raw):
            documents.append(_load_document(path))
    return documents


def _register_catalog_fallback(
    runtime: Runtime,
    documents: list[WorkflowDocument],
    config: AppConfig,
) -> None:
    """CLI-only convenience: load ``<base_dir>/<graph_id>.ldsq|json``."""
    existing = set(runtime.automation_ids)
    wanted: set[str] = set()
    for document in documents:
        for node in document.nodes:
            graph_id = node.params.get("graph_id")
            if (
                isinstance(graph_id, str)
                and graph_id
                and graph_id not in existing
            ):
                wanted.add(graph_id)
    for graph_id in sorted(wanted):
        for suffix in (DSL_EXTENSION, _JSON_SUFFIX):
            candidate = Path(config.base_dir) / f"{graph_id}{suffix}"
            if not candidate.exists():
                continue
            try:
                document = _load_document(candidate)
                _enable_recursive_preview(document)
                runtime.register(document, automation_id=graph_id)
            except Exception as exc:
                print(
                    f"warning: catalog fallback for {graph_id!r} failed "
                    f"({candidate}): {exc}",
                    file=sys.stderr,
                )
                continue
            print(
                f"warning: graph_id {graph_id!r} was not in the catalog; "
                f"loaded {candidate}",
                file=sys.stderr,
            )
            break


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
    normalized = normalize_document_data(
        raw, default_id=Path(path).stem or None
    )
    try:
        return document_to_dict(parse_graph(normalized))
    except GraphParseError as exc:
        raise GraphParseError(f"{path}: {exc}") from exc


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


def _enable_recursive_preview(document: WorkflowDocument) -> None:
    """Opt nested nodes into preview when the CLI is dry-running."""
    for node in document.nodes:
        if node.type in ("flow.loop_batch", "flow.subflow"):
            node.params["recursive_preview"] = True


def _planning_callback(
    args: argparse.Namespace,
) -> Callable[[Any, Any, str | None], Any]:
    """Return a recursive planner callback for CLI dry-runs."""
    from lokdocsqr.core.planning.builder import build_action_plan

    policy = _policy_from_args(args)

    def callback(
        child_ctx: Any, child_graph: Any, graph_id: str | None
    ) -> Any:
        del graph_id
        return build_action_plan(child_graph, child_ctx, policy)

    return callback


def _make_runtime(
    args: argparse.Namespace,
    config: AppConfig,
    *,
    default_dry_run: bool | None = None,
    events: EventBus | None = None,
    single_instance: bool = False,
) -> Runtime:
    return Runtime(
        config,
        policy=_policy_from_args(args),
        events=events if events is not None else EventBus(),
        single_instance=single_instance,
        default_dry_run=default_dry_run,
        verify_callback=_verify_callback(args),
    )


def _audit_log(config: AppConfig) -> AuditLog:
    logs_dir = (
        Path(config.logs_dir)
        if config.logs_dir is not None
        else Path(config.base_dir) / ".logs"
    )
    return AuditLog(expand_user_path(logs_dir) / _AUDIT_FILENAME)


def _scope_manager(config: AppConfig) -> ScopeManager:
    roots = [
        expand_user_path(item).resolve() for item in config.scope_roots
    ]
    if not roots:
        roots = [expand_user_path(config.base_dir).resolve()]
    writable = list(roots)
    trash = getattr(config, "trash_dir", None)
    if trash is not None:
        trash_path = expand_user_path(trash).resolve()
        try:
            trash_path.relative_to(expand_user_path(config.base_dir).resolve())
        except ValueError:
            pass
        else:
            if trash_path not in writable:
                writable.append(trash_path)
    return ScopeManager(
        WorkspaceScope(
            roots=list(roots),
            writable=writable,
            allow_symlinks=False,
            allow_parent_traversal=bool(
                getattr(config, "allow_parent_traversal", False)
            ),
        )
    )


def _ensure_nodes() -> None:
    if not registry.list():
        discover_plugins()


def _assert_graph_buildable(document: WorkflowDocument) -> None:
    """Fail before a run starts if the graph cannot be built.

    ``validate_graph`` does not look up node handlers, so an unknown node
    type currently surfaces only later inside ``Runtime._execute`` and is
    reported as an exception. Building here keeps the CLI diagnostic clean
    and avoids logging tracebacks for ordinary user errors.
    """
    _ensure_nodes()
    states = resolve_states(document, registry)
    build_graph(document, states, registry)


def _prepare_documents_for_mode(
    documents: list[WorkflowDocument],
    *,
    dry_run: bool | None,
) -> None:
    """Apply the CLI-only dry-run wiring to every document.

    The flag is harmless during a real run and required whenever the
    effective mode becomes a dry-run: CLI ``--dry-run``, a document
    ``default_dry_run`` value, a config default, or a trigger's
    ``params.dry_run``. The library keeps its strict opt-in behaviour.
    """
    del dry_run
    for document in documents:
        _enable_recursive_preview(document)


def _collect_documents_from_args(
    paths: list[str | Path],
) -> list[WorkflowDocument]:
    """Expand automation files/directories into ``WorkflowDocument``s."""
    documents: list[WorkflowDocument] = []
    for raw in paths:
        candidates = _iter_automation_paths(raw)
        if not candidates:
            raise GraphParseError(
                f"{raw}: no {_JSON_SUFFIX}/{DSL_EXTENSION} automations found"
            )
        for path in candidates:
            documents.append(_load_document(path))
    return documents


def _install_console_event_logger(
    bus: EventBus,
    *,
    as_json: bool = False,
) -> None:
    """Print every runtime event to the console for serve/daemon mode."""

    def _emit(event_type: str, payload: dict[str, Any]) -> None:
        if as_json:
            record = {"event": event_type, **payload}
            log = record.pop("action_log", None)
            if log is not None:
                record["action_log"] = log.to_dict()
            print(
                json.dumps(record, ensure_ascii=False, default=str),
                flush=True,
            )
            return

        run_id = payload.get("run_id", "-")
        if event_type == EVENT_RUN_STARTED:
            mode = "dry-run" if payload.get("dry_run") else "run"
            print(
                f"[run] {run_id} started "
                f"automation={payload.get('automation_id', '-')} ({mode})",
                flush=True,
            )
        elif event_type == EVENT_NODE_STARTED:
            print(
                f"[node] {run_id} {payload.get('node_id', '-')} "
                f"started (attempt {payload.get('attempt', 1)})",
                flush=True,
            )
        elif event_type == EVENT_NODE_FINISHED:
            print(
                f"[node] {run_id} {payload.get('node_id', '-')} "
                f"{payload.get('state', '-')} "
                f"({payload.get('attempts', 0)} attempt(s), "
                f"{float(payload.get('duration', 0.0)):.4f}s)",
                flush=True,
            )
        elif event_type == EVENT_AUDIT_LOGGED:
            log = payload.get("action_log")
            if log is not None:
                print(f"[audit] {run_id} {_format_log(log)}", flush=True)
        elif event_type == EVENT_RUN_FINISHED:
            state = "ok" if payload.get("success") else "failed"
            print(f"[run] {run_id} finished {state}", flush=True)
            for error in payload.get("errors") or []:
                print(f"[run] {run_id} error: {error}", flush=True)
            for warning in payload.get("warnings") or []:
                print(f"[run] {run_id} warning: {warning}", flush=True)

    bus.subscribe(EVENT_RUN_STARTED, lambda payload: _emit(EVENT_RUN_STARTED, payload))
    bus.subscribe(EVENT_NODE_STARTED, lambda payload: _emit(EVENT_NODE_STARTED, payload))
    bus.subscribe(
        EVENT_NODE_FINISHED,
        lambda payload: _emit(EVENT_NODE_FINISHED, payload),
    )
    bus.subscribe(
        EVENT_AUDIT_LOGGED,
        lambda payload: _emit(EVENT_AUDIT_LOGGED, payload),
    )
    bus.subscribe(
        EVENT_RUN_FINISHED,
        lambda payload: _emit(EVENT_RUN_FINISHED, payload),
    )


def _serve_until_signal(runtime: Runtime) -> None:
    """Block in ``Runtime.serve`` and stop gracefully on SIGINT/SIGTERM."""
    stop_event = threading.Event()
    previous: dict[int, Any] = {}

    def _handler(signum: int, frame: Any) -> None:
        del signum, frame
        stop_event.set()

    for name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, _handler)
        except (ValueError, OSError):  # not in the main thread / unsupported
            previous.pop(signum, None)

    try:
        runtime.serve(stop_event)
    finally:
        for signum, handler in previous.items():
            with contextlib.suppress(ValueError, OSError):
                signal.signal(signum, handler)


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    document = _load_document(Path(args.automation))
    _apply_scope_override(document, args, config.base_dir)

    warnings: list[str] = []
    errors = list(validate_graph(document, warnings=warnings))
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if errors:
        if getattr(args, "first_error", False):
            errors = errors[:1]
        raise GraphValidationError("; ".join(errors))

    included = _load_included_documents(getattr(args, "include", []))
    for included_document in included:
        _apply_scope_override(included_document, args, config.base_dir)
    for included_document in included:
        included_warnings: list[str] = []
        included_errors = list(
            validate_graph(included_document, warnings=included_warnings)
        )
        for warning in included_warnings:
            print(
                f"warning: {included_document.metadata.id}: {warning}",
                file=sys.stderr,
            )
        if included_errors:
            prefix = f"{included_document.metadata.id}: "
            raise GraphValidationError(
                "; ".join(prefix + error for error in included_errors)
            )

    all_documents = [document, *included]
    _prepare_documents_for_mode(all_documents, dry_run=args.dry_run)
    for candidate in all_documents:
        _assert_graph_buildable(candidate)

    output_format = _output_format(args)
    as_json = output_format == "json"
    explain = bool(getattr(args, "explain", False))
    daemon = bool(getattr(args, "daemon", False))
    if daemon:
        setup_logging(config)
    event_bus = EventBus()
    runtime = _make_runtime(
        args,
        config,
        default_dry_run=args.dry_run if daemon else None,
        events=event_bus,
        single_instance=daemon,
    )
    runtime._planning_callback = _planning_callback(args)
    for included_document in included:
        if included_document.metadata.id == document.metadata.id:
            continue
        try:
            runtime.register(included_document)
        except ValueError as exc:
            print(f"warning: {exc}", file=sys.stderr)
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
    if getattr(args, "catalog_fallback", False):
        _register_catalog_fallback(
            runtime, [document, *included], config
        )

    if not daemon:
        result = runtime.run_once(automation_id, dry_run=args.dry_run)
        _print_run_result(result, as_json=as_json, explain=explain)
        return 0 if result.success else 1

    # Daemon mode acquires the single-instance lock before doing any work,
    # executes the automation once, then keeps serving declared triggers.
    runtime.start(start_triggers=False)
    try:
        if not as_json:
            _install_console_event_logger(event_bus, as_json=False)
        result = runtime.run_once(automation_id, dry_run=args.dry_run)
        _print_run_result(result, as_json=as_json, explain=explain)
        if as_json:
            _install_console_event_logger(event_bus, as_json=True)
        runtime.start_triggers()
        print(
            "daemon running; press Ctrl+C to stop",
            file=sys.stderr if as_json else sys.stdout,
            flush=True,
        )
        _serve_until_signal(runtime)
    finally:
        runtime.stop()
    return 0


def _cmd_compile(args: argparse.Namespace) -> int:
    payload = _compile_payload(Path(args.automation))
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    output = getattr(args, "output", None)
    if output is not None:
        destination = Path(output)
        safe_fs.atomic_write(destination, text)
        print(f"compiled -> {destination}", file=sys.stderr)
    elif getattr(args, "format", "json") == "text":
        metadata = payload.get("metadata") or {}
        print(
            f"compiled {metadata.get('id', '')} "
            f"({len(payload.get('nodes') or [])} nodes, "
            f"{len(payload.get('edges') or [])} edges)"
        )
    else:
        print(text, end="")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    document = _load_document(Path(args.automation))
    warnings: list[str] = []
    errors = list(validate_graph(document, warnings=warnings))

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    for included_document in _load_included_documents(
        getattr(args, "include", [])
    ):
        if included_document.metadata.id == document.metadata.id:
            continue
        included_warnings: list[str] = []
        included_errors = list(
            validate_graph(
                included_document, warnings=included_warnings
            )
        )
        for warning in included_warnings:
            print(f"warning: {warning}", file=sys.stderr)
        if not included_errors:
            try:
                _assert_graph_buildable(included_document)
            except GraphValidationError as exc:
                included_errors.append(str(exc))
        prefix = f"{included_document.metadata.id}: "
        errors.extend(prefix + error for error in included_errors)

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
    if _output_format(args) == "json":
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
    setup_logging(config)
    documents = _collect_documents_from_args(list(args.automation))
    included = _load_included_documents(getattr(args, "include", []))
    for document in [*documents, *included]:
        _apply_scope_override(document, args, config.base_dir)

    seen: set[str] = set()
    unique: list[WorkflowDocument] = []
    for document in [*documents, *included]:
        if document.metadata.id in seen:
            continue
        seen.add(document.metadata.id)
        unique.append(document)

    _prepare_documents_for_mode(unique, dry_run=args.dry_run)
    for document in unique:
        warnings: list[str] = []
        errors = list(validate_graph(document, warnings=warnings))
        for warning in warnings:
            print(
                f"warning: {document.metadata.id}: {warning}",
                file=sys.stderr,
            )
        if errors:
            raise GraphValidationError(
                f"{document.metadata.id}: " + "; ".join(errors)
            )
        _assert_graph_buildable(document)

    output_format = _output_format(args)
    as_json = output_format == "json"
    explain = bool(getattr(args, "explain", False))
    daemon = bool(getattr(args, "daemon", False))

    event_bus = EventBus()
    runtime = _make_runtime(
        args,
        config,
        default_dry_run=args.dry_run,
        events=event_bus,
        single_instance=True,
    )
    runtime._planning_callback = _planning_callback(args)

    automation_ids: list[str] = []
    for document in unique:
        automation_id = runtime.register(document)
        automation_ids.append(automation_id)
        print(
            f"registered {automation_id}",
            file=sys.stderr if as_json else sys.stdout,
            flush=True,
        )

    if getattr(args, "catalog_fallback", False):
        _register_catalog_fallback(runtime, unique, config)

    if daemon:
        # Lock first, then run each automation once, then start triggers.
        runtime.start(start_triggers=False)
        try:
            if not as_json:
                _install_console_event_logger(event_bus, as_json=False)
            for automation_id in automation_ids:
                result = runtime.run_once(
                    automation_id, dry_run=args.dry_run
                )
                _print_run_result(
                    result, as_json=as_json, explain=explain
                )
            if as_json:
                _install_console_event_logger(event_bus, as_json=True)
            runtime.start_triggers()
            print(
                "daemon running; press Ctrl+C to stop",
                file=sys.stderr if as_json else sys.stdout,
                flush=True,
            )
            _serve_until_signal(runtime)
        finally:
            runtime.stop()
        return 0

    _install_console_event_logger(event_bus, as_json=as_json)
    print(
        "serving; press Ctrl+C to stop",
        file=sys.stderr if as_json else sys.stdout,
        flush=True,
    )
    try:
        _serve_until_signal(runtime)
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
    if isinstance(explicit, str):
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
