"""
Astra Spark Streaming & Windowed Feature Aggregation Service
Consumes events, applies strictly causal sliding-window aggregations into the 8-feature schema,
tracks data quality counters, and delivers model-ready feature vectors.
"""

import logging
import json
import os
import time
from urllib.request import urlopen
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
import numpy as np

logger = logging.getLogger("astra.spark")

FEATURE_NAMES = [
    "egress_mb",
    "sensitive_files",
    "off_hours",
    "new_device",
    "peer_deviation",
    "baseline_egress",
    "behavior_drift",
    "session_count"
]

SENSITIVE_EXTENSIONS = {".kdbx", ".key", ".pem", ".zip", ".tar", ".gz", ".7z", ".xlsx", ".pdf", ".sql", ".conf"}

class AstraSparkAggregator:
    def __init__(self):
        # Sliding-window causal state storage
        self.known_devices: Dict[str, set] = defaultdict(set)
        self.user_egress_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
        self.daily_session_counts: Dict[str, int] = defaultdict(int)
        self.daily_session_dates: Dict[str, Any] = {}
        self.department_egress: Dict[str, deque] = defaultdict(lambda: deque(maxlen=500))
        
        # Data quality metrics
        self.metrics = {
            "events_processed": 0,
            "malformed_events": 0,
            "duplicate_events": 0,
            "late_arrival_events": 0,
            "events_without_measured_bytes": 0,
            "features_generated": 0,
            "start_time": time.time(),
            "last_event_time": None
        }
        self.seen_event_ids: set = set()
        self._recent_event_ids = deque()
        self._event_id_cache_limit = 100_000

    def probe_spark(self) -> Dict[str, Any]:
        """Probes the configured Spark master's REST endpoint without starting a local runtime."""
        try:
            master_url = os.environ.get("SPARK_MASTER_URL", "http://localhost:8080").rstrip("/")
            with urlopen(f"{master_url}/json/", timeout=1.0) as response:
                payload = json.loads(response.read())
            state = payload.get("status")
            if state != "ALIVE":
                raise RuntimeError(f"Spark master reported state {state!r}")
            active_apps = payload.get("activeapps", [])
            processor_application_active = any(
                app.get("name") == "Astra-CERT-Kafka-Feature-Bridge"
                for app in active_apps
            )
            return {
                "online": True,
                "status": "connected",
                "engine": "Apache Spark cluster master",
                "master_url": master_url,
                "workers": len(payload.get("workers", [])),
                "active_applications": len(active_apps),
                "processor_application_active": processor_application_active,
            }
        except Exception as e:
            return {
                "online": False,
                "status": "unavailable",
                "engine": "Apache Spark Structured Streaming",
                "master_url": os.environ.get("SPARK_MASTER_URL", "http://localhost:8080"),
                "diagnostic": f"Could not verify the Spark master: {e}",
                "remedy": "Start the combined Docker Compose stack with the streaming services enabled",
            }

    def process_event(self, event: Dict[str, Any]) -> Tuple[Optional[Dict[str, float]], Dict[str, Any]]:
        """
        Processes a single event into the canonical 8-feature representation with zero future leakage.
        Returns (feature_dict, quality_report).
        """
        event_id = str(event.get("eventId") or event.get("id", ""))
        user = str(event.get("user") or event.get("entityId", "unknown"))
        pc = str(event.get("pc") or event.get("device", "EP-UNKNOWN"))
        raw_bytes = float(event.get("bytes", 0))
        if not event.get("bytesKnown", "bytes" in event):
            self.metrics["events_without_measured_bytes"] += 1
        filename = str(event.get("filename") or event.get("resource", ""))
        raw_time = event.get("timestamp") or event.get("date")

        # Duplicate checking
        if event_id and event_id in self.seen_event_ids:
            self.metrics["duplicate_events"] += 1
            return None, {"status": "duplicate", "eventId": event_id}
        if event_id:
            if len(self._recent_event_ids) >= self._event_id_cache_limit:
                expired_id = self._recent_event_ids.popleft()
                self.seen_event_ids.discard(expired_id)
            self._recent_event_ids.append(event_id)
            self.seen_event_ids.add(event_id)

        # Parse timestamp safely
        try:
            if isinstance(raw_time, datetime):
                dt = raw_time
            elif isinstance(raw_time, str):
                dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            else:
                dt = datetime.now(timezone.utc)
        except Exception:
            try:
                import pandas as pd
                dt = pd.to_datetime(raw_time).to_pydatetime()
            except Exception:
                self.metrics["malformed_events"] += 1
                return None, {"status": "malformed_timestamp", "eventId": event_id}

        self.metrics["events_processed"] += 1
        self.metrics["last_event_time"] = dt.isoformat()

        # Check late arrivals (event time earlier than last seen for this user)
        last_t = getattr(self, f"_last_t_{user}", None)
        if last_t and dt < last_t:
            self.metrics["late_arrival_events"] += 1
        setattr(self, f"_last_t_{user}", dt)

        # Feature 1: Egress MB
        egress_mb = round(raw_bytes / (1024 * 1024), 4)

        # Feature 2: Sensitive files
        is_sensitive = 1.0 if any(filename.lower().endswith(ext) for ext in SENSITIVE_EXTENSIONS) else 0.0

        # Feature 3: Off hours (08:00 - 18:00 or weekends)
        is_off_hours = 1.0 if (dt.hour < 8 or dt.hour >= 18 or dt.weekday() >= 5) else 0.0

        # Feature 4: New device detection (first time this PC seen for user)
        user_devices = self.known_devices[user]
        is_new_device = 1.0 if (pc not in user_devices and len(user_devices) > 0) else 0.0
        user_devices.add(pc)

        # Feature 5: Baseline egress (rolling 30-event historical average)
        history = self.user_egress_history[user]
        baseline_egress = float(np.mean(history)) if history else 15.0
        history.append(egress_mb)

        # Feature 6: Behavior drift (divergence from baseline)
        behavior_drift = max(0.0, float(egress_mb - baseline_egress))

        # Feature 7: Session count (cumulative events today)
        event_day = dt.date()
        if self.daily_session_dates.get(user) != event_day:
            self.daily_session_counts[user] = 0
            self.daily_session_dates[user] = event_day
        self.daily_session_counts[user] += 1
        session_count = float(self.daily_session_counts[user])

        # Feature 8: Peer deviation
        dept = event.get("department", "Engineering")
        dept_history = self.department_egress[dept]
        dept_mean = float(np.mean(dept_history)) if dept_history else 20.0
        dept_history.append(egress_mb)
        peer_deviation = round(float(egress_mb / max(dept_mean, 1.0)), 3)

        features = {
            "egress_mb": egress_mb,
            "sensitive_files": is_sensitive,
            "off_hours": is_off_hours,
            "new_device": is_new_device,
            "peer_deviation": peer_deviation,
            "baseline_egress": round(baseline_egress, 2),
            "behavior_drift": round(behavior_drift, 2),
            "session_count": session_count
        }

        self.metrics["features_generated"] += 1
        return features, {"status": "ok", "eventId": event_id, "user": user}

    def get_metrics(self) -> Dict[str, Any]:
        elapsed = max(0.001, time.time() - self.metrics["start_time"])
        eps = round(self.metrics["events_processed"] / elapsed, 2)
        return {
            **self.metrics,
            "events_per_second": eps,
            "spark_runtime": self.probe_spark()
        }

spark_aggregator = AstraSparkAggregator()
