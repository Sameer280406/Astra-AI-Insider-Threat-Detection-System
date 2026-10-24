"""Bounded-memory local CERT replay, feature scoring, policy evaluation, and persistence."""

import time
import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Any
import pandas as pd

from .cert_dataset import iter_cert_events, resolve_dataset, resolve_insider_labels
from .kafka_service import kafka_pipeline
from .spark_service import spark_aggregator
from .policy_rules import alert_severity, evaluate_rules
from ..models.gateway import TrainedAdapter
from ..models.contracts import FeatureRequest
from ..core.db import SessionLocal
from ..core.config import settings
from ..domain.repository import TenantRepository
from ..domain.entities import Record

logger = logging.getLogger("astra.cert_replay")

class CertReplayEngine:
    def __init__(self):
        self.state = {
            "status": "idle",  # idle, running, paused, completed, error
            "speed_multiplier": 10.0,
            "events_processed": 0,
            "events_enqueued": 0,
            "total_events_loaded": 0,
            "sim_clock": None,
            "real_start_time": None,
            "events_per_second": 0.0,
            "lag_seconds": 0.0,
            "alerts_generated": 0,
            "incidents_detected": 0,
            "ground_truth_events": 0,
            "true_positive_alerts": 0,
            "false_positive_alerts": 0,
            "predictions_generated": 0,
            "data_source": "unresolved",
            "provenance": "unknown",
            "user_filter": [],
            "date_range": None
        }
        self._insiders: List[Dict[str, Any]] = []
        self._insider_windows: Dict[str, List[tuple[Any, Any, str | None]]] = {}
        self._detected_scenarios: set[str] = set()
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._stop_event = asyncio.Event()
        self._stop_requested = False
        self._task = None
        self._continuous_batch_size = 100
        self._continuous_tenant_id = None
        self._data_dir = None
        self._data_root = Path(__file__).resolve().parents[3] / "data"
        self._dataset_loaded = False
        self._event_iterator = None
        self._adapter = None
        self._pending_features: Dict[str, Dict[str, float]] = {}

    def _locate_cert_dir(self) -> Path:
        path, provenance = resolve_dataset(self._data_root)
        self.state["data_source"] = path.name
        self.state["provenance"] = provenance
        return path

    def _require_streaming_ready(self) -> None:
        if not settings().kafka_scoring_enabled:
            return
        kafka_status = kafka_pipeline.probe_connection()
        spark_status = spark_aggregator.probe_spark()
        checks = {
            "Kafka broker/topics": kafka_status.get("online"),
            "Spark processor application": spark_status.get("processor_application_active"),
            "Kafka scoring consumer": kafka_status.get("consumer_group_active"),
        }
        missing = [name for name, ready in checks.items() if not ready]
        if missing:
            raise RuntimeError(
                "Kafka scoring mode is enabled but the streaming path is not ready: "
                + ", ".join(missing)
            )

    def load_dataset(self) -> int | None:
        """Initializes a bounded-memory chronological reader over local CERT CSVs."""
        cert_dir = self._locate_cert_dir()
        self._data_dir = cert_dir
        logger.info("Loading CERT dataset from: %s", cert_dir)
        labels_file = resolve_insider_labels(self._data_root, cert_dir)
        if labels_file:
            labels = pd.read_csv(labels_file, dtype=str)
            if "dataset" in labels.columns:
                labels = labels[labels["dataset"].astype(str) == "4.2"]
            self._insiders = labels.to_dict(orient="records")
        else:
            self._insiders = []
        self._insider_windows = {}
        for label in self._insiders:
            if not all(label.get(field) for field in ("user", "start", "end")):
                raise ValueError(f"Invalid CERT insider label in {labels_file}: missing user/start/end")
            start = pd.to_datetime(label["start"], format="mixed")
            end = pd.to_datetime(label["end"], format="mixed")
            if end < start:
                raise ValueError(f"Invalid CERT insider label in {labels_file}: end precedes start")
            scenario = label.get("scenario")
            self._insider_windows.setdefault(label["user"], []).append(
                (start, end, scenario if scenario and scenario.lower() != "nan" else None)
            )

        self._event_iterator = iter_cert_events(cert_dir, self.state["provenance"])
        self._dataset_loaded = True
        self._source_exhausted = False
        self._pending_events: List[Dict[str, Any]] = []
        self.state["total_events_loaded"] = None
        self.state["cursor"] = 0
        logger.info("Initialized streaming CERT reader; loaded %d r4.2 labels", len(self._insiders))
        return None

    async def start(self, tenant_id: str, speed_multiplier: float, batch_size: int = 100) -> None:
        self._require_streaming_ready()
        if self._task and not self._task.done():
            if self._continuous_tenant_id != tenant_id:
                raise RuntimeError("CERT replay is already active for another tenant")
            return
        if self.state["status"] == "completed":
            raise RuntimeError("CERT replay reached end of source; restart the service to replay from the beginning")
        if not self._dataset_loaded:
            self.load_dataset()
        self.state["speed_multiplier"] = speed_multiplier
        self._continuous_batch_size = batch_size
        self._stop_requested = False
        self._stop_event.clear()
        self._pause_event.set()
        self._continuous_tenant_id = tenant_id
        self.state["status"] = "running"
        self._task = asyncio.create_task(self._run_continuous(tenant_id))

    def _check_control_tenant(self, tenant_id: str) -> None:
        if self._task and not self._task.done() and self._continuous_tenant_id != tenant_id:
            raise RuntimeError("CERT replay is active for another tenant")

    async def pause(self, tenant_id: str) -> None:
        self._check_control_tenant(tenant_id)
        if not self._task or self._task.done():
            raise RuntimeError("No active continuous replay")
        self._pause_event.clear()
        self.state["status"] = "paused"

    async def resume(self, tenant_id: str) -> None:
        self._check_control_tenant(tenant_id)
        if not self._task or self._task.done():
            raise RuntimeError("No active continuous replay")
        self._pause_event.set()
        if not self._stop_requested:
            self.state["status"] = "running"

    async def stop(self, tenant_id: str) -> None:
        self._check_control_tenant(tenant_id)
        self._stop_requested = True
        self._pause_event.set()
        self._stop_event.set()
        if self._task and not self._task.done():
            await self._task
        self.state["status"] = "stopped"

    async def _run_continuous(self, tenant_id: str) -> None:
        try:
            while not self._stop_requested:
                await self._pause_event.wait()
                if self._stop_requested:
                    break
                started = time.monotonic()
                report = await self.run_batch(self._continuous_batch_size, tenant_id)
                if report["status"] == "completed":
                    self.state["status"] = "completed"
                    return
                target_seconds = self._continuous_batch_size / (
                    10.0 * self.state["speed_multiplier"]
                )
                delay = max(0.0, target_seconds - (time.monotonic() - started))
                if delay:
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        pass
        except Exception:
            self.state["status"] = "error"
            logger.exception("Continuous CERT replay failed")
            return
        self.state["status"] = "stopped"

    async def run_batch(self, n_events: int = 100, tenant_id: str = "meridian") -> Dict[str, Any]:
        """
        Executes a bounded batch through local feature generation, model scoring,
        policy/XAI evaluation, and database persistence; Kafka publish is auxiliary.
        """
        self._require_streaming_ready()
        if not self._dataset_loaded:
            self.load_dataset()

        if self._adapter is None:
            self._adapter = TrainedAdapter("Ensemble")

        batch = self._pending_events
        while len(batch) < n_events and not self._source_exhausted:
            try:
                batch.append(next(self._event_iterator))
            except StopIteration:
                self._source_exhausted = True
                self.state["total_events_loaded"] = self.state["cursor"] + len(batch)
        if not batch:
            self.state["status"] = "completed"
            return {
                "status": "completed",
                "events_processed": 0,
                "throughput_eps": 0.0,
                "alerts_generated": 0,
                "incidents_detected": self.state["incidents_detected"],
                "sim_clock": self.state["sim_clock"],
                "kafka_status": kafka_pipeline.probe_connection(),
                "spark_status": spark_aggregator.probe_spark(),
                "sample_predictions": []
            }

        batch = batch[:max(1, n_events)]
        results = []
        alerts = []
        batch_ground_truth_events = 0
        batch_true_positive_alerts = 0
        batch_false_positive_alerts = 0
        batch_predictions = 0
        batch_enqueued = 0
        start_time = time.time()

        from ..domain.entities import Organization

        with SessionLocal() as db:
            org = db.query(Organization).filter((Organization.id == tenant_id) | (Organization.slug == tenant_id)).first()
            if org:
                tenant_id = org.id
            else:
                org = Organization(id=tenant_id, name=tenant_id.title(), slug=tenant_id, tier="enterprise")
                db.add(org)
                db.commit()

            repo = TenantRepository(db, tenant_id)
            # Pre-load entities into memory
            existing_entities = {r.data.get("name"): r for r in repo.list("entities", 1000)}
            configured_rules = repo.list("rules")

            for ev in batch:
                u = ev["user"]
                if u not in existing_entities:
                    dept = "Engineering" if sum(map(ord, u)) % 2 == 0 else "Finance"
                    new_ent = repo.add("entities", {
                        "name": u,
                        "department": dept,
                        "riskScore": 10.0,
                        "riskVelocity": 0.0,
                        "lastSeen": ev["timestamp"].isoformat()
                    })
                    existing_entities[u] = new_ent
            db.commit()

            for ev in batch:
                # Feature generation is shared by local replay and the Spark scoring path.
                event_id = str(ev["eventId"])
                features = self._pending_features.get(event_id)
                if features is None:
                    features, q_report = spark_aggregator.process_event(ev)
                    if features is not None:
                        self._pending_features[event_id] = features
                if not features:
                    continue
                kafka_result = kafka_pipeline.publish_event(
                    {**ev, "features": features},
                    tenant_id=tenant_id,
                )
                if settings().kafka_scoring_enabled:
                    if not kafka_result.get("delivered"):
                        raise RuntimeError(
                            "Kafka scoring is enabled but event publication failed: "
                            f"{kafka_result.get('diagnostic') or kafka_result.get('error') or 'unknown error'}"
                        )
                    batch_enqueued += 1
                    results.append({
                        "eventId": ev["eventId"],
                        "user": ev["user"],
                        "timestamp": ev["timestamp"].isoformat(),
                        "features": features,
                        "status": "queued_for_spark_scoring",
                    })
                    continue

                # Local fallback scores and persists synchronously.
                req = FeatureRequest(
                    tenantId=tenant_id,
                    entityId=ev["user"],
                    timestamp=ev["timestamp"],
                    eventId=ev["eventId"],
                    features=features
                )
                pred = await self._adapter.predict(req)
                batch_predictions += 1
                ent_row = existing_entities.get(ev["user"])
                risk_velocity = round(
                    pred.riskScore - (ent_row.data.get("riskScore", 0.0) if ent_row else 0.0),
                    1,
                )
                matches = evaluate_rules(
                    configured_rules,
                    {**features, "riskScore": pred.riskScore, "riskVelocity": risk_velocity},
                )
                from .explainability_service import explainability_service
                xai = explainability_service.include_rule_matches(
                    pred.metadata.get("xai"), matches, features, pred.riskScore, ev["user"]
                )
                pred.metadata = {**pred.metadata, "xai": xai}

                # 4. Check if ground truth insider incident window
                is_incident = False
                matched_scenario = None
                for st, et, scenario in self._insider_windows.get(ev["user"], []):
                    if st <= ev["timestamp"] <= et:
                        is_incident = True
                        matched_scenario = scenario
                        break
                if is_incident:
                    batch_ground_truth_events += 1

                # 5. Persist to DB (idempotent duplicate protection)
                existing_ev = db.query(Record).filter(
                    Record.tenant_id == tenant_id,
                    Record.kind == "events",
                    Record.external_id == ev["eventId"]
                ).first()
                if existing_ev:
                    continue

                ent_id = ent_row.id if ent_row else ev["user"]

                ev_row = repo.add("events", {
                    "eventId": ev["eventId"],
                    "entityId": ent_id,
                    "timestamp": ev["timestamp"].isoformat(),
                    "action": ev["activity"],
                    "resource": ev["filename"] or ev["type"],
                    "device": ev["pc"],
                    "riskScore": pred.riskScore,
                    "provenance": ev["provenance"],
                    "isSynthetic": False,
                    "features": features,
                    "xai": xai,
                    "ruleMatches": matches,
                }, ent_id, ev["eventId"])

                repo.add("predictions", pred.model_dump(mode="json"), ent_id)

                # Update entity score
                if ent_row:
                    repo.update(ent_row, {
                        "riskScore": pred.riskScore,
                        "lastSeen": ev["timestamp"].isoformat(),
                        "classification": pred.classification,
                        "threatType": pred.threatType,
                        "xai": xai
                    })

                # A deterministic policy match may alert independently of model risk.
                if pred.riskScore >= 65.0 or matches:
                    if is_incident:
                        batch_true_positive_alerts += 1
                        if matched_scenario:
                            self._detected_scenarios.add(matched_scenario)
                    else:
                        batch_false_positive_alerts += 1
                    top_feat = "risk_score"
                    if pred.featureContributions:
                        c0 = pred.featureContributions[0]
                        top_feat = getattr(c0, "feature", None) or (c0.get("feature") if isinstance(c0, dict) else str(c0))
                    headline = (xai.get("rule_summary") + " " if matches else "")
                    headline += xai.get("headline") or f"Top feature: {top_feat}"
                    alert_row = repo.add("alerts", {
                        "title": f"CERT Threat: {pred.threatType}",
                        "entityName": ev["user"],
                        "riskScore": pred.riskScore,
                        "severity": alert_severity(pred.riskScore, matches),
                        "status": "open",
                        "eventId": ev_row.id,
                        "explanation": headline,
                        "modelConsensus": int(pred.riskScore >= 65.0),
                        "matchedScenario": matched_scenario,
                        "matchedRules": [rule["name"] for rule in matches],
                        "ruleMatches": matches,
                        "riskVelocity": risk_velocity,
                        "provenance": ev["provenance"],
                        "isSynthetic": False,
                        "xai": xai
                    }, ent_id)
                    alerts.append(alert_row.data)
                    self.state["alerts_generated"] += 1
                    if is_incident:
                        self.state["incidents_detected"] += 1

                results.append({
                    "eventId": ev["eventId"],
                    "user": ev["user"],
                    "timestamp": ev["timestamp"].isoformat(),
                    "features": features,
                    "riskScore": pred.riskScore,
                    "classification": pred.classification,
                    "isIncident": is_incident,
                    "xai": xai
                })

            db.commit()

        for ev in batch:
            self._pending_features.pop(str(ev["eventId"]), None)
        elapsed = time.time() - start_time
        del self._pending_events[:len(batch)]
        self.state["cursor"] += len(batch)
        self.state["events_processed"] += len(batch)
        self.state["events_enqueued"] += batch_enqueued
        self.state["ground_truth_events"] += batch_ground_truth_events
        self.state["true_positive_alerts"] += batch_true_positive_alerts
        self.state["false_positive_alerts"] += batch_false_positive_alerts
        self.state["predictions_generated"] += batch_predictions
        self.state["events_per_second"] = round(len(batch) / max(elapsed, 0.001), 2)
        if batch:
            self.state["sim_clock"] = batch[-1]["timestamp"].isoformat()

        return {
            "status": "completed" if self._source_exhausted and not self._pending_events else "success",
            "events_processed": len(results),
            "events_enqueued": batch_enqueued,
            "processing_mode": "kafka_spark" if settings().kafka_scoring_enabled else "local",
            "throughput_eps": self.state["events_per_second"],
            "alerts_generated": len(alerts),
            "incidents_detected": self.state["incidents_detected"],
            "ground_truth_events": batch_ground_truth_events,
            "true_positive_alerts": batch_true_positive_alerts,
            "false_positive_alerts": batch_false_positive_alerts,
            "predictions_generated": batch_predictions,
            "sim_clock": self.state["sim_clock"],
            "kafka_status": kafka_pipeline.probe_connection(),
            "spark_status": spark_aggregator.probe_spark(),
            "sample_predictions": results[:5]
        }

    def get_status(self) -> Dict[str, Any]:
        return {
            **self.state,
            "spark_metrics": spark_aggregator.get_metrics(),
            "kafka_probe": kafka_pipeline.probe_connection()
        }

cert_replay = CertReplayEngine()
