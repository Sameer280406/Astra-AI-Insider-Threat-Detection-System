# API contract

Base `/api/v1`; generated OpenAPI at `docs/openapi.json`, live `/openapi.json` and `/docs` on FastAPI. JSON names use the frontend contract’s camelCase. HTTP timestamps are ISO 8601.

## Authentication and tenant scope

POST `/auth/onboard`: organization slug/name, administrator name/email, password (12+).
POST `/auth/login`: organization slug, email, password.
GET `/auth/me`: principal, organization and entitlements.
POST `/auth/logout`: revokes the current JWT session.
GET `/auth/providers`: empty until an OIDC provider is implemented.

JWT is in an HttpOnly, SameSite=Strict cookie. Secure cookies are mandatory in production. Bearer tokens are accepted for trusted clients, but no unaudited token-minting endpoint is exposed. Every mutation requires `X-Astra-Request: 1` and rejects a mismatched Origin. The tenant comes from the validated session; clients cannot select another tenant with a query/header/body.

## Event and intelligence routes

| Method | Route | Purpose |
|---|---|---|
| POST | `/events` | Strict EventInput, full scoring pipeline, 201 |
| GET | `/events?entityId=&limit=&offset=` | Persisted telemetry |
| GET | `/entities`, `/entities/{id}` | Entity risk, features, predictions |
| GET | `/alerts`, `/alerts/{id}` | Explainable alert records |
| GET | `/overview` | Organization metrics |
| GET | `/models` | Model mode and configuration status, not fabricated latency/accuracy |
| GET | `/stream?after=` | Authenticated SSE audit updates, Last-Event-ID supported |
| POST | `/demo/simulate` | Only with ENABLE_DEMO and ingest permission |

EventInput: `eventId, entityId, timestamp, action, resource, device, egressMb, sensitiveFiles, newDevice, category, details`. Unknown top-level fields are rejected. Category: authentication, process, command, file, usb, network, privilege, model_explanation. Details is a bounded primitive-valued metadata map. Do not send secrets or full arbitrary machine logs.

Scoring response: `{event, prediction, alert, duplicate, incidentId}`. Duplicate retry returns `{event, duplicate:true}`. The event id is unique per tenant. Model output failures return 502, not successful empty scores.

Enabled detection rules are evaluated against each event after model scoring.
A rule match can independently trigger an alert and is returned on the alert as
`ruleMatches` (rule id/name, feature, `>=` operator, configured threshold,
observed value, and configured severity); `matchedRules` remains as the
backward-compatible list of names. Event and alert XAI include
`rule_matches`/`rule_summary` to explain the match while explicitly stating
that rule evidence does not change the model risk score or its feature
attributions. Alert severity is the higher of the model-score severity and the
severity of any matching rule. Rule changes apply to future event scoring;
they do not modify prior results.

## Analyst operations

POST `/cases`: title, alertId, optional assignee.
PATCH `/cases/{id}`: status or assignee. Valid transitions: open→investigating; investigating→contained/closed; contained→investigating/closed; closed→investigating.
POST `/cases/{id}/notes`: text.
POST `/cases/{id}/bookmarks`: eventId.
GET `/cases/{id}/similar`: simple same-entity/title-token similarity, not semantic ML search.
POST `/feedback`: alertId, verdict (true_positive|false_positive|needs_review), reason.
GET `/reports/export`: JSON snapshot, audited.
POST `/notifications/{id}/read`: mark tenant notification read (shared organization read state).

## Administration

GET/POST `/rules`; PATCH `/rules/{id}`. Numeric greater-than-or-equal conditions only; no arbitrary expression execution. Rules can flag an event and influence alert severity, but are not model-training parameters. A match is evidence for containment policy evaluation, not by itself a guarantee of containment.
GET/POST `/users`; user creation supports all organization roles except platform_admin, which is bootstrap-only.
GET/POST `/integrations`: configuration records, no outbound activation.
GET `/subscription`: usage/limits/plans. PATCH is demo-only; real billing is not connected.
GET `/audit`: permission-restricted hash-chain records.
GET `/health` is outside `/api/v1`; checks actual DB connection.

## Containment and forensics

GET/PUT `/response/policy`: strict versioned ContainmentPolicy.
GET `/response/incidents`: P1 queue.
GET `/response/incidents/{id}`: incident, frozen policy, gates, actions.
POST `/response/incidents/{id}/approve|release|escalate`: `{reason}` with minimum 5 characters.
GET `/response/capsules/{id}`: immutable manifest, snapshots, hashes, seal and integrity verification result.

Approvals/releases require `contain` permission: soc_manager, org_admin, platform_admin. soc_analyst can investigate and label but cannot quarantine. Model code cannot call response actions.

## RBAC

| Role | Read | Triage / feedback | Export | Audit | Policies / containment | Users / ingest |
|---|---|---|---|---|---|---|
| viewer | yes | no | no | no | no | no |
| auditor | yes | no | yes | yes | no | no |
| soc_analyst | yes | yes | yes | no | no | no |
| soc_manager | yes | yes | yes | yes | yes | no |
| org_admin | yes | yes | yes | yes | yes | yes |
| platform_admin | yes | yes | yes | yes | yes | yes |

All permissions remain within the authenticated organization. Sensitive raw feature/prediction/integration list endpoints deny viewers. No business write endpoint allows changing tenant ownership.

## Error semantics

401 unauthenticated/expired/revoked; 403 missing permission/origin/feature; 404 absent or another tenant’s object; 409 duplicate/conflicting state; 422 schema validation; 429 rate limit; 502 model unavailable; 503 rate limiter unavailable; 500 safe generic error. Clients should retry transient failures with the same source event id.
