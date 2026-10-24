"""
CERT feature extraction with the same causal aggregator used by replay.

The source is streamed completely; only a deterministic bounded reservoir is
retained for model fitting and chronological holdout evaluation.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import random
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from sklearn.preprocessing import RobustScaler

from app.services.cert_dataset import iter_cert_events, resolve_insider_labels
from app.services.spark_service import AstraSparkAggregator


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


@dataclass
class DatasetSplits:
    X_train: np.ndarray
    y_train: np.ndarray
    train_metadata: List[Dict]
    X_val: np.ndarray
    y_val: np.ndarray
    val_metadata: List[Dict]
    X_test: np.ndarray
    y_test: np.ndarray
    test_metadata: List[Dict]
    scaler: RobustScaler
    split_dates: Tuple[datetime, datetime]


def extract_features_from_cert(cert_dir: Path, sample_size: int = 500_000, seed: int = 42) -> pd.DataFrame:
    """Stream the ordered CERT source and retain a deterministic uniform sample."""
    if sample_size < 1:
        raise ValueError("sample_size must be positive")

    data_root = cert_dir.parent.parent if cert_dir.parent.name == "cert_reference" else cert_dir.parent
    labels_file = resolve_insider_labels(data_root, cert_dir)
    incident_windows: Dict[str, List[Tuple[pd.Timestamp, pd.Timestamp]]] = {}
    if labels_file:
        labels = pd.read_csv(labels_file, dtype=str)
        if "dataset" in labels.columns:
            labels = labels[labels["dataset"].astype(str) == "4.2"]
        for label in labels.to_dict(orient="records"):
            if all(label.get(field) for field in ("user", "start", "end")):
                incident_windows.setdefault(label["user"], []).append((
                    pd.to_datetime(label["start"], format="mixed"),
                    pd.to_datetime(label["end"], format="mixed"),
                ))

    if (cert_dir / "_synthetic_manifest.json").is_file():
        provenance = "synthetic_cert_fixture"
    elif cert_dir.name == "r4.2":
        provenance = "cert_r4.2"
    elif cert_dir.name == "cert_reference":
        provenance = "cert_reference"
    else:
        provenance = "cert_local"

    aggregator = AstraSparkAggregator()
    rng = random.Random(seed)
    sample: List[Dict] = []
    source_events_scanned = 0
    events_without_measured_bytes = 0
    for event in iter_cert_events(cert_dir, provenance):
        if not event.get("bytesKnown", "bytes" in event):
            events_without_measured_bytes += 1
        features, _ = aggregator.process_event(event)
        if features is None:
            continue
        event_time = pd.Timestamp(event["timestamp"])
        is_anomaly = any(
            start <= event_time <= end
            for start, end in incident_windows.get(event["user"], ())
        )
        row = {
            "eventId": event["eventId"],
            "timestamp": event_time,
            "entityId": event["user"],
            **features,
            "y": int(is_anomaly),
            "provenance": provenance,
            "is_synthetic": provenance == "synthetic_cert_fixture",
        }
        if source_events_scanned < sample_size:
            sample.append(row)
        else:
            replacement = rng.randrange(source_events_scanned + 1)
            if replacement < sample_size:
                sample[replacement] = row
        source_events_scanned += 1

    if not sample:
        raise ValueError(f"No usable telemetry records found in {cert_dir}")
    result = pd.DataFrame(sample).sort_values("timestamp").reset_index(drop=True)
    result.attrs.update({
        "source_events_scanned": source_events_scanned,
        "sampled_events": len(result),
        "source_complete": True,
        "sampling": "deterministic uniform reservoir",
        "events_without_measured_bytes": events_without_measured_bytes,
    })
    return result


def train_val_test_chronological_split(
    df: pd.DataFrame,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
) -> DatasetSplits:
    """Chronologically split real samples and fit scaling only on training data."""
    if not 0 < train_ratio < 1 or not 0 <= val_ratio < 1 or train_ratio + val_ratio >= 1:
        raise ValueError("train_ratio and val_ratio must leave non-empty train and test partitions")
    df_sorted = df.sort_values("timestamp").reset_index(drop=True)
    n = len(df_sorted)
    if n < 3:
        raise ValueError("At least three sampled events are required for chronological splitting")

    train_end_idx = max(1, int(n * train_ratio))
    val_end_idx = max(train_end_idx + 1, int(n * (train_ratio + val_ratio)))
    val_end_idx = min(val_end_idx, n - 1)
    df_train = df_sorted.iloc[:train_end_idx].copy()
    df_val = df_sorted.iloc[train_end_idx:val_end_idx].copy()
    df_test = df_sorted.iloc[val_end_idx:].copy()
    split_date_1 = df_val["timestamp"].iloc[0]
    split_date_2 = df_test["timestamp"].iloc[0]

    X_train_raw = df_train[FEATURE_NAMES].to_numpy(dtype=float)
    y_train = df_train["y"].to_numpy(dtype=int)
    X_val_raw = df_val[FEATURE_NAMES].to_numpy(dtype=float)
    y_val = df_val["y"].to_numpy(dtype=int)
    X_test_raw = df_test[FEATURE_NAMES].to_numpy(dtype=float)
    y_test = df_test["y"].to_numpy(dtype=int)

    scaler = RobustScaler()
    X_train = scaler.fit_transform(X_train_raw)
    X_val = scaler.transform(X_val_raw)
    X_test = scaler.transform(X_test_raw)
    metadata_fields = ["eventId", "timestamp", "entityId", "provenance", "is_synthetic"]

    return DatasetSplits(
        X_train=X_train,
        y_train=y_train,
        train_metadata=df_train[metadata_fields].to_dict(orient="records"),
        X_val=X_val,
        y_val=y_val,
        val_metadata=df_val[metadata_fields].to_dict(orient="records"),
        X_test=X_test,
        y_test=y_test,
        test_metadata=df_test[metadata_fields].to_dict(orient="records"),
        scaler=scaler,
        split_dates=(split_date_1, split_date_2),
    )
