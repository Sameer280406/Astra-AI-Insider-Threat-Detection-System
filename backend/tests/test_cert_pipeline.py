"""
Integration tests for CERT Replay, Kafka Ingestion, Spark Aggregation, Model Scoring, and Alert Generation.
"""

import asyncio
import pytest
from itertools import islice
from types import SimpleNamespace
from app.api.schemas import StreamEnvelope
from app.services.cert_replay_engine import CertReplayEngine, cert_replay
from app.services.cert_dataset import iter_cert_events
from app.services.kafka_service import kafka_pipeline, KafkaMessageSchema
from app.services.spark_service import AstraSparkAggregator, spark_aggregator, FEATURE_NAMES
from app.services.policy_rules import evaluate_rules
from app.core.config import settings
from app.services.kafka_worker import _commit_message

def onboard(c, slug='pipe-org'):
    r = c.post('/api/v1/auth/onboard', json={
        'organization': slug,
        'organizationName': slug.title(),
        'name': 'Pipeline Admin',
        'email': f'{slug}@example.com',
        'password': 'Strong-Password-123!'
    })
    assert r.status_code == 201, r.text
    return r.json()

def test_cert_dataset_loading():
    """Validates streaming initialization and chronological CERT event iteration."""
    n_records = cert_replay.load_dataset()
    assert n_records is None
    assert cert_replay.state["total_events_loaded"] is None
    # Verify chronological ordering
    ts = [r["timestamp"] for r in islice(cert_replay._event_iterator, 50)]
    assert ts
    for i in range(len(ts) - 1):
        assert ts[i] <= ts[i+1], f"Events not chronological at index {i}"

def test_spark_duplicate_cache_is_bounded():
    aggregator = AstraSparkAggregator()
    aggregator._event_id_cache_limit = 2
    for index in range(3):
        aggregator.process_event({
            "eventId": f"evt-bounded-{index}",
            "user": "USR0001",
            "timestamp": f"2026-01-26T22:30:0{index}Z",
            "pc": "EP-1",
        })
    assert len(aggregator.seen_event_ids) == 2
    assert len(aggregator._recent_event_ids) == 2

def test_cert_stream_merge_preserves_unknown_byte_measurements(tmp_path):
    (tmp_path / "logon.csv").write_text(
        "id,date,user,pc,activity\nL1,01/02/2010 08:00:00,U1,P1,Logon\n",
        encoding="utf-8",
    )
    (tmp_path / "device.csv").write_text(
        "id,date,user,pc,activity\nD1,01/02/2010 08:02:00,U1,P1,Connect\n",
        encoding="utf-8",
    )
    (tmp_path / "http.csv").write_text(
        "id,date,user,pc,url,content\nH1,01/02/2010 08:01:00,U1,P1,https://example.invalid,download\n",
        encoding="utf-8",
    )
    events = list(iter_cert_events(tmp_path, "cert_r4.2"))
    assert [event["eventId"] for event in events] == ["LOGON:L1", "HTTP:H1", "DEVICE:D1"]
    assert all(event["bytesKnown"] is False for event in events)
    assert all("bytes" not in event for event in events)

@pytest.mark.anyio
async def test_continuous_replay_lifecycle_and_tenant_control():
    engine = CertReplayEngine()
    engine._dataset_loaded = True
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fake_batch(n_events, tenant_id):
        entered.set()
        await release.wait()
        return {"status": "success"}

    engine.run_batch = fake_batch
    await engine.start("tenant-a", speed_multiplier=1000, batch_size=1)
    await entered.wait()
    with pytest.raises(RuntimeError, match="another tenant"):
        await engine.pause("tenant-b")
    release.set()
    await engine.stop("tenant-a")
    assert engine.state["status"] == "stopped"

def test_kafka_schema_serialization():
    """Validates the Kafka event envelope and metadata."""
    event = {
        "eventId": "evt-kfk-1",
        "user": "USR0003",
        "timestamp": "2026-01-12T19:30:00Z",
        "type": "http",
        "activity": "HTTP Transfer",
        "filename": "http://external-storage.com/archive.zip",
        "pc": "EP-1042",
        "bytes": 25000000,
        "bytesKnown": True,
        "provenance": "cert_r4.2"
    }
    raw = KafkaMessageSchema.serialize_event(event, tenant_id="meridian")
    assert isinstance(raw, bytes)
    deserialized = KafkaMessageSchema.deserialize_event(raw)
    assert deserialized["tenantId"] == "meridian"
    assert deserialized["entityId"] == "USR0003"
    assert deserialized["bytes"] == 25000000
    assert deserialized["bytesKnown"] is True
    assert deserialized["provenance"] == "cert_r4.2"
    naive_event = {**event, "timestamp": "2010-01-12T19:30:00"}
    normalized = KafkaMessageSchema.deserialize_event(
        KafkaMessageSchema.serialize_event(naive_event, tenant_id="meridian")
    )
    assert normalized["timestamp"].endswith("+00:00")

def test_kafka_feature_envelope_contract_and_stable_event_id():
    event = {
        "eventId": "evt-feature-contract",
        "user": "USR0003",
        "timestamp": "2026-01-12T19:30:00Z",
        "type": "http",
        "activity": "HTTP Transfer",
        "filename": "archive.zip",
        "pc": "EP-1042",
        "bytes": 25000000,
        "bytesKnown": True,
        "provenance": "cert_r4.2",
        "features": {
            "egress_mb": 23.84,
            "sensitive_files": 1.0,
            "off_hours": 1.0,
            "new_device": 0.0,
            "peer_deviation": 2.1,
            "baseline_egress": 12.0,
            "behavior_drift": 11.84,
            "session_count": 4.0,
        },
    }
    envelope = KafkaMessageSchema.deserialize_event(
        KafkaMessageSchema.serialize_event(event, tenant_id="tenant-a")
    )
    assert StreamEnvelope.model_validate(envelope).features.egress_mb == 23.84
    with pytest.raises(ValueError, match="stable eventId"):
        KafkaMessageSchema.serialize_event({"timestamp": event["timestamp"]})

def test_policy_rules_do_not_match_absent_features_as_zero():
    rule = SimpleNamespace(
        id="rule-zero-threshold",
        data={
            "name": "Zero threshold",
            "feature": "egress_mb",
            "threshold": 0,
            "enabled": True,
            "severity": "high",
        },
    )
    assert evaluate_rules([rule], {}) == []
    assert evaluate_rules([rule], {"egress_mb": 0})[0]["observedValue"] == 0

def test_kafka_consumer_commits_only_the_processed_offset():
    commits = []
    consumer = SimpleNamespace(commit=lambda offsets: commits.append(offsets))
    message = SimpleNamespace(topic="cert-features", partition=2, offset=8)
    _commit_message(consumer, message)
    assert len(commits) == 1
    committed = next(iter(commits[0].values()))
    assert committed.offset == 9

def test_private_kafka_scoring_route_applies_tenant_rules(client, monkeypatch):
    onboard(client, "kafka-score-org")
    rule = client.post("/api/v1/rules", json={
        "name": "Kafka egress limit",
        "feature": "egress_mb",
        "threshold": 1,
        "severity": "critical",
    })
    assert rule.status_code == 201, rule.text
    token = "kafka-internal-test-secret-at-least-32-chars"
    monkeypatch.setattr(settings(), "internal_ingest_token", token)
    envelope = {
        "schemaVersion": "1.0",
        "tenantId": client.get("/api/v1/auth/me").json()["organization"]["id"],
        "eventId": "kafka-route-event-1",
        "entityId": "USR-KAFKA-01",
        "timestamp": "2026-01-12T19:30:00Z",
        "eventType": "http",
        "action": "HTTP Transfer",
        "resource": "archive.zip",
        "device": "EP-1042",
        "bytes": 0,
        "bytesKnown": False,
        "provenance": "cert_r4.2",
        "isSynthetic": False,
        "features": {
            "egress_mb": 5.0,
            "sensitive_files": 1.0,
            "off_hours": 1.0,
            "new_device": 0.0,
            "peer_deviation": 2.0,
            "baseline_egress": 10.0,
            "behavior_drift": 0.0,
            "session_count": 3.0,
        },
    }
    unauthorized = client.post("/api/v1/internal/stream/score", json={"event": envelope})
    assert unauthorized.status_code == 401
    response = client.post(
        "/api/v1/internal/stream/score",
        headers={"Authorization": f"Bearer {token}"},
        json={"event": envelope},
    )
    assert response.status_code == 200, response.text
    assert response.json()["alert"]["matchedRules"] == ["Kafka egress limit"]
    assert response.json()["prediction"]["metadata"]["xai"]["rule_matches"][0]["threshold"] == 1

def test_spark_causal_windowing_and_deduplication():
    """Validates 8-feature causal derivation and duplicate rejection."""
    ev1 = {
        "eventId": "evt-spark-1",
        "user": "USR0007",
        "timestamp": "2026-01-26T22:30:00Z",
        "type": "logon",
        "pc": "EP-NEW-1",
        "bytes": 0,
        "filename": ""
    }
    feats1, q1 = spark_aggregator.process_event(ev1)
    assert q1["status"] == "ok"
    assert feats1["off_hours"] == 1.0  # 22:30 is off-hours
    assert all(k in feats1 for k in FEATURE_NAMES)

    # Duplicate check
    feats_dup, q_dup = spark_aggregator.process_event(ev1)
    assert q_dup["status"] == "duplicate"
    assert feats_dup is None

@pytest.mark.anyio
async def test_end_to_end_replay_batch_with_db():
    """Tests local CERT replay through feature generation, model scoring, and DB persistence."""
    report = await cert_replay.run_batch(n_events=25, tenant_id="meridian")
    assert report["status"] == "success"
    assert report["events_processed"] >= 20
    assert report["throughput_eps"] > 0
    assert "sim_clock" in report
    assert len(report["sample_predictions"]) > 0

def test_pipeline_api_endpoints(client):
    """Verifies that /api/v1/pipeline/status and /api/v1/replay/status return authentic telemetry."""
    onboard(client, 'status-org')

    pipe_res = client.get("/api/v1/pipeline/status")
    assert pipe_res.status_code == 200
    p_data = pipe_res.json()
    assert "kafka" in p_data
    assert "spark" in p_data
    assert "models" in p_data
    assert "replay" in p_data

    replay_res = client.get("/api/v1/replay/status")
    assert replay_res.status_code == 200
    r_data = replay_res.json()
    assert "status" in r_data
    assert "events_processed" in r_data
