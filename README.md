# Astra — AI Insider Threat Intelligence Platform

Astra is an end-to-end, multi-tenant behavioral insider threat detection and forensics workspace. It processes enterprise telemetry streams, calculates causal temporal features, performs ensemble anomaly detection using trained machine learning models, and orchestrates incident containment with immutable forensic evidence sealing.

---

## 1. Quickstart & Deployment

### Prerequisites
- Python 3.11+ (tested on Python 3.12 and 3.14)
- Node.js 18+ and pnpm
- (Optional for full container stack) Docker Desktop with Compose v2

### Option A: Local Development Setup (Fastest)

1. **Configure Environment**:
   ```bash
   cp .env.example .env
   ```
   Set your passwords in `.env`:
   - `JWT_SECRET`: 32+ character random string
   - `DEMO_PASSWORD`: `Mer@paswordlalantap@#1234` (or your chosen password)

2. **Start Backend**:
   ```bash
   cd backend
   python -m pip install -r requirements.txt
   alembic upgrade head
   python -m app.seed
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8008
   ```

3. **Start Frontend** (in a separate terminal from root):
   ```bash
   pnpm install
   pnpm dev
   ```

4. **Sign In**:
   - URL: `http://localhost:3000`
   - Organization slug: `meridian`
   - Email: `org_admin@astra.demo`
   - Password: `Mer@paswordlalantap@#1234` (value of `DEMO_PASSWORD`)

---

### Option B: Docker Stack Deployment

```bash
docker compose up --build -d
docker compose exec backend python -m app.seed
```

To run the Kafka and Spark streaming cluster:
```bash
# Set KAFKA_SCORING_ENABLED=true and a random INTERNAL_INGEST_TOKEN
# (at least 32 characters) in .env, then:
docker compose -f docker-compose.yml -f docker-compose.streaming.yml up --build -d
```

---

## 2. CERT r4.2 Data and Replay

The local CERT source is resolved from `data/r4.2/`, `data/cert_reference/r4.2/`,
or `data/cert/`, before the smaller `data/cert_reference/` fallback. The replay
reader merges source CSVs incrementally and requires each source file to be
chronologically ordered. It does not load the multi-gigabyte HTTP/email files
into memory. Ground-truth labels are loaded separately and filtered to dataset
version 4.2.

### Replay
1. Navigate to **Live events** (`/events`) and use the batch replay operation.
2. By default, replay uses the local Python feature aggregator and model
   adapter. Kafka publication is best-effort and offline Kafka does not stop
   local scoring.
3. Set `KAFKA_SCORING_ENABLED=true` to select the distributed replay path.
   Replay then queues feature-bearing events to Kafka and does not report them
   as scored until the Spark bridge and Kafka scoring worker process them.
4. Configured deterministic rules are evaluated against replay features and
   model risk/velocity. Rule evidence is included in event/alert explanations;
   it remains separate from the model score and model feature attributions.
5. Events with no measured byte size are not assigned synthetic transfer
   amounts. They are represented as zero for the numeric model feature and
   counted in the `events_without_measured_bytes` quality metric.

`POST /api/v1/replay/start` starts a local background replay loop. Its speed
sets the target pacing; it is not an event-time delay simulator. Use
`POST /api/v1/replay/pause`, `/resume`, and `/stop` to control it. Manual
`/replay/batch` requests are rejected while continuous replay is active.

### Ground-truth evaluation (CLI)
Run a bounded sample:
```bash
python ml_training/cert_evaluator.py --max-events 10000 --batch-size 500
```

Omit `--max-events` to process the full dataset. This can be a long-running
workload because the source contains tens of millions of rows. The report at
`ml_training/cert_pipeline_report.json` contains metrics measured during that
run; no recall, false-alert rate, or throughput result is assumed in advance.

---

## 3. Streaming Infrastructure: Kafka & Spark

The optional streaming path is:

```text
CERT replay -> Python causal feature aggregation -> Kafka cert-events
            -> Spark Structured Streaming schema/feature validation
            -> Kafka cert-features -> Kafka scoring worker
            -> tenant-scoped API scoring, rules/XAI, and database
```

The Spark job validates and forwards the feature vector; feature engineering
still occurs in the Python replay aggregator. The consumer commits each Kafka
offset only after scoring succeeds or a rejected message is durably sent to the
dead-letter topic. Delivery is at-least-once; the API's tenant/event uniqueness
check makes retries idempotent. Spark checkpoints are stored in a named volume.

Use the combined Compose files shown above. Verify `/api/v1/pipeline/status`
reports Kafka topics, the active Spark processor application, and the scoring
consumer as ready before enabling replay in Kafka mode. The default local mode
remains available when the streaming services are absent.

The supplied Compose topology is a single-broker, plaintext local reference
stack. It is not a secure high-availability production deployment. Production
requires broker authentication/TLS and ACLs, multiple brokers and suitable
replication, managed secrets, checkpoint/backup policy, monitoring, load tests,
and operational recovery procedures.

---

## 4. Model Training and Inference

The backend's `TrainedAdapter` loads artifacts from
`backend/app/models/trained_models/` when present. Artifact presence does not
prove the model was trained on the downloaded CERT source.

- `ml_training/train_models.py` generates synthetic CERT-aligned vectors. Its
  outputs are isolated under `ml_training/artifacts/synthetic_bootstrap/` and
  are not automatically published to the backend model directory.
- `ml_training/experiments.py` streams CERT telemetry through the same local
  feature aggregator, keeps a deterministic bounded reservoir, applies
  synthetic augmentation only to training data, chooses ratios/thresholds on
  validation data, and reports on a chronological held-out test set.
- `python ml_training/export_and_integrate.py` explicitly runs the controlled
  experiment and exports its resulting artifacts. It may update active backend
  artifacts; validate and back them up before using it in deployment.

No measured CERT recall or production readiness is claimed by this guide.

---

## 5. Rate-Limiting & Traffic Storm Hardening

The rate-limiting architecture prevents 429 errors during dashboard loads and simulation bursts:
1. **Frontend GET Request Deduplication**: In-flight HTTP GET requests share a single `Promise` in `lib/client.ts`. Concurrent requests from dashboard widgets never trigger duplicate network calls.
2. **1000ms Read Cache**: Idempotent GET responses are cached in memory for 1 second.
3. **SSE Debouncing**: Server-sent events notifications are debounced by 400ms to eliminate refresh storms.
4. **Per-Category Rate Limit Buckets** (`backend/app/main.py`):
   - `exempt` (health, docs, SSE stream): 1,200/min.
   - `auth` (login, onboard): 20/min.
   - `stream`: 30/min.
   - `simulate`: 60/min (scoped per tenant:user).
   - `write`: 120/min.
   - `read`: 300/min.
5. **Simulation Locks**: Returns 429 with `Retry-After: 1` if a concurrent simulation is already processing.

---

## 6. Verification & Test Suite

Run the automated test suite:
```bash
python -m pytest backend/tests/ -v
```

Covered test suites:
- `test_rate_limiting.py`: Exempt routes, 429 `Retry-After` headers, simulation debounce, tenant isolation.
- `test_cert_pipeline.py`: Dataset loading, Kafka schema serialization, Spark causal windowing, end-to-end replay, pipeline API endpoints.
- `test_ml_pipeline.py`: CERT schema validation, zero-leakage causal pipeline, synthetic training isolation, model serialization, calibrated evaluation, trained adapter direct inference.
- `test_migrations.py`: Versioned migrations and immutable forensic evidence capsules.
- `test_system.py`: Multi-tenant isolation, RBAC permissions, containment gates, audit integrity.

---

## 7. Troubleshooting

| Symptom | Cause | Solution |
|---|---|---|
| `Rate limit exceeded` (429) | Exceeded burst quota | The client automatically backs off using the `Retry-After` header. If testing programmatically, respect the 1-second backoff. |
| `Authentication required` (401) | Missing or expired JWT session | Sign in via `/api/v1/auth/login` using `org_admin@astra.demo` and the `DEMO_PASSWORD` from `.env`. Ensure header `X-Astra-Request: 1` is included. |
| `Kafka status: unavailable` | Kafka broker or required topics are unavailable | Start the combined Compose stack. In default local mode, replay still scores locally; Kafka scoring mode rejects publication failures. |
| `Spark feature query: not ready` | Spark master/job is not running or its checkpoint cannot be written | Inspect `spark-processor` logs and the `spark_checkpoints` volume. Start replay only after the pipeline readiness checks pass. |
