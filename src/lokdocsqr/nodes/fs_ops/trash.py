"""fs.trash node — Delete to Trash / Quarantine.

Never deletes. Moves files into ``ctx.config["trash_dir"]`` via
``safe_fs.trash`` and writes a sidecar JSON with hash, size, TTL and
lineage. TTL enforcement (the actual expiry sweep) is the responsibility
of ``core/runtime.py``, not of this node — this node only records the
planned expiry in the sidecar.

Invariants honoured here:
  * invariant 5 — both ``run`` and ``dry_run`` are declared explicitly.
  * invariant 3 — no deletion API is called anywhere in this file.
  * invariant 9 — every mutation goes through ``safe_fs``; read-only
    probes (``Path.stat``, ``safe_fs.hash_file``) are allowed.

Return contract (matches fs_ops convention, cf. ``fs_ops.rename``):
  * ``run`` returns the appended ``ActionLog``.
  * ``dry_run`` returns an unappended ``ActionLog``.
Both ``run`` and ``dry_run`` therefore expose ``action_type``,
``dry_run``, ``trash_path``, ``sidecar_path``, ``hash_before`` and
``reason`` as attributes — exactly what ``tests/trash/*`` and
``conftest.run_trash`` consume.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import ConfigError, ScopeViolation
from lokdocsqr.core.models import ActionLog, Risk
from lokdocsqr.core.node_api.base import Node
from lokdocsqr.core.node_api.decorators import register_node

from . import _common


_ONE_DAY_SECONDS = 24 * 3600
_DEFAULT_TTL_SECONDS = 30 * _ONE_DAY_SECONDS


def _as_payload(payload: Any) -> dict[str, Any]:
    """Normalize positional / dict call shapes.

    The node contract is ``payload: dict``, but several call sites
    (``tests/trash/conftest.run_trash``, property tests) invoke
    ``run(ctx, path)`` / ``dry_run(ctx, path)`` directly with a
    ``Path`` or ``str``. Accept both: a path-like becomes
    ``{"path": <that>}``. Dicts pass through untouched.
    """
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, (str, bytes, Path)):
        return {"path": payload}
    return payload  # type: ignore[return-value]


def _resolve_trash_dir(ctx) -> Path:
    """Read ``ctx.config["trash_dir"]`` (dict-style or attribute-style).

    Raises ``ConfigError`` (not ``ScopeViolation``) when missing — the
    operator owns this value, not the user of the node, so a missing
    setting is a configuration bug and should be diagnosed as one.
    """
    cfg = getattr(ctx, "config", None)
    value: Any = None
    if cfg is not None:
        try:
            value = cfg["trash_dir"]
        except (KeyError, TypeError):
            value = getattr(cfg, "trash_dir", None)
    if not value:
        raise ConfigError(
            "trash_dir is not configured "
            "(set config.trash_dir to a path inside the workspace scope)"
        )
    return Path(value)


def _resolve_ttl_seconds(ctx, payload: dict[str, Any]) -> int:
    """``payload.ttl_seconds`` > ``ctx.config["trash_default_ttl"]`` > 30 days."""
    raw = payload.get("ttl_seconds")
    if raw is not None:
        return int(raw)
    cfg = getattr(ctx, "config", None)
    default: Any = None
    if cfg is not None:
        try:
            default = cfg["trash_default_ttl"]
        except (KeyError, TypeError):
            default = getattr(cfg, "trash_default_ttl", None)
    if default is None:
        return _DEFAULT_TTL_SECONDS
    return int(default)


def _check_trash_dir_in_scope(ctx, trash_dir: Path) -> None:
    """Verify ``trash_dir`` is inside ``ctx.scope``.

    Raises ``ConfigError`` (not ``ScopeViolation``) on failure.

    Justification: the scope gate for **user-supplied** file paths
    raises ``ScopeViolation`` — that tells the automation author
    "you tried to touch a file outside the workspace". The value of
    ``trash_dir``, by contrast, comes from **deployment configuration**.
    A mismatch here is not the user's fault: it is an operator
    misconfiguration (config / scope-roots drift). ``ConfigError``
    is the diagnostic the operator sees; ``ScopeViolation`` would
    misattribute the bug to the user running the automation.
    """
    try:
        ctx.scope.check(str(trash_dir))
    except ScopeViolation as exc:
        raise ConfigError(
            f"trash_dir outside workspace scope: {trash_dir} "
            f"(fix config.trash_dir / scope roots)"
        ) from exc


@register_node("fs.trash")
class TrashNode(Node):
    """Move a single file into the trash directory. Never deletes.

    Parameters (from ``payload``):
        path: str — absolute path of the file to trash (required).
        ttl_seconds: int | None — sidecar TTL; ``None`` → config default.
        reason: str | None — free-text reason recorded in ``ActionLog.reason``;
            does not affect behaviour.

    Return:
        ``ActionLog`` (per fs_ops convention). ``run`` appends it to
        ``ctx.audit``; ``dry_run`` returns it unappended. Callers read
        ``log.action_type``, ``log.dry_run``, ``log.trash_path``,
        ``log.sidecar_path``, ``log.hash_before``, ``log.reason``.
    """

    id = "fs.trash"
    title = "Delete to Trash / Quarantine"
    inputs = ("files",)
    outputs = ("files",)

    # ── run ─────────────────────────────────────────────────────
    def run(self, ctx, payload):
        payload = _as_payload(payload)
        origin = Path(payload["path"])
        _common.check_scope(ctx, str(origin))

        trash_dir = _resolve_trash_dir(ctx)
        _check_trash_dir_in_scope(ctx, trash_dir)

        ttl_seconds = _resolve_ttl_seconds(ctx, payload)
        reason = payload.get("reason") or "trash"

        # Move (never delete). ``safe_fs.trash`` returns the final
        # timestamped path inside ``trash_dir``.
        trash_path = safe_fs.trash(origin, trash_dir)

        # Phase 5c: ``build_trash_sidecar`` returns ``(sidecar_path, payload)``
        # (was ``(payload, sidecar_path)`` in 5b). Unpack in the new order so
        # ``sidecar_payload["hash_before"]`` stays a dict access, not a
        # WindowsPath subscript.
        sidecar_path, sidecar_payload = _common.build_trash_sidecar(
            ctx, origin, trash_path, ttl_seconds
        )

        log = _common.build_action_log(
            run_id=getattr(ctx, "run_id", ""),
            automation_id=getattr(ctx, "automation_id", ""),
            node_id=self.id,
            action_type="trash",
            source=str(origin),
            target=str(trash_path),
            reason=reason,
            confidence=1.0,
            risk=Risk.MEDIUM,
            dry_run=False,
            rollback_eligible=True,
            original_path=str(origin),
            trash_path=str(trash_path),
            sidecar_path=str(sidecar_path),
            hash_before=sidecar_payload["hash_before"],
        )
        ctx.audit.append(log)
        return log

    # ── dry_run ─────────────────────────────────────────────────
    def dry_run(self, ctx, payload) -> ActionLog:
        payload = _as_payload(payload)
        origin = Path(payload["path"])
        _common.check_scope(ctx, str(origin))

        trash_dir = _resolve_trash_dir(ctx)
        _check_trash_dir_in_scope(ctx, trash_dir)

        ttl_seconds = _resolve_ttl_seconds(ctx, payload)
        reason = payload.get("reason") or "trash"

        # Plan destinations FIRST — the sidecar payload records them.
        planned_trash_path = trash_dir / origin.name
        planned_sidecar_path = planned_trash_path.parent / (
            planned_trash_path.name + _common.TRASH_SIDECAR_SUFFIX
        )

        sidecar_payload = _common.build_trash_sidecar_payload(
            ctx, origin, ttl_seconds,
            trash_path=planned_trash_path,
            sidecar_path=planned_sidecar_path,
        )

        log = _common.build_action_log(
            run_id=getattr(ctx, "run_id", ""),
            automation_id=getattr(ctx, "automation_id", ""),
            node_id=self.id,
            action_type="trash",
            source=str(origin),
            target=str(planned_trash_path),
            reason=reason,
            confidence=1.0,
            risk=Risk.MEDIUM,
            dry_run=True,
            rollback_eligible=True,
            original_path=str(origin),
            trash_path=str(planned_trash_path),
            sidecar_path=str(planned_sidecar_path),
            hash_before=sidecar_payload["hash_before"],
        )
        return log