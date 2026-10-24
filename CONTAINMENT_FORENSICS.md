# Containment & Forensics

## Responsibility chain

Models → risk engine → policy engine → containment orchestrator → endpoint/network/identity adapters. Models only return Prediction objects. The response adapters are isolated in `services/response_adapters.py`; all included adapters are simulations.

## Policy behavior

- **advisory:** opens P1, freezes evidence, notifies SOC, records recommendation; no automatic quarantine.
- **analyst_approval:** opens P1 pending approval. A SOC manager/admin must provide an approval reason.
- **automatic_critical:** quarantines only when risk threshold, minimum confidence, minimum detector consensus, and optional independent deterministic rule all pass. A failed gate leaves the incident pending approval.

Consensus is the number of four detector risk scores at or above the policy’s critical threshold. Rule corroboration excludes riskScore and riskVelocity rules because those are derived from the same model outputs. Analyst approval is an explicit authorized override of automatic gates and is audited.

A policy can request simulated session termination or credential restriction following successful quarantine. Quarantine preserves explicit management channel identifiers; empty/wildcard allowlists are rejected. Real network adapters must resolve those identifiers into exact firewall allowlists, verify the management path before isolation, support operation-id deduplication, and reconcile actual provider state. The application does not implement malware removal.

## State and idempotency

Advisory or pending_approval → quarantined → released. Escalation adds SOC-manager queue state. Duplicate approvals/releases do not repeat mock actions. Active incidents are grouped by tenant and endpoint; later critical events extend their risk history and evidence within the defined window. A released incident remains preserved; a later critical event can create a new incident.

Each automated/manual operation stores an action result plus append-only audit entry. Mode, gates, predictions, corroborating rules and management allowlist are snapshotted for review. Endpoint state is explicitly labeled simulated in UI and adapter results.

## Selective evidence retention

Capsule scope is relevant entity OR endpoint, configured pre/post event-time windows, and selected categories: authentication, process, command, file, USB, network, privilege, model explanation. No whole-machine log scrape is implemented.

The immutable capsule manifest contains the triggering event reference, policy snapshot, time windows, predictions and feature explanations. Pre-event and triggering telemetry are copied into append-only evidence rows, so later source-record edits do not alter evidence. New relevant post-event telemetry is copied while the intake window remains open. On delayed triggers, intake remains open for a configured interval after detection, while included event timestamps must still lie within the original event-time window.

A separate forensics worker seals expired capsules. The seal contains the ordered evidence ids and hashes plus the manifest hash. Database guards reject update/delete of evidence and insertion after a seal. Capsule API recomputes hashes to check consistency. A sealed capsule is never reopened or rewritten. Later evidence requires a new capsule/incident rather than altering the preserved record.

Collection levels baseline/elevated/critical request progressively richer category collection through a mock endpoint adapter. These are collection requests only; no operating-system agent is installed. Normalizing a real EDR event into EventInput requires an operator-provided connector.

## Trust limits and production work

Application-level immutability and database triggers protect against ordinary API mutation. A privileged database owner can still modify the database. For stronger forensic assurance, export seals to independently controlled object-lock/WORM storage and use signed timestamps and chain-of-custody processes. Define legal holds, access retention, privacy controls, and verified provider recovery before real deployment.

Only mock side effects execute synchronously in the request transaction. Real provider actions must move behind a durable outbox/worker with retry, circuit breaker, operation reconciliation and compensating-state handling. A database rollback cannot undo a real network quarantine. This code intentionally does not claim real EDR safety from mock tests.

Release currently releases endpoint network isolation only. Credential restoration and session recovery are separate provider-specific workflows; the release result states this explicitly.

## UI walkthrough

Containment & forensics → Response policy → choose mode/gates/windows/categories → save. Live events → Simulate event → return to P1 queue → select incident. Inspect policy gates, risk evolution, management channels, capsule hashes, triggering/pre/post evidence, frozen model explanations and action trail. Approve/release/escalate requires a reason. Download the capsule JSON for your report or downstream evidence system.
