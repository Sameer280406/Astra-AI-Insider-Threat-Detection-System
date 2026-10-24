# Astra System Architecture

## 1. System Topology & Data Flow

Astra is an end-to-end, multi-tenant AI insider threat intelligence platform designed to ingest high-volume enterprise telemetry, calculate causal sliding-window features, run multi-model anomaly detection, and deliver actionable alerts and containment workflows.

```
[ CERT r4.2 Telemetry Streams ]
(Logon, Device/USB, HTTP, File)
              |
              v
[ CERT Replay / Ingestion Engine ] ---> Authenticated HTTP API (/api/v1/events)
(Accelerated clock, Idempotent Dedup)
              |
              v
[ Apache Kafka Topics ]
  - `cert-events` (partitioned by user)
  - `cert-features`
  - `cert-events-dlq` (dead-letter queue)
              |
              v
[ Apache Spark Streaming Processor ]
  - 8 Causal Sliding Window Features
  - Zero Lookahead Temporal Guarantee
  - Data Quality Counters (Malformed, Duplicates, Lateness)
              |
              v
[ Model Gateway & Trained Model Ensemble ]
  - StandardScaler (scaler.joblib)
  - Base Detectors: Isolation Forest, Autoencoder, LSTM, GNN
  - Meta-Learner: XGBoost (ensemble.joblib)
  - Output: Calibrated Risk Score (0-100) + Feature Explanations
              |
              v
[ Core Backend Engine (FastAPI) ]
  - Rate Limiting Middleware (Exempt, Read, Write, Simulate)
  - Tenant Repository (PostgreSQL / SQLite with Tenant Isolation)
  - Containment & Forensics Policy Engine (Immutable Cryptographic Capsules)
  - Server-Sent Events (SSE) Audit Broadcaster (Debounced 400ms)
              |
              v
[ Modern Reactive UI (Next.js / Vite) ]
  - Overview Dashboard (Deduplicated GETs, 1000ms Read Cache)
  - Live Events & CERT Replay Controller
  - Threat Timeline & Behavior Analytics
  - Investigations Workspace & Evidence Bookmarking
  - Incident Containment & Forensic Sealing Console
```

---

## 2. Boundaries & Isolation

### 2.1 Multi-Tenancy & Data Segregation
- Every business document in `records` has a required foreign key `tenant_id` linked to `organizations.id`.
- The `TenantRepository` scopes all queries with `Record.tenant_id == tenant_id`. Cross-tenant reads and mutations are strictly forbidden and validated in `tests/test_system.py`.
- Platform and organization administrators are bound to their respective organization contexts; there are no hidden cross-tenant overrides.

### 2.2 Model Gateway Boundaries
- The `ModelGateway` acts as an abstraction layer between event persistence and inference.
- Supported modes:
  - `trained`: Loads real trained joblib artifacts from `backend/app/models/trained_models/` without synthetic mocks.
  - `remote`: Issues authenticated HTTP requests to external model microservices conforming to `PredictionContract v1.0`.
  - `mock`: Explicitly labeled deterministic fallback for local UI development only.

### 2.3 Containment & Evidence Immutability
- Incidents exceeding critical thresholds trigger automated or analyst-approved containment policies.
- An evidence capsule (`evidence_capsules`) is created, freezing the event history, entity profile, and model consensus.
- When closed, a cryptographic digital seal (`capsule_seals`) locks the capsule using SHA-256 content hashes, preventing further modifications.

---

## 3. Rate-Limiting & Traffic Storm Protection

The request path is hardened against polling storms and duplicate simulations:
1. **Frontend GET Request Deduplication**: In-flight HTTP GET requests share a single underlying `Promise`. If 8 dashboard widgets query `/api/v1/overview` concurrently, exactly 1 network request is dispatched.
2. **Read Response Caching**: Idempotent GET responses are cached in memory for 1,000 ms.
3. **SSE Debouncing**: Background updates from `/api/v1/stream` are debounced with a 400 ms trailing edge timer.
4. **Per-Category Rate Limit Buckets**:
   - `exempt` (health, SSE, docs): 1,200 requests/minute.
   - `auth` (login, onboard): 20 requests/minute.
   - `stream` (event polling): 30 requests/minute.
   - `simulate` (synthetic generation, scoped per user): 60 requests/minute.
   - `write` (mutations, policy updates): 120 requests/minute.
   - `read` (standard GETs): 300 requests/minute.
5. **Simulation Concurrency Locks**: Concurrent simulations for the same user return `429 Too Many Requests` with a `Retry-After: 1` header, preventing duplicate event generation.

---

## 4. Module Directory Structure

```
Astra/
├── backend/
│   ├── app/
│   │   ├── api/               # API routes (routes.py, pipeline_routes.py)
│   │   ├── core/              # Config, DB, Security, Rate Limiter
│   │   ├── domain/            # Entities, Forensics, TenantRepository
│   │   ├── models/            # ModelGateway, TrainedAdapter, starter_service
│   │   │   └── trained_models/ # Joblib model weights & thresholds.json
│   │   └── services/          # Kafka, Spark, CERT Replay, Containment
│   └── tests/                 # Unit & integration test suite (30 passing tests)
├── components/                # React UI components (astra.tsx, containment.tsx)
├── data/                      # Reference CERT r4.2 CSV files & insiders.csv
├── lib/                       # client.ts (dedup, cache, backoff), navigation.ts
├── ml_training/               # Evaluator script, report.json, schema validator
├── services/                  # Standalone spark_streaming.py script
└── docker-compose.streaming.yml # Zookeeper, Kafka, Spark cluster configuration
```
