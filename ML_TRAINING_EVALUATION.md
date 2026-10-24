# Astra Model Evaluation: Evidence and Limits

## Summary

There is **no completed, verified full CERT r4.2 training/evaluation run** in
the current project state. Do not present the saved scores as measured
production performance on the downloaded CERT corpus.

## Saved experiment results

`ml_training/experiment_results.json` is an earlier comparison report. Its
metadata explicitly records `is_real_cert_logs: false`; the reported test
partition contains 2,600 rows, including 267 positive rows. These results are
from the project's CERT-aligned non-real fixture, not a fresh evaluation on the
downloaded `data/cert_reference/r4.2` source.

The file compares Isolation Forest, an autoencoder, unweighted and weighted
XGBoost, balanced logistic regression, several augmented XGBoost models, and a
calibrated ensemble. Consult the JSON for the exact saved metrics. They are
historical experiment output, not current deployment metrics.

## Historical CERT replay report

`ml_training/cert_pipeline_report.json` is also a bounded historical run, not a
full-corpus model benchmark. It records 300 events evaluated and three labelled
scenarios. Its own infrastructure diagnostics report Kafka offline and the
Apache Spark runtime unavailable; consequently, the recorded replay used the
local Python path, not a live Kafka/Spark pipeline. Its `PASS` verdict applies
only to that report's configured acceptance check.

## Fresh full-data attempt

A fresh controlled experiment was started against the resolver-selected
`data/cert_reference/r4.2` source on 2026-10-23. It passed schema validation but
was still scanning and extracting features when the run was interrupted. It
did not complete training or write a fresh result report. No new model scores
were produced by that attempt.

`ml_training/experiments.py` streams the source and retains a bounded reservoir
of up to 500,000 engineered rows. The source contains approximately 32.7
million events, so the per-event Python aggregation and full source scan can
take a long time even though memory use is bounded. The full chronology audit
previously passed; that validates source ordering, not model quality.

## Model artifacts and training provenance

Files currently present under `backend/app/models/trained_models/` are not
proof that the models were trained on the downloaded CERT r4.2 data. The
separate `ml_training/train_models.py` script generates synthetic,
CERT-aligned data and writes its output under
`ml_training/artifacts/synthetic_bootstrap/`. Those artifacts must not be
described as CERT-trained.

## Metrics and interpretation

The evaluation code can calculate precision, recall, F1, PR-AUC, ROC-AUC,
confusion counts, false alerts per user-day, and incident recall. Those
definitions do not validate the input data or guarantee that an experiment
finished correctly. For any result, report its dataset provenance, positive
prevalence, split method, sample sizes, threshold-selection data, feature path,
and infrastructure mode.

Feature parity between direct API ingestion and CERT replay has not been
established. A replay metric therefore must not be represented as online API
performance. Rule matches can independently trigger alerts and appear in XAI,
but they do not change model scores or model feature attributions.

## Current streaming evaluation status

An optional feature-bearing Kafka -> Spark Structured Streaming validation and
forwarding -> Kafka scoring worker path is now implemented in code. The supplied
Compose stack is a single-broker, plaintext reference topology. It has not been
launched or load-tested in this environment and is not a secure, highly
available production deployment. No model evaluation through a live Kafka/Spark
deployment is claimed.
