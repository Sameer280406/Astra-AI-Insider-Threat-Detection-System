"""
API Endpoints for CERT Replay, Kafka Ingestion, Spark Processing, and Pipeline Health.
"""

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from typing import Optional
from hmac import compare_digest
from ..core.security import require
from ..core.db import get_db, set_tenant
from ..domain.entities import Organization
from ..domain.repository import TenantRepository
from ..api.schemas import EventInput, StreamEnvelope
from ..services.cert_replay_engine import cert_replay
from ..services.kafka_service import kafka_pipeline
from ..services.spark_service import spark_aggregator
from ..models.gateway import registry
from ..core.config import settings

router = APIRouter(prefix="/api/v1", tags=["pipeline"])

class KafkaScoringRequest(BaseModel):
    event: StreamEnvelope

class ReplayStartRequest(BaseModel):
    speed_multiplier: float = Field(default=10.0, ge=0.1, le=1000.0)
    batch_size: int = Field(default=100, ge=1, le=2000)

class BatchReplayRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=2000)
    batch_size: Optional[int] = Field(default=None)

@router.get("/pipeline/status")
def pipeline_status(user=Depends(require("read"))):
    """Reports each component's observed health without overstating degraded infrastructure."""
    kafka_status = kafka_pipeline.probe_connection()
    spark_status = spark_aggregator.probe_spark()
    model_adapters = []
    for model_name in settings().models:
        adapter = registry.resolve(model_name)
        models = getattr(adapter, "_models", None)
        thresholds = models.get("thresholds") if isinstance(models, dict) else None
        model_adapters.append({
            "name": model_name,
            "adapter": adapter.__class__.__name__,
            "version": thresholds.get(f"{model_name}_version", "unknown")
            if isinstance(thresholds, dict) else "unknown",
        })
    readiness_checks = {
        "kafka_broker_and_topics": bool(kafka_status.get("online")),
        "spark_master": bool(spark_status.get("online")),
        "spark_processor_application": bool(spark_status.get("processor_application_active")),
        "kafka_scoring_consumer": bool(kafka_status.get("consumer_group_active")),
    }
    infrastructure_ready = all(readiness_checks.values())
    return {
        "status": "operational" if infrastructure_ready else "degraded",
        "streaming_pipeline": {
            "mode": "kafka_spark" if settings().kafka_scoring_enabled else "local",
            "ready": infrastructure_ready,
            "readiness_checks": readiness_checks,
        },
        "kafka": kafka_status,
        "spark": spark_status,
        "models": {
            "mode": settings().model_mode,
            "configured_models": settings().models,
            "adapters": model_adapters,
        },
        "replay": cert_replay.get_status()
    }

@router.get("/replay/status")
def replay_status(user=Depends(require("read"))):
    """Returns current CERT replay execution status, simulation clock, and processing metrics."""
    return cert_replay.get_status()

@router.post("/replay/batch")
async def replay_batch(body: BatchReplayRequest, user=Depends(require("ingest"))):
    """Scores a bounded local CERT batch with optional Kafka publication."""
    if cert_replay._task and not cert_replay._task.done():
        raise HTTPException(409, "Stop continuous replay before submitting a manual batch")
    try:
        n = body.batch_size if body.batch_size is not None else body.limit
        report = await cert_replay.run_batch(n_events=n, tenant_id=user.tenant_id)
        return report
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"Batch replay failed: {str(e)}")

@router.post("/replay/start")
async def replay_start(body: ReplayStartRequest, user=Depends(require("ingest"))):
    try:
        await cert_replay.start(user.tenant_id, body.speed_multiplier, body.batch_size)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "status": cert_replay.state["status"],
        "speed": body.speed_multiplier,
        "batch_size": body.batch_size,
        "total_events": cert_replay.state["total_events_loaded"],
        "total_events_known": cert_replay.state["total_events_loaded"] is not None,
        "provenance": cert_replay.state["provenance"],
    }

@router.post("/replay/pause")
async def replay_pause(user=Depends(require("ingest"))):
    try:
        await cert_replay.pause(user.tenant_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "paused"}

@router.post("/replay/resume")
async def replay_resume(user=Depends(require("ingest"))):
    try:
        await cert_replay.resume(user.tenant_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "running"}

@router.post("/replay/stop")
async def replay_stop(user=Depends(require("ingest"))):
    try:
        await cert_replay.stop(user.tenant_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "stopped"}

@router.post("/internal/stream/score")
async def score_stream_event(
    body: KafkaScoringRequest,
    authorization: str = Header(default=""),
    db=Depends(get_db),
):
    """Private service endpoint used only by the Kafka scoring worker."""
    token = settings().internal_ingest_token
    if not token:
        raise HTTPException(503, "Kafka internal scoring is not configured")
    if not authorization.startswith("Bearer ") or not compare_digest(authorization[7:], token):
        raise HTTPException(401, "Invalid service credentials")

    envelope = body.event
    set_tenant(db, envelope.tenantId)
    organization = db.get(Organization, envelope.tenantId)
    if not organization:
        raise HTTPException(404, "Kafka event tenant does not exist")
    repo = TenantRepository(db, organization.id)
    entity = next((row for row in repo.list("entities", 10000)
                   if row.data.get("name") == envelope.entityId), None)
    if entity is None:
        department = "Engineering" if sum(map(ord, envelope.entityId)) % 2 == 0 else "Finance"
        entity = repo.add("entities", {
            "name": envelope.entityId,
            "department": department,
            "riskScore": 10.0,
            "riskVelocity": 0.0,
            "lastSeen": envelope.timestamp.isoformat(),
        })

    category = {
        "logon": "authentication",
        "device": "usb",
        "file": "file",
        "http": "network",
        "email": "network",
    }.get(envelope.eventType.lower(), "process")
    event = EventInput(
        eventId=envelope.eventId,
        entityId=entity.id,
        timestamp=envelope.timestamp,
        action=envelope.action[:100] or "Activity",
        resource=envelope.resource[:200] or envelope.eventType,
        device=envelope.device,
        egressMb=envelope.features.egress_mb,
        sensitiveFiles=int(envelope.features.sensitive_files),
        newDevice=bool(envelope.features.new_device),
        category=category,
        details={
            "provenance": envelope.provenance,
            "bytesKnown": envelope.bytesKnown,
            "isSynthetic": envelope.isSynthetic,
        },
    )
    from types import SimpleNamespace
    from ..services.pipeline import ingest
    actor = SimpleNamespace(id="kafka-scoring-worker", tenant_id=organization.id)
    return await ingest(
        db,
        actor,
        event,
        features_override=envelope.features.model_dump(),
    )
