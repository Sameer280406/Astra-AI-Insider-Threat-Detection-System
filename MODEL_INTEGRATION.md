# Model integration contract v1.0

No training is implemented. Your team supplies inference services. The UI never calls them.

## Switch to your model services

Configure backend environment:

```dotenv
MODEL_MODE=remote
MODEL_API_KEY=your-server-side-service-token
MODEL_TIMEOUT_SECONDS=10
MODEL_ENDPOINTS={"IsolationForest":"https://ml.example/predict/IsolationForest","Autoencoder":"https://ml.example/predict/Autoencoder","LSTM":"https://ml.example/predict/LSTM","GNN":"https://ml.example/predict/GNN","Ensemble":"https://ml.example/predict/Ensemble"}
```

Then `docker compose up -d --force-recreate backend`. No frontend rebuild or UI business-logic changes are required. HTTPS is required for production endpoints. Never put model credentials into `NEXT_PUBLIC_*` variables.

Each adapter receives a POST with JSON, `Authorization: Bearer <MODEL_API_KEY>`, `X-Tenant-ID`, and an idempotency key `<eventId>:<modelName>`. Services must isolate tenant information and accept repeated requests safely. The backend does not follow redirects. Timeout, invalid schema, mismatched entity/model, or timestamp drift over five minutes returns 502 to the producer and rolls back scoring. There is no mock fallback in remote mode.

## Request

```json
{
  "schemaVersion":"1.0",
  "tenantId":"organization-uuid",
  "entityId":"employee-uuid",
  "timestamp":"2026-09-30T12:00:00Z",
  "eventId":"source-event-uuid",
  "features":{"egress_mb":2100,"sensitive_files":90,"off_hours":0,"new_device":1,"peer_deviation":3.2,"baseline_egress":120,"behavior_drift":1980,"session_count":14},
  "sequence":[],
  "graph":{"nodes":["employee-uuid","EP-1042","restricted-share"],"edges":[["employee-uuid","EP-1042"],["EP-1042","restricted-share"]]},
  "predictions":[]
}
```

`sequence` contains up to 100 recent entity events in chronological order. `graph` currently contains the event's observed user-device-resource links; a production GNN service may enrich this with a persistent graph store. `predictions` is empty for the four detectors and contains their validated results when invoking Ensemble. The model team owns scaling, categorical encodings, sequence padding, graph enrichment and trained preprocessing. Version these with the model; do not silently reinterpret raw feature units.

## Required response

```json
{
  "modelName":"IsolationForest",
  "modelVersion":"if-2026-09-v1",
  "entityId":"employee-uuid",
  "timestamp":"2026-09-30T12:00:00Z",
  "anomalyScore":0.91,
  "riskScore":91,
  "confidence":0.88,
  "classification":"high_risk",
  "threatType":"Data exfiltration",
  "featureContributions":[{"feature":"egress_mb","value":2100,"contribution":0.36,"direction":"increases"}],
  "evidence":[{"eventId":"source-event-uuid","description":"Outbound transfer exceeded behavioral baseline"}],
  "metadata":{"schemaVersion":"1.0","synthetic":false}
}
```

Strict fields: anomaly/confidence/contribution in [0,1], risk in [0,100], classification `normal|suspicious|high_risk`, direction `increases|decreases`, timestamp timezone aware. Contribution is a normalized explanatory magnitude, not automatically a SHAP value or causal attribution. Match `modelName` exactly. Emit calibrated, consistent anomaly/risk scores. Missing or extra top-level response keys fail validation.

## Remote contract starter

From backend, with development environment/secret configured:

```bash
uvicorn app.models.starter_service:app --host 127.0.0.1 --port 8100
```

Point each MODEL_ENDPOINTS value to `http://127.0.0.1:8100/predict/<modelName>` when FastAPI runs locally (not inside Docker). This fixture runs the deterministic adapters over real HTTP. Replace its inference internals with loaded model weights, validated preprocessing, and tenant-safe inference. Do not expose the unauthenticated fixture publicly.

## Verification checklist for your ML team

1. Implement all five endpoints and return the required schema.
2. Preserve feature units and event timestamp; document model/preprocessing versions.
3. Verify normal, high-risk, malformed, missing-feature, timeout, and batch-retry scenarios.
4. Confirm both four individual predictions and the fused prediction persist and appear on the entity screen.
5. Calibrate confidence and critical thresholds on held-out data. Mock thresholds are product fixtures, not validated threat-detection performance.
6. Test automatic-containment policy gates using simulated adapters before enabling any real provider.

Adding a new model mode can use `AdapterRegistry.register`. Model gateway code has no dependency on endpoint or credential response adapters. Keep this boundary intact.
