# Product capability map

| # | Capability | Implementation |
|---|---|---|
| 1 | Individual baseline | Recent entity event mean persisted in feature snapshot |
| 2 | Drift | Current egress minus historical mean |
| 3 | Temporal detection | LSTM adapter and chronological event sequence; mock until supplied |
| 4 | Peer analytics | Department mean/std deviation feature |
| 5 | Graph anomaly | GNN adapter with observed graph request; mock until supplied |
| 6 | Fusion | Ensemble adapter with four validated detector outputs |
| 7 | Dynamic risk | Persisted 0–100 score, updated per event |
| 8 | Velocity | Latest fused score delta |
| 9 | Categorization | Model threatType/classification |
| 10 | Early warning | Escalation velocity and numeric policy rules |
| 11 | Explanations | Alert evidence and persisted prediction explanations |
| 12 | Contributions | Ranked feature bars in entity panel |
| 13 | Timeline | Filterable event-time reconstruction |
| 14 | Evidence explorer | Event details, immutable capsules and bookmarks |
| 15 | Consensus | Individual scores, alert and critical gate consensus |
| 16 | Live events | Authenticated SSE-triggered refresh |
| 17 | Live leaderboard | Re-sorted entity risk on committed changes |
| 18 | Relationship graph | Clickable user-device-resource edges derived from events |
| 19 | Streaming alerts | SSE audit notifications update alert lists |
| 20 | Spike escalation | Configurable delta threshold plus P1 critical policy |
| 21 | Investigations | Persisted case workspaces |
| 22 | Assignment | Tenant-validated analyst selection |
| 23 | Notes | Authored/timestamped notes with database row lock |
| 24 | Bookmarks | Validated event references |
| 25 | Status workflow | Enforced state transitions |
| 26 | Similar incidents | Same entity/title-token overlap; no semantic model claim |
| 27 | Reports | JSON evidence/case report and printable summary |
| 28 | Feedback | Verdict/reason/prediction references persisted for external retraining |
| 29 | Tenancy | Repository scoping + PostgreSQL business-table RLS |
| 30 | Onboarding | New isolated organization and administrator |
| 31 | RBAC | Six roles; no implicit tenant bypass |
| 32 | Tiers | Configured Starter/Team/Enterprise entitlements |
| 33 | Metering | Monthly event counts and seat limits enforced server-side |
| 34 | Billing-ready | Tenant plan/configuration; no payment provider |
| 35 | Posture | Organization mean risk and alert metrics |
| 36 | Departments | Grouped risk chart/table |
| 37 | Connectors | HTTP event ingestion and connector port; other adapters not active |
| 38 | Webhook/SIEM/SOAR | Configuration records and extension boundary only |
| 39 | Policies | Numeric rules with enabled state and severity |
| 40 | Model registry | Mock/remote registry; environment-configured endpoints |
| 41 | Containment | Advisory/approval/automatic modes and mock adapters |
| 42 | Forensics | Selective append-only capsules, post-event collection, final seals |
