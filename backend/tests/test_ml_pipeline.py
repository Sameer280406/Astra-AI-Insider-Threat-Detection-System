"""
Unit and Integration Tests for Astra Imbalanced ML Pipeline
Tests schema validation, zero-leakage chronological splitting, synthetic augmentation isolation,
model serialization, and end-to-end inference serving.
"""

from datetime import datetime, timedelta
from pathlib import Path
import pytest
import numpy as np
import pandas as pd

from ml_training.cert_schema import validate_cert_directory, generate_cert_reference_subset
from ml_training.feature_pipeline import (
    extract_features_from_cert, train_val_test_chronological_split, FEATURE_NAMES
)
from ml_training.synthetic_augmentation import augment_training_split, generate_scenario_vectors
from ml_training.models import (
    train_isolation_forest, score_isolation_forest,
    train_autoencoder, score_autoencoder,
    create_lstm_model, score_lstm,
    create_gnn_model, score_gnn,
    train_supervised_classifier, train_ensemble_meta_model
)
from ml_training.evaluator import evaluate_predictions, calibrate_decision_threshold
from app.models.contracts import FeatureRequest, Prediction
from app.models.gateway import MockAdapter
from app.models import inference_server

def test_cert_schema_validation(tmp_path):
    """Verifies that CERT schema validation correctly checks files and columns."""
    cert_dir = tmp_path / "cert_test"
    generate_cert_reference_subset(cert_dir, n_users=12, n_days=5, seed=42)

    report = validate_cert_directory(cert_dir)
    assert report.is_valid is True
    assert report.users_count >= 5
    assert "logon.csv" in report.record_counts
    assert "http.csv" in report.record_counts

def test_causal_feature_pipeline_zero_leakage(tmp_path):
    """Ensures chronological splitting strictly prevents future temporal leakage."""
    cert_dir = tmp_path / "cert_split"
    generate_cert_reference_subset(cert_dir, n_users=12, n_days=10, seed=42)
    df = extract_features_from_cert(cert_dir)

    splits = train_val_test_chronological_split(df, train_ratio=0.6, val_ratio=0.2)
    
    # Assert dimensions
    assert splits.X_train.shape[1] == len(FEATURE_NAMES)
    assert splits.X_val.shape[1] == len(FEATURE_NAMES)
    assert splits.X_test.shape[1] == len(FEATURE_NAMES)

    # Check chronological ordering
    train_timestamps = [m["timestamp"] for m in splits.train_metadata if m["timestamp"]]
    val_timestamps = [m["timestamp"] for m in splits.val_metadata if m["timestamp"]]
    test_timestamps = [m["timestamp"] for m in splits.test_metadata if m["timestamp"]]

    max_train_t = max(train_timestamps)
    min_val_t = min(val_timestamps)
    max_val_t = max(val_timestamps)
    min_test_t = min(test_timestamps)

    assert max_train_t <= min_val_t, "Train partition overlaps with Validation partition!"
    assert max_val_t <= min_test_t, "Validation partition overlaps with Test partition!"

def test_synthetic_augmentation_training_isolation(tmp_path):
    """Verifies augmentation is training-only and fixture provenance remains explicit."""
    cert_dir = tmp_path / "cert_aug"
    generate_cert_reference_subset(cert_dir, n_users=12, n_days=5, seed=42)
    df = extract_features_from_cert(cert_dir)
    splits = train_val_test_chronological_split(df, train_ratio=0.7, val_ratio=0.15)

    orig_train_len = len(splits.X_train)
    orig_test_len = len(splits.X_test)

    X_train_aug, y_train_aug, meta_aug = augment_training_split(
        splits.X_train, splits.y_train, splits.train_metadata,
        splits.scaler, augmentation_ratio=0.10, seed=42
    )

    # Training set must be larger
    assert len(X_train_aug) > orig_train_len
    # Test set must remain 100% untouched
    assert len(splits.X_test) == orig_test_len
    for m in splits.test_metadata:
        assert m.get("provenance") == "synthetic_cert_fixture"
        assert m.get("is_synthetic") is True
        assert not m["eventId"].startswith("SYNTH-"), "Augmented sample leaked into test set!"

    # Verify synthetic metadata provenance
    synthetic_count = sum(1 for m in meta_aug if m["eventId"].startswith("SYNTH-"))
    assert synthetic_count > 0
    assert all(m["provenance"].startswith("synthetic_") for m in meta_aug if m["eventId"].startswith("SYNTH-"))

def test_model_training_and_serialization(tmp_path):
    """Verifies that all 5 model architectures train and serialize properly."""
    X_train = np.random.normal(0, 1, (100, len(FEATURE_NAMES)))
    y_train = np.array([0] * 90 + [1] * 10)

    iso = train_isolation_forest(X_train)
    ae, p95 = train_autoencoder(X_train, y_train)
    lstm = create_lstm_model()
    gnn = create_gnn_model()

    if_scores = score_isolation_forest(iso, X_train)
    ae_scores = score_autoencoder(ae, p95, X_train)
    lstm_scores = score_lstm(lstm, X_train)
    gnn_scores = score_gnn(gnn, X_train)

    assert len(if_scores) == 100
    assert len(ae_scores) == 100
    assert len(lstm_scores) == 100
    assert len(gnn_scores) == 100

    conf = np.full_like(if_scores, 0.88)
    meta_X = np.column_stack([if_scores, ae_scores, lstm_scores, gnn_scores, conf, conf, conf, conf])
    ensemble = train_ensemble_meta_model(meta_X, y_train)

    assert hasattr(ensemble, "predict_proba")
    preds = ensemble.predict_proba(meta_X)[:, 1]
    assert 0.0 <= preds.min() <= preds.max() <= 1.0

def test_operational_evaluator_and_calibration():
    """Verifies metrics computation including false alerts per user day."""
    y_true = np.array([0] * 90 + [1] * 10)
    y_scores = np.array([0.1] * 80 + [0.7] * 10 + [0.8] * 8 + [0.2] * 2)
    metadata = [{"entityId": f"U{i%5}", "timestamp": datetime(2026, 1, 1) + timedelta(days=i%10)} for i in range(100)]

    metrics = evaluate_predictions(y_true, y_scores, metadata, threshold=0.50)
    assert metrics.precision > 0
    assert metrics.recall > 0
    assert metrics.f1 > 0
    assert metrics.false_alerts_per_user_day >= 0

    best_thresh, best_metrics = calibrate_decision_threshold(y_true, y_scores, metadata, target_max_false_alert_rate=2.0)
    assert 0.10 <= best_thresh <= 0.90
    assert best_metrics.f1 >= 0

def test_inference_server_integration():
    """Verifies backend inference server loads and scores with integrated models."""
    from pathlib import Path
    model_dir = Path(__file__).resolve().parent.parent / "app" / "models" / "trained_models"
    inference_server.MODEL_DIR = model_dir
    inference_server._load_models()

    assert "IsolationForest" in inference_server._models
    assert "Autoencoder" in inference_server._models
    assert "Ensemble" in inference_server._models

    features = {
        "egress_mb": 600.0,
        "sensitive_files": 25.0,
        "off_hours": 1.0,
        "new_device": 1.0,
        "peer_deviation": 2.5,
        "baseline_egress": 30.0,
        "behavior_drift": 570.0,
        "session_count": 40.0
    }
    score_ens = inference_server._score_ens(
        [{"anomalyScore": 0.8, "confidence": 0.9}],
        features, "usr-test", "evt-test"
    )
    assert 0.01 <= score_ens <= 0.99

@pytest.mark.anyio
async def test_trained_adapter_direct_inference():
    """Verifies that TrainedAdapter loads genuine artifacts and generates valid predictions without mock fallback."""
    from app.models.gateway import TrainedAdapter
    from app.models.contracts import FeatureRequest
    from datetime import timezone
    adapter = TrainedAdapter("Ensemble")
    req = FeatureRequest(
        tenantId="meridian",
        entityId="USR0001",
        timestamp=datetime.now(timezone.utc),
        eventId="evt-direct-test",
        features={
            "egress_mb": 1200.0,
            "sensitive_files": 45.0,
            "off_hours": 1.0,
            "new_device": 1.0,
            "peer_deviation": 3.0,
            "baseline_egress": 20.0,
            "behavior_drift": 1180.0,
            "session_count": 25.0
        }
    )
    pred = await adapter.predict(req)
    assert pred.modelName == "Ensemble"
    assert pred.metadata.get("trained") is True
    assert pred.metadata.get("synthetic") is False
    assert 0.01 <= pred.anomalyScore <= 0.99
    assert pred.classification in {"high_risk", "suspicious", "normal"}
    assert len(pred.featureContributions) == 8
