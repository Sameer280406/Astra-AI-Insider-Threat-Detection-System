# Validation record

Build date: 2026-09-30.

## Executed successfully

- Backend pytest: **14 passed**. Includes event-to-prediction-to-alert/case workflow, deduplication, tenant isolation for entities/incidents/capsules, viewer/analyst response denial, approval/release idempotency, preserved mock management channels, all three policy modes, independent security-rule gating, selective pre/post snapshots, source mutation isolation, session revocation/CSRF header, deterministic inference, remote-model HTTP contract using a transport fixture, and rollback on model failure.
- Alembic 0001 → 0002 → 0003 applied to SQLite. Evidence update/delete and post-seal insertion fail through database triggers.
- TypeScript strict compilation: passed.
- Standard Next.js production build, connected API mode: passed, including static root and dynamic screen routes.
- Vinext Site build, synthetic browser mode: passed. Recharts contributes to a client chunk warning above 500 KB; this is a performance optimization item, not a build error.
- Synthetic seed generated organizations/users, 12 employees, 12 endpoints, resources, 84 events, predictions, alerts, investigations and 3 P1 evidence capsules.

## Not executed here

- Docker Compose and PostgreSQL runtime: Docker/PostgreSQL executables are unavailable in this workspace. PostgreSQL RLS, grants and PostgreSQL-specific concurrency/trigger paths require a deployment smoke test.
- Browser visual/interaction automation: the required browser-control skill was unavailable. Source/type/build checks do not establish visual or accessibility conformance.
- Real trained model services: the remote contract path was exercised with deterministic fixture responses, not trained models.
- Real EDR/network/identity changes: intentionally not implemented; all adapters simulate actions.
- Load/security penetration testing and live SSO/billing/SIEM/Kafka: not performed.

## Required first Docker smoke test

1. Copy and configure `.env`; run `docker compose up --build -d`.
2. Confirm `docker compose ps` reports healthy PostgreSQL/Redis/backend.
3. Seed and log in; simulate an event; inspect P1 incident and capsule.
4. Verify a second organization cannot read first-organization ids.
5. Approve/release with a SOC manager and verify a viewer receives 403.
6. Wait for a short evidence window and verify final seal through the worker.
7. Switch to the remote contract fixture or trained APIs and repeat scoring without UI edits.
