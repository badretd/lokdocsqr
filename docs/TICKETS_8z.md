# TICKETS_8z.md — individual tickets recorded at gate 8z

Updated 2026-11-03 at the 8.6-pre ticket-closeout gate.

8.6-pre disposition: two tickets closed, three explicitly deferred. The
deferred items are not abandoned; each has the phase and reason below.
Only the five 8.6-pre items changed here. The remaining 8z tickets keep
their prior status.

## TICKET-8z-T0: fs.copy/move/rename._plan — payload override
**Status:** DEFERRED to Phase 8.6 — root cause is F-01, not T0.
**Symptom:** `_plan` reads `self.template` / `self.conflict_policy`
rather than the payload. A node configured only through `NodeSpec.params`
can therefore plan a different target than the executor uses.
**Reason for deferral:** T0 is a symptom. The root cause is that the
executor does not seed `NodeSpec.params` into the payload; fixing only
`FsTemplateNode._plan` would leave every other node’s params ignored.
The authoritative fix is F-01 in Phase 8.6.
**Opened:** `TICKET-8.6-root-params` — shared payload resolution with
precedence `params` < edges < port defaults, called by both the planner
and the executor.
**Priority:** medium (deferred by phase boundary).

## TICKET-8z-ActionLog-from_dict: audit integrity
**File:** core/models.py::ActionLog.from_dict
**Status:** CLOSED in 8.6-pre — audit parsing is fail-closed.
**Resolution:** A missing `action_type` still raises `KeyError`. A
present but invalid `risk`, `confidence`, `dry_run`,
`rollback_eligible`, or `extra` now raises `ValueError` instead of being
silently coerced. `AuditLog.iter_all` treats `ValueError` as a malformed
line and skips it with a warning, so corruption cannot become a
plausible LOW-risk record.
**Fail-before-pass-after tests:**
`tests/test_models_audit.py::test_actionlog_from_dict_rejects_corrupt_fields`,
`tests/test_models_audit.py::test_audit_iter_all_skips_corrupt_risk`;
mirrored in `tests/test_models.py`.
**Priority:** low (resolved).

## TICKET-8z-RunResult-to_dict: payload dropped
**File:** core/models.py::RunResult.to_dict
**Status:** DEFERRED until after F-03 (audit contract).
**Symptom:** `NodeResult.payload` (Phase 5c) is not serialized.
**Reason for deferral:** This is audit serialization. The roadmap
requires F-03 (one audit owner, executor populates
`RunResult.action_logs`) to land before serialization tickets are
closed. Closing it first would lock in a result shape that F-03 may
change.
**Priority:** low (deferred by ordering).

## TICKET-8z-ActionLog-to_dict: field loss
**File:** core/models.py::ActionLog.to_dict
**Status:** DEFERRED until after F-03 (audit contract).
**Symptom:** `original_path`, `trash_path`, `sidecar_path`,
`hash_before`, and `dry_run` are not serialized; `from_dict` does not
restore them, so the on-disk audit log loses evidence.
**Reason for deferral:** Same ordering rule as `RunResult-to_dict`:
serialization follows F-03, not the other way round.
**Priority:** low (deferred by ordering).

## TICKET-8z-FlowRetry-port-types: bool contract
**File:** nodes/flow/retry.py::_execute
**Status:** CLOSED in 8.6-pre — fixed.
**Resolution:** `retry` and `pass` now always return real booleans; the
inactive branch returns `False`, never `None`.
**Fail-before-pass-after tests:**
`tests/test_phase_8g.py::test_retry_on_error_true` /
`_false` / `_missing` now assert `isinstance(..., bool)`; the baseline
returns `None` for the inactive branch and fails those assertions.
**Priority:** low (resolved).

## TICKET-8z-engine-double-validate: 7d-b unification
**File:** core/executor/engine.py::_check_policy_and_verify
**Status:** OPEN — Phase 8.6.
**Symptom:** `validate_proposal` is called twice (before decide and on
an edited payload after EDIT). Not a bug; unify at 7d-b.
**Reason:** Any change here touches `core/executor/engine.py`, which is
frozen during 8.6-pre. It is part of the 8.6 engine gate.
**Priority:** low (cleanup).

## TICKET-8z-ActionSummary-post_init: cleanup
**File:** core/models.py::ActionSummary.__post_init__
**Status:** OPEN — retained by design until the 7d-b removal.
**Symptom:** recreates `target_paths` / `payload` even when valid.
**Reason:** The defensive copy is security-relevant for the frozen
policy/preview boundary; the class is scheduled for removal in 7d-b.
**Priority:** low (will disappear with the class).

## TICKET-8z-API_CONTRACTS-stricter-policy: no-test-no-line
**Status:** CLOSED in 8z (see API_CONTRACTS.md v1.3).
**Resolution:** M6/M7/M9 are enforced; the 8z verifier tests exist and
pass. The dangling “see item 3 below” reference was removed.
**Priority:** low (closed).
