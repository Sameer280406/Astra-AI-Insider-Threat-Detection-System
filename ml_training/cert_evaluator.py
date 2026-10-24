"""Bounded-memory, provenance-aware CERT replay evaluation."""

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir / "backend"))

import app.services.cert_replay_engine as replay_module
from app.services.cert_replay_engine import CertReplayEngine
from app.services.kafka_service import kafka_pipeline
from app.services.spark_service import AstraSparkAggregator

logger = logging.getLogger("astra.evaluator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


async def run_cert_pipeline_evaluation(
    max_events: int | None = None,
    batch_size: int = 2_000,
) -> dict:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if max_events is not None and max_events < 1:
        raise ValueError("max_events must be positive when specified")

    aggregator = AstraSparkAggregator()
    replay_module.spark_aggregator = aggregator
    cert_replay = CertReplayEngine()
    evaluation_tenant = f"cert-eval-{uuid4().hex}"
    cert_replay.load_dataset()
    started_at = time.perf_counter()
    evaluated = 0
    unique_users: set[str] = set()

    while max_events is None or evaluated < max_events:
        requested = batch_size if max_events is None else min(batch_size, max_events - evaluated)
        report = await cert_replay.run_batch(n_events=requested, tenant_id=evaluation_tenant)
        evaluated += report["events_processed"]
        unique_users.update(
            item["user"] for item in report.get("sample_predictions", [])
        )
        logger.info(
            "CERT replay progress: %d events; %d predictions; %d alerts",
            evaluated,
            cert_replay.state["predictions_generated"],
            cert_replay.state["alerts_generated"],
        )
        if report["status"] == "completed" or report["events_processed"] == 0:
            break

    elapsed = time.perf_counter() - started_at
    ground_truth_events = cert_replay.state["ground_truth_events"]
    true_positives = cert_replay.state["true_positive_alerts"]
    false_positives = cert_replay.state["false_positive_alerts"]
    precision = true_positives / max(true_positives + false_positives, 1)
    event_recall = true_positives / max(ground_truth_events, 1)
    total_scenarios = len(cert_replay._insiders)
    scenario_recall = len(cert_replay._detected_scenarios) / max(total_scenarios, 1)

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "source": cert_replay.state["data_source"],
            "provenance": cert_replay.state["provenance"],
            "evaluation_tenant": evaluation_tenant,
            "total_events": cert_replay.state["total_events_loaded"],
            "events_evaluated_in_run": evaluated,
            "unique_users_in_sample_predictions": len(unique_users),
            "complete_dataset_processed": cert_replay.state["total_events_loaded"] is not None,
        },
        "streaming_infrastructure": {
            "kafka": kafka_pipeline.probe_connection(),
            "spark": aggregator.probe_spark(),
        },
        "ml_inference": {
            "predictions_generated": cert_replay.state["predictions_generated"],
            "alerts_generated": cert_replay.state["alerts_generated"],
        },
        "ground_truth_alignment": {
            "ground_truth_scenarios_count": total_scenarios,
            "detected_scenarios": sorted(cert_replay._detected_scenarios),
            "scenario_recall": scenario_recall,
            "event_precision": precision,
            "event_recall": event_recall,
            "ground_truth_events": ground_truth_events,
            "true_positive_alerts": true_positives,
            "false_positive_alerts": false_positives,
        },
        "performance": {
            "elapsed_seconds": round(elapsed, 3),
            "throughput_events_per_sec": round(evaluated / max(elapsed, 0.001), 2),
            "average_latency_ms": round((elapsed / max(evaluated, 1)) * 1000.0, 2),
        },
    }

    out_path = root_dir / "ml_training" / "cert_pipeline_report.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    logger.info("Evaluation report written to %s", out_path)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-events", type=int, help="Stop after this many events; omit to process the full source")
    parser.add_argument("--batch-size", type=int, default=2_000)
    arguments = parser.parse_args()
    asyncio.run(
        run_cert_pipeline_evaluation(
            max_events=arguments.max_events,
            batch_size=arguments.batch_size,
        )
    )
