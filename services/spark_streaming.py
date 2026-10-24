"""Spark Structured Streaming bridge from validated events to model scoring."""

import logging
import os
from functools import reduce
from urllib.parse import urlparse

from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    abs as spark_abs,
    col,
    coalesce,
    concat,
    from_json,
    isnan,
    lit,
    struct,
    to_json,
    to_timestamp,
    when,
)
from pyspark.sql.types import (
    BooleanType,
    DoubleType,
    LongType,
    StringType,
    StructField,
    StructType,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("astra.spark_streaming")

FEATURE_SCHEMA = StructType([
    StructField("egress_mb", DoubleType(), True),
    StructField("sensitive_files", DoubleType(), True),
    StructField("off_hours", DoubleType(), True),
    StructField("new_device", DoubleType(), True),
    StructField("peer_deviation", DoubleType(), True),
    StructField("baseline_egress", DoubleType(), True),
    StructField("behavior_drift", DoubleType(), True),
    StructField("session_count", DoubleType(), True),
])

EVENT_SCHEMA = StructType([
    StructField("schemaVersion", StringType(), True),
    StructField("tenantId", StringType(), True),
    StructField("eventId", StringType(), True),
    StructField("entityId", StringType(), True),
    StructField("timestamp", StringType(), True),
    StructField("eventType", StringType(), True),
    StructField("action", StringType(), True),
    StructField("resource", StringType(), True),
    StructField("device", StringType(), True),
    StructField("bytes", LongType(), True),
    StructField("bytesKnown", BooleanType(), True),
    StructField("provenance", StringType(), True),
    StructField("isSynthetic", BooleanType(), True),
    StructField("features", FEATURE_SCHEMA, True),
])

FEATURE_NAMES = [
    "egress_mb",
    "sensitive_files",
    "off_hours",
    "new_device",
    "peer_deviation",
    "baseline_egress",
    "behavior_drift",
    "session_count",
]


def run_spark_job() -> None:
    bootstrap_servers = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
    input_topic = os.environ.get("KAFKA_INPUT_TOPIC", "cert-events")
    output_topic = os.environ.get("KAFKA_FEATURE_TOPIC", "cert-features")
    dlq_topic = os.environ.get("KAFKA_DLQ_TOPIC", "cert-events-dlq")
    checkpoint = os.environ.get(
        "SPARK_CHECKPOINT_LOCATION",
        "/var/lib/astra/spark-checkpoints/cert-feature-bridge",
    )
    master = os.environ.get("SPARK_MASTER", "spark://spark-master:7077")
    starting_offsets = os.environ.get("KAFKA_STARTING_OFFSETS", "latest")
    max_offsets = os.environ.get("KAFKA_MAX_OFFSETS_PER_TRIGGER", "500")

    if starting_offsets not in {"earliest", "latest"}:
        raise ValueError("KAFKA_STARTING_OFFSETS must be 'earliest' or 'latest'")
    if not max_offsets.isdigit() or int(max_offsets) < 1:
        raise ValueError("KAFKA_MAX_OFFSETS_PER_TRIGGER must be a positive integer")
    if urlparse(master).scheme != "spark":
        raise ValueError("SPARK_MASTER must be a spark:// master URL")

    spark = (
        SparkSession.builder
        .appName("Astra-CERT-Kafka-Feature-Bridge")
        .master(master)
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel(os.environ.get("SPARK_LOG_LEVEL", "WARN"))
    spark.conf.set("spark.sql.session.timeZone", "UTC")

    raw = (
        spark.readStream
        .format("kafka")
        .option("kafka.bootstrap.servers", bootstrap_servers)
        .option("subscribe", input_topic)
        .option("startingOffsets", starting_offsets)
        .option("maxOffsetsPerTrigger", max_offsets)
        .option("failOnDataLoss", "true")
        .load()
        .select(col("key").alias("source_key"), col("value").cast("string").alias("raw"))
    )
    parsed = raw.select(
        "source_key",
        "raw",
        from_json(col("raw"), EVENT_SCHEMA, {"mode": "PERMISSIVE"}).alias("event"),
    )

    def write_batch(batch_df, batch_id: int) -> None:
        event = col("event")
        feature_values = [event["features"][name] for name in FEATURE_NAMES]
        valid_features = reduce(
            lambda left, right: left & right,
            [
                value.isNotNull()
                & (~isnan(value))
                & (spark_abs(value) < lit(float("inf")))
                & (value >= lit(0.0))
                for value in feature_values
            ],
        ) & (event["features"]["off_hours"] <= lit(1.0)) \
          & (event["features"]["new_device"] <= lit(1.0))
        valid = (
            event.isNotNull()
            & (event["schemaVersion"] == "1.0")
            & event["tenantId"].isNotNull()
            & (event["tenantId"] != "")
            & event["eventId"].isNotNull()
            & (event["eventId"] != "")
            & event["entityId"].isNotNull()
            & (event["entityId"] != "")
            & event["timestamp"].isNotNull()
            & event["timestamp"].rlike(r".*(Z|[+-][0-9]{2}:[0-9]{2})$")
            & to_timestamp(event["timestamp"]).isNotNull()
            & event["eventType"].isNotNull()
            & (event["eventType"] != "")
            & event["action"].isNotNull()
            & (event["action"] != "")
            & event["device"].isNotNull()
            & (event["device"] != "")
            & event["resource"].isNotNull()
            & event["bytes"].isNotNull()
            & (event["bytes"] >= lit(0))
            & event["bytesKnown"].isNotNull()
            & event["provenance"].isNotNull()
            & event["isSynthetic"].isNotNull()
            & valid_features
        )
        valid_row = coalesce(valid, lit(False))
        accepted = batch_df.where(valid_row).select(
            concat(event["tenantId"], lit(":"), event["entityId"])
            .cast("binary")
            .alias("key"),
            to_json(event).cast("binary").alias("value"),
        )
        rejected = batch_df.where(~valid_row).select(
            when(event["tenantId"].isNotNull() & event["entityId"].isNotNull(),
                 concat(event["tenantId"], lit(":"), event["entityId"]))
            .otherwise(col("source_key").cast("string"))
            .cast("binary")
            .alias("key"),
            to_json(
                struct(
                    col("raw").alias("rawValue"),
                    lit("Malformed event envelope or invalid feature vector").alias("reason"),
                    lit(batch_id).alias("sparkBatchId"),
                )
            ).cast("binary").alias("value"),
        )
        kafka_options = {
            "kafka.bootstrap.servers": bootstrap_servers,
            "kafka.acks": "all",
            "kafka.enable.idempotence": "true",
            "kafka.max.in.flight.requests.per.connection": "1",
        }
        (
            accepted.write
            .format("kafka")
            .options(**kafka_options)
            .option("topic", output_topic)
            .save()
        )
        (
            rejected.write
            .format("kafka")
            .options(**kafka_options)
            .option("topic", dlq_topic)
            .save()
        )
        logger.info("Finished Spark micro-batch %s", batch_id)

    query = (
        parsed.writeStream
        .foreachBatch(write_batch)
        .option("checkpointLocation", checkpoint)
        .start()
    )
    logger.info(
        "Spark streaming query started: %s -> %s (checkpoint=%s)",
        input_topic,
        output_topic,
        checkpoint,
    )
    try:
        query.awaitTermination()
    finally:
        query.stop()
        spark.stop()


if __name__ == "__main__":
    run_spark_job()
