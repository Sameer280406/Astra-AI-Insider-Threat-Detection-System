"""Kafka feature consumer with at-least-once delivery to Astra's scoring API."""

import json
import logging
import os
import random
import time
from typing import Any

import httpx
from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import KafkaError
from kafka.structs import OffsetAndMetadata, TopicPartition

from .kafka_service import AstraKafkaPipeline, KafkaMessageSchema

logger = logging.getLogger("astra.kafka_worker")


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be configured for the Kafka scoring worker")
    return value


def run_worker() -> None:
    bootstrap_servers = _required_env("KAFKA_BOOTSTRAP_SERVERS")
    api_url = _required_env("INTERNAL_API_URL").rstrip("/")
    service_token = _required_env("INTERNAL_INGEST_TOKEN")
    if len(service_token) < 32:
        raise RuntimeError("INTERNAL_INGEST_TOKEN must contain at least 32 characters")

    pipeline = AstraKafkaPipeline(bootstrap_servers)
    consumer = KafkaConsumer(
        pipeline.topic_features,
        bootstrap_servers=bootstrap_servers,
        group_id=pipeline.consumer_group,
        enable_auto_commit=False,
        auto_offset_reset=os.environ.get("KAFKA_STARTING_OFFSETS", "latest"),
        max_poll_records=int(os.environ.get("KAFKA_MAX_POLL_RECORDS", "1")),
        request_timeout_ms=30000,
        session_timeout_ms=10000,
        heartbeat_interval_ms=3000,
        value_deserializer=None,
        key_deserializer=None,
        client_id="astra-feature-scorer",
    )
    producer = KafkaProducer(
        bootstrap_servers=bootstrap_servers,
        acks="all",
        retries=5,
        max_in_flight_requests_per_connection=1,
        request_timeout_ms=5000,
        client_id="astra-scoring-dlq",
    )
    headers = {
        "Authorization": f"Bearer {service_token}",
        "X-Astra-Request": "1",
        "Content-Type": "application/json",
    }

    logger.info("Kafka scoring worker subscribed to %s", pipeline.topic_features)
    try:
        with httpx.Client(timeout=15.0, trust_env=False) as client:
            for message in consumer:
                try:
                    envelope: dict[str, Any] = KafkaMessageSchema.deserialize_event(message.value)
                    response = None
                    for attempt in range(5):
                        response = client.post(
                            f"{api_url}/api/v1/internal/stream/score",
                            json={"event": envelope},
                            headers=headers,
                        )
                        if response.is_success:
                            break
                        if 400 <= response.status_code < 500 and response.status_code != 429:
                            raise ValueError(
                                f"Scoring API rejected event with HTTP {response.status_code}"
                            )
                        delay = min(30.0, (2**attempt) + random.random())
                        logger.warning(
                            "Scoring API unavailable (HTTP %s); retrying in %.1fs",
                            response.status_code,
                            delay,
                        )
                        time.sleep(delay)
                    if response is None or not response.is_success:
                        raise RuntimeError("Scoring API retry budget exhausted")
                    _commit_message(consumer, message)
                except (ValueError, json.JSONDecodeError) as exc:
                    _send_to_dlq(producer, pipeline, message, str(exc))
                    _commit_message(consumer, message)
                except httpx.HTTPError as exc:
                    logger.exception(
                        "Scoring API transport failed for Kafka offset %s; leaving offset uncommitted",
                        message.offset,
                    )
                    raise RuntimeError("Scoring API transport failed; worker will restart") from exc
                except RuntimeError:
                    logger.exception(
                        "Kafka event processing failed at offset %s; leaving offset uncommitted",
                        message.offset,
                    )
                    raise
    except KafkaError:
        logger.exception("Kafka consumer failed")
        raise
    finally:
        consumer.close()
        producer.flush(timeout=10)
        producer.close(timeout=10)


def _send_to_dlq(producer, pipeline, message, reason: str) -> None:
    dead_letter = {
        "sourceTopic": message.topic,
        "sourcePartition": message.partition,
        "sourceOffset": message.offset,
        "error": reason,
        "rawValue": message.value.decode("utf-8", errors="replace"),
    }
    producer.send(
        pipeline.topic_dlq,
        key=message.key,
        value=json.dumps(dead_letter, separators=(",", ":")).encode("utf-8"),
    ).get(timeout=10)
    logger.error(
        "Kafka message rejected and routed to DLQ (partition=%s offset=%s)",
        message.partition,
        message.offset,
    )


def _commit_message(consumer, message) -> None:
    consumer.commit({
        TopicPartition(message.topic, message.partition):
            OffsetAndMetadata(message.offset + 1, "", -1)
    })


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_worker()
