# Astra Data Pipeline: Current Behavior and Operations

## 1. What runs when CERT replay is requested

The normal API ingestion and CERT replay paths are separate:

```text
Normal event API:
request -> FeaturePipeline -> configured model adapters -> policy rules/XAI
        -> tenant database -> alerts/containment

CERT batch replay:
local CERT CSVs -> bounded chronological reader -> in-process Python aggregator
                 -> local model / policy rules/XAI -> tenant database

Optional Kafka/Spark replay:
local CERT CSVs -> Python features -> Kafka cert-events
                 -> Spark validation/forwarding -> Kafka cert-features
                 -> Kafka scoring worker -> common tenant-scoped API pipeline
                 -> model -> policy rules/XAI -> tenant database
```

The replay reader does not put all raw records into memory. It holds a small
number of file cursors plus the requested batch and merges the source streams
by timestamp. Every source CSV must itself be chronologically ordered; an
out-of-order row causes replay to fail explicitly rather than silently emitting
events in the wrong order. The reader namespaces source event IDs (for example,
`HTTP:<id>`) so identifiers from different source files cannot collide.

The local dataset resolver prefers these locations, in order:

1. `data/r4.2/`
2. `data/cert_reference/r4.2/`
3. `data/cert/`
4. `data/cert_reference/`

The first three locations take priority over the reference fallback. The last
is the reference fallback. Insider labels are resolved separately from the source
files, and a `dataset` column is filtered to version `4.2`.

## 2. Source data, provenance, and limits

CERT telemetry is read from `logon.csv`, `device.csv`, `http.csv`, `file.csv`,
and, when present, `email.csv`. No raw CERT rows are copied into generated
documentation or reports.

- Source rows are tagged `isSynthetic: false`, with provenance derived from
  the resolved directory.
- Email size is read from the source `size` field. HTTP bytes are included only
  when an explicit `bytes=` value exists in the source content.
- Logon, device, and file rows do not contain measured transfer amounts. The
  replay path does not invent byte values for these records. Missing values are
  represented as zero only for the model's numeric feature input and counted
  by `events_without_measured_bytes`.
- The resulting `egress_mb` feature therefore is not a complete measurement
  of network/device egress. It must not be interpreted as a ground-truth byte
  total.
- The bundled CERT license restricts redistribution. Keep the downloaded raw
  data local and do not publish it or excerpts from it.

The accompanying CERT reference release is described by its source README as
a dense-needle benchmark. Its scenario density should not be treated as the
real-world prevalence of insider incidents.

## 3. Kafka and Spark: what they actually do

### Kafka

`backend/app/services/kafka_service.py` serializes stable event IDs and
feature-bearing replay messages to `cert-events`, keyed by tenant and entity.
CERT timestamps without an explicit offset are normalized to UTC at this
serialization boundary so that the downstream strict API event contract
receives a timezone-aware timestamp.
By default, a Kafka publish is best-effort while local inference proceeds.
When `KAFKA_SCORING_ENABLED=true`, a publish failure is explicit and the batch
is not silently scored locally.

### Spark and the scoring consumer

`backend/app/services/spark_service.py` remains the Python replay feature
aggregator. The actual Spark Structured Streaming job in
`services/spark_streaming.py` parses the event envelope, validates the complete
feature vector, and forwards valid messages from `cert-events` to
`cert-features`; invalid messages are sent to `cert-events-dlq`. It does not
recompute the eight features, so Spark and the application do not have two
different feature implementations.

`backend/app/services/kafka_worker.py` consumes `cert-features` with automatic
offset commits disabled and invokes the private, token-protected internal API.
The request uses the shared event-scoring pipeline with the supplied validated
features, so configured model adapters, tenant policy rules, XAI, alerting,
and persistence are applied. Offsets are committed per message only after a
successful API response or a confirmed DLQ write. Delivery is at-least-once;
replays/retries are idempotent through the tenant/event unique key. A persistent
Spark checkpoint volume is required to resume offsets across restarts.

The Compose stack is a functional single-broker development/reference topology,
not a secure or highly available production deployment. It currently uses
plaintext Kafka and replication factor 1. Production needs authenticated TLS,
Kafka ACLs, multiple brokers/replicas, secret management, monitored checkpoint
storage, and recovery/load testing.

The in-process features are:

| Feature | Current calculation |
|---|---|
| `egress_mb` | Event bytes divided by 1,048,576; unavailable source byte values are imputed as zero and counted. |
| `sensitive_files` | Binary filename-extension match against the aggregator's sensitive-extension set. |
| `off_hours` | 1 for before 08:00, at/after 18:00, or a weekend. |
| `new_device` | 1 when a user is seen on a device not previously seen for that user. |
| `baseline_egress` | Mean of up to 200 prior event egress values for that user; 15 MB before history exists. |
| `behavior_drift` | Positive difference between current egress and the prior-user baseline. |
| `session_count` | Per-user event count for the current calendar date. |
| `peer_deviation` | Current egress divided by the prior department mean, with a 1 MB lower denominator bound. |

This aggregation is causal with respect to the events it has processed. Spark
does not independently derive these features; it validates and transports the
computed vector.

## 4. Model and rule behavior

Replay calls `TrainedAdapter("Ensemble")` directly. The configured policy rules
are then evaluated against generated features, model `riskScore`, and
`riskVelocity`. A matching rule can create an alert even when the model score
is below its alert threshold. XAI records the rule name, feature, operator,
threshold, and observed value, while explicitly keeping deterministic rule
evidence separate from model attributions and model risk score.

The same rule-evaluation helper is used by normal API event ingestion. Rule
edits apply to events scored after the edit; they do not retroactively rescore
stored events.

**Feature-parity limitation:** the normal API path uses
`backend/app/services/features.py`, while CERT replay and training use the
aggregator above. Their feature definitions are not equivalent today. For
example, API ingestion accepts a numeric `sensitiveFiles` count and a supplied
`newDevice` flag, uses a different off-hours window and peer-standard-deviation
calculation, and derives history features from database records. CERT replay
uses a binary filename match, infers new devices from replay state, uses its
own off-hours/peer/baseline/session windows, and imputes missing measured bytes.
Consequently, sharing policy rules does not make model scores or numeric
threshold behavior equivalent between those paths. CERT replay metrics are
not evidence of online API performance; establish feature parity and validate
it before deploying a CERT-trained model to online scoring.

## 5. Training and evaluation data

`ml_training/experiments.py` resolves the local CERT source through the same
dataset resolver, streams the full ordered source, derives features with the
same Python aggregator, and retains a deterministic uniform reservoir (default
500,000 events) rather than holding the raw source or every derived row in
memory. It labels events against the version-filtered insider windows and
chronologically partitions the retained sample.

Synthetic augmentation is applied to the training partition only. The
augmentation ratio is selected using validation F1, the decision threshold is
calibrated using validation data, and the untouched test partition is used for
final reporting. The experiment aborts when a chronological partition lacks
both positive and negative labels rather than presenting a misleading metric.
The output records scan/sample sizes and source provenance.

`ml_training/train_models.py` is a separate synthetic-bootstrap trainer. Its
outputs now go to `ml_training/artifacts/synthetic_bootstrap/`, not the active
backend model directory. Do not describe those artifacts as trained on the
downloaded CERT records. `ml_training/export_and_integrate.py` runs the
controlled real-data experiment and exports its resulting artifacts when
explicitly invoked.

## 6. Replay API

- `POST /api/v1/replay/batch`: advances one bounded batch and returns sample
  predictions, throughput, and observed Kafka/Spark probe status.
- `POST /api/v1/replay/start`: starts an in-process background replay loop.
  `speed_multiplier` controls the target pacing (10 events/second multiplied by
  the requested value); source event timestamps are retained in stored events.
- `POST /api/v1/replay/pause`, `/resume`, and `/stop`: control that loop.
- `POST /api/v1/replay/batch`: manual batches are rejected while a continuous
  loop is active.
- `GET /api/v1/replay/status`: returns counters, source provenance, and
  aggregator metrics.

The API's `total_events` remains unknown until the iterator reaches end of
source. This avoids preloading multi-gigabyte files merely to count them.

## 7. Evaluation command

Run a bounded check:

```powershell
python ml_training/cert_evaluator.py --max-events 10000 --batch-size 500
```

Omit `--max-events` to process the full source. Full evaluation can take a long
time because it scores every selected event. The JSON report records measured
counts and elapsed time from that invocation; it does not hard-code recall,
false-alert rate, or throughput claims. A bounded prefix is not a substitute
for a full chronological evaluation.

## 8. Infrastructure health interpretation

`GET /api/v1/pipeline/status` reports Kafka broker/topic state, Spark master
state and active processor application, and whether the scoring consumer group
has an active member. This does not replace checking Spark query logs and
throughput. The streaming path is ready only when all checks pass. When
`KAFKA_SCORING_ENABLED` is false, replay remains in local mode regardless of
streaming service availability.
