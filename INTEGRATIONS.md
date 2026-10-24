# Astra Integrations: Verified Status and Limitations

This inventory distinguishes code paths that run locally from configured
external services. Configuration or a health probe is not proof of an
end-to-end integration.

## Implemented local paths

| Component | Current behavior | Important boundary |
|---|---|---|
| CERT replay reader | Bounded-memory chronological merge of local CERT source CSVs; source-qualified event IDs; r4.2 ground-truth label filtering. | Source ordering is checked while reading. The full data set is not preloaded or counted at startup. |
| Replay feature aggregation | Stateful Python feature aggregation in `backend/app/services/spark_service.py`. | The feature calculation remains in Python; the Spark job validates/forwards rather than reimplementing it. |
| Kafka/Spark scoring path | Replay can publish feature-bearing events; Spark validates and forwards them; a Kafka worker calls the tenant-scoped scoring API. | Opt-in via `KAFKA_SCORING_ENABLED`; at-least-once delivery, API idempotency, token-protected internal route, and DLQ handling. The Compose topology is single-broker/plaintext and not production HA/security. |
| Model adapter | Replay invokes `TrainedAdapter("Ensemble")`; normal ingestion uses configured model adapters. | Artifact presence and actual trained-data provenance must be verified separately; no performance/recall claim is implied here. |
| Policy rules and XAI | Both normal ingestion and replay evaluate enabled rules and include matched-rule evidence in alert/event XAI. | Rules do not alter model scores or model feature attributions. |
| Database | Tenant-scoped records, entities, alerts, and predictions are stored by the application repositories. | Deployment database mode and operational backups depend on environment configuration. |
| Demo simulation | `/api/v1/demo/simulate` generates clearly labeled synthetic events. | Synthetic demo events are not CERT observations. |

## Streaming deployment and remaining production work

### Kafka

Use `docker compose -f docker-compose.yml -f docker-compose.streaming.yml up
--build -d` after setting `KAFKA_SCORING_ENABLED=true` and a random
`INTERNAL_INGEST_TOKEN` of at least 32 characters in the environment. The
streaming Compose file creates the topics, starts Spark Structured Streaming,
and launches the Kafka scoring worker. The scorer commits offsets only after
successful API persistence or dead-letter routing.

### Apache Spark

The Spark job validates incoming feature envelopes and forwards them; it does
not independently calculate model features. Readiness distinguishes a live
Spark master from the active processor application; confirm query progress in
Spark logs/metrics as well. `GET /api/v1/pipeline/status`
reports whether Kafka topics, Spark query, and scoring consumer are all ready.
This was not launched or load-tested in this environment.

The supplied topology is a local/reference stack: Kafka has one plaintext
broker and replication factor 1. Before production, configure TLS/SASL, ACLs,
multiple brokers and appropriate replication, external secret storage, backup
and restore for checkpoints, monitoring/alerting, and failover/load testing.

### Identity and containment integrations

OIDC/SSO and real external EDR/identity-provider actions require provider
configuration and adapters. The local containment behavior is a simulation;
do not treat it as production endpoint isolation or account suspension.

## Data and licensing

The data resolver checks `data/r4.2/`, `data/cert_reference/r4.2/`,
`data/cert/`, then `data/cert_reference/`. It uses whichever compatible local
source is found, and reports its provenance. The CERT source license prohibits
redistribution; keep raw data local and do not add its rows to tests,
documentation, or artifacts.

CERT HTTP content does not generally provide measured bytes, and file/device
rows do not provide transfer sizes. Missing byte measurements are not
fabricated; numeric model input uses zero with a corresponding quality counter.

## Health endpoint

`GET /api/v1/pipeline/status` reports Kafka and Spark health plus stream-path
readiness checks. The local Python replay mode remains functional if the
streaming services are absent; Kafka scoring mode requires every readiness
check and reports queued events separately from scored predictions.
