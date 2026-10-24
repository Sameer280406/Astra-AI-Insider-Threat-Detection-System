"""
Rigorous Insider Threat Model Evaluator
Computes Precision, Recall, F1, PR-AUC, Confusion Matrix, False Alerts/User-Day, and Incident-Level Recall.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from sklearn.metrics import (
    precision_score, recall_score, f1_score,
    roc_auc_score, average_precision_score, confusion_matrix
)

@dataclass
class EvaluationMetrics:
    model_name: str
    threshold: float
    precision: float
    recall: float
    f1: float
    pr_auc: float
    roc_auc: float
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int
    false_alerts_per_user_day: float
    incident_recall: float
    total_samples: int
    positive_samples: int

def evaluate_predictions(
    y_true: np.ndarray,
    y_scores: np.ndarray,
    metadata: List[Dict],
    model_name: str = "Model",
    threshold: float = 0.50
) -> EvaluationMetrics:
    """
    Evaluates probability/anomaly scores against binary ground truth with operational security metrics.
    """
    y_true = np.asarray(y_true, dtype=int)
    y_scores = np.asarray(y_scores, dtype=float)
    y_pred = (y_scores >= threshold).astype(int)

    n_pos = int(np.sum(y_true == 1))
    n_total = len(y_true)

    # Scikit-learn metrics
    prec = float(precision_score(y_true, y_pred, zero_division=0))
    rec = float(recall_score(y_true, y_pred, zero_division=0))
    f1 = float(f1_score(y_true, y_pred, zero_division=0))

    try:
        pr_auc = float(average_precision_score(y_true, y_scores)) if n_pos > 0 else 0.0
    except Exception:
        pr_auc = 0.0

    try:
        roc_auc = float(roc_auc_score(y_true, y_scores)) if (0 < n_pos < n_total) else 0.5
    except Exception:
        roc_auc = 0.5

    cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
    tn, fp, fn, tp = int(cm[0, 0]), int(cm[0, 1]), int(cm[1, 0]), int(cm[1, 1])

    # False Alerts Per User-Day calculation
    # Group metadata by entity and days
    entities = set()
    timestamps = []
    for m in metadata:
        if m.get("entityId"):
            entities.add(m["entityId"])
        if m.get("timestamp") and isinstance(m["timestamp"], (datetime, pd.Timestamp)):
            timestamps.append(m["timestamp"])

    n_users = max(len(entities), 1)
    if timestamps:
        t_min = min(timestamps)
        t_max = max(timestamps)
        days = max(1.0, (t_max - t_min).total_seconds() / 86400.0)
    else:
        days = 14.0  # Default test period assumption

    false_alerts_per_user_day = round(fp / (n_users * days), 4)

    # Incident-level Detection Rate
    # An incident is detected if at least one event in the incident window is flagged
    incident_groups: Dict[str, List[int]] = {}
    for idx, m in enumerate(metadata):
        if y_true[idx] == 1:
            key = f"{m.get('entityId', 'unknown')}"
            incident_groups.setdefault(key, []).append(y_pred[idx])

    if incident_groups:
        detected_incidents = sum(1 for preds in incident_groups.values() if any(p == 1 for p in preds))
        incident_recall = round(detected_incidents / len(incident_groups), 4)
    else:
        incident_recall = 1.0 if rec > 0.5 else 0.0

    return EvaluationMetrics(
        model_name=model_name,
        threshold=round(threshold, 4),
        precision=round(prec, 4),
        recall=round(rec, 4),
        f1=round(f1, 4),
        pr_auc=round(pr_auc, 4),
        roc_auc=round(roc_auc, 4),
        true_positives=tp,
        false_positives=fp,
        true_negatives=tn,
        false_negatives=fn,
        false_alerts_per_user_day=false_alerts_per_user_day,
        incident_recall=incident_recall,
        total_samples=n_total,
        positive_samples=n_pos
    )

def calibrate_decision_threshold(
    y_true: np.ndarray,
    y_scores: np.ndarray,
    metadata: List[Dict],
    model_name: str = "Model",
    target_max_false_alert_rate: float = 2.0
) -> Tuple[float, EvaluationMetrics]:
    """
    Sweeps thresholds [0.10, 0.90] to calibrate the optimal boundary balancing F1 and SOC alert budget.
    """
    best_thresh = 0.50
    best_metrics = None
    best_f1 = -1.0

    for t in np.linspace(0.15, 0.85, 29):
        metrics = evaluate_predictions(y_true, y_scores, metadata, model_name=model_name, threshold=float(t))
        # Prioritize F1 while respecting false alert budget
        if metrics.false_alerts_per_user_day <= target_max_false_alert_rate:
            if metrics.f1 > best_f1:
                best_f1 = metrics.f1
                best_thresh = float(t)
                best_metrics = metrics

    if best_metrics is None:
        # Fallback to threshold maximizing F1 regardless of budget
        for t in np.linspace(0.20, 0.80, 25):
            metrics = evaluate_predictions(y_true, y_scores, metadata, model_name=model_name, threshold=float(t))
            if metrics.f1 > best_f1:
                best_f1 = metrics.f1
                best_thresh = float(t)
                best_metrics = metrics

    return best_thresh, best_metrics or evaluate_predictions(y_true, y_scores, metadata, model_name=model_name, threshold=0.50)
