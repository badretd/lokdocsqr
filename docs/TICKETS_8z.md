# TICKETS_8z.md — individual tickets recorded at gate 8z
Updated as they appear. Do not close in 8z without an explicit operator decision.
Remind at: closing 8z, switching to a new chat.

## TICKET-8z-T0: fs.copy/move/rename._plan — payload override
**Status:** BLOCKED-BY-MISSING-FILE (needs nodes/fs_ops/_base.py)
**Symptom:** _plan reads self.template/self.conflict_policy rather than
payload.get(...). When the node is not recreated with the required __init__ arguments,
payload values are lost. Possible H2-parity violation:
the planner shows one path, the executor another.
**Action:** attach _base.py → read _render_target/_plan →
issue a verdict “bug”/“instance-level API” → fix in 8z-fix or 8z1.
**Priority:** medium (depends on real bug verdict)

## TICKET-8z-ActionLog-from_dict: audit integrity
**File:** core/models.py::ActionLog.from_dict
**Symptom:** silently defaults risk=RISK.LOW on an invalid value
(except ValueError), but fails on a missing action_type.
Asymmetry required/lenient → potential 9x audit integrity.
**Priority:** low (audit path, not runtime)

## TICKET-8z-RunResult-to_dict: payload dropped
**File:** core/models.py::RunResult.to_dict
**Symptom:** NodeResult.payload (Phase 5c) is not serialized.
If payload is important for audit — data loss.
**Priority:** low (audit completeness)

## TICKET-8z-ActionLog-to_dict: field loss
**File:** core/models.py::ActionLog.to_dict
**Symptom:** original_path, trash_path, sidecar_path, hash_before
are not serialized; from_dict does not restore them. Round-trip loses data.
**Priority:** low (audit completeness)

## TICKET-8z-FlowRetry-port-types: bool contract
**File:** nodes/flow/retry.py::_execute
**Symptom:** returns retry=None/pass=None for the irrelevant branch,
declared type — boolean. H6 checks keys, not types.
**Priority:** low (documented D)

## TICKET-8z-engine-double-validate: 7d-b unification
**File:** core/executor/engine.py::_check_policy_and_verify
**Symptom:** validate_proposal is called twice (before decide and on
edited after EDIT). Not a bug; unify at 7d-b.
**Priority:** low (cleanup)

## TICKET-8z-ActionProposal-post_init: cleanup
**File:** core/models.py::ActionProposal.__post_init__
**Symptom:** recreates target_paths/payload even when they are valid;
will go away with the class when it is removed in 7d-b.
**Priority:** low (will disappear when the class is removed)

## TICKET-8z-API_CONTRACTS-stricter-policy: no-test-no-line
**Status:** CLOSED in 8z (see item 3 below)
**Action:** M6/M7/M9 remain deferred in v1.2; the 8z verifier creates
tests; v1.3 (gate 8z) flips to enforced.