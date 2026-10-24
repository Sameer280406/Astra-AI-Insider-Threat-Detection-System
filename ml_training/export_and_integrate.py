"""
Model Export & Integration Script for Astra
Saves trained model weights, preprocessing scaler, calibrated thresholds, and schemas.
Integrates artifacts directly into backend/app/models/trained_models/ and verifies loading.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import json
import logging
import joblib
import numpy as np

from ml_training.feature_pipeline import FEATURE_NAMES

logger = logging.getLogger("astra.integration")
logging.basicConfig(level=logging.INFO, format="%(message)s")

def export_and_integrate_artifacts(trained_artifacts: dict, experiment_results: dict, dest_dir: Path) -> bool:
    logger.info("=" * 70)
    logger.info(f"INTEGRATING TRAINED ARTIFACTS INTO {dest_dir}")
    logger.info("=" * 70)

    dest_dir.mkdir(parents=True, exist_ok=True)

    iso = trained_artifacts["isolation_forest"]
    ae = trained_artifacts["autoencoder"]
    lstm = trained_artifacts["lstm"]
    gnn = trained_artifacts["gnn"]
    ensemble = trained_artifacts["ensemble"]
    scaler = trained_artifacts["scaler"]
    p95 = trained_artifacts["p95"]
    calibrated_threshold = trained_artifacts["calibrated_threshold"]

    # 1. Save model weights
    joblib.dump(scaler, dest_dir / "scaler.joblib")
    joblib.dump(iso, dest_dir / "isolation_forest.joblib")
    joblib.dump(ae, dest_dir / "autoencoder.joblib")
    joblib.dump(lstm, dest_dir / "lstm.joblib")
    joblib.dump(gnn, dest_dir / "gnn.joblib")
    joblib.dump(ensemble, dest_dir / "ensemble.joblib")
    logger.info("Successfully serialized joblib artifacts: scaler, isolation_forest, autoencoder, lstm, gnn, ensemble.")

    # 2. Extract metrics from Exp D
    exp_d_metrics = (
        experiment_results.get("experiments", {})
        .get("Exp_D_Calibrated_Fusion", {})
        .get("Ensemble_Calibrated", {})
    )

    thresholds_payload = {
        "autoencoder_p95": round(float(p95), 6),
        "high_risk_threshold": round(float(calibrated_threshold), 4),
        "suspicious_threshold": round(max(0.20, float(calibrated_threshold) - 0.25), 4),
        "best_augmentation_ratio": experiment_results.get("experiments", {}).get("Exp_D_Calibrated_Fusion", {}).get("best_augmentation_ratio", 0.05),
        "metrics": {
            "Ensemble_precision": exp_d_metrics.get("precision", 0.0),
            "Ensemble_recall": exp_d_metrics.get("recall", 0.0),
            "Ensemble_f1": exp_d_metrics.get("f1", 0.0),
            "Ensemble_pr_auc": exp_d_metrics.get("pr_auc", 0.0),
            "Ensemble_roc_auc": exp_d_metrics.get("roc_auc", 0.0),
            "Ensemble_false_alerts_per_user_day": exp_d_metrics.get("false_alerts_per_user_day", 0.0),
            "Ensemble_incident_recall": exp_d_metrics.get("incident_recall", 0.0)
        },
        "IsolationForest_version": getattr(iso, "version_", "trained-isoforest-v3.0"),
        "Autoencoder_version": getattr(ae, "version_", "trained-ae-v3.0"),
        "LSTM_version": getattr(lstm, "version_", "trained-lstm-v3.0"),
        "GNN_version": getattr(gnn, "version_", "trained-gnn-v3.0"),
        "Ensemble_version": getattr(ensemble, "version_", "trained-xgboost-ensemble-v3.0"),
        "features": FEATURE_NAMES,
        "is_real_cert_logs": experiment_results.get("metadata", {}).get("is_real_cert_logs", False)
    }

    with open(dest_dir / "thresholds.json", "w", encoding="utf-8") as f:
        json.dump(thresholds_payload, f, indent=2)
    logger.info("Saved calibrated thresholds and metadata to thresholds.json.")

    # 3. Validation: Verify that inference_server can load all models cleanly
    logger.info("Verifying inference_server artifact loading...")
    from app.models import inference_server
    inference_server.MODEL_DIR = dest_dir
    inference_server._load_models()

    loaded_keys = list(inference_server._models.keys())
    required_keys = {"IsolationForest", "Autoencoder", "LSTM", "GNN", "Ensemble", "scaler", "thresholds"}
    missing = required_keys - set(loaded_keys)
    if missing:
        raise RuntimeError(f"Inference server failed to load expected models: {missing}")

    # 4. End-to-end prediction check
    test_features = {
        "egress_mb": 1250.0,
        "sensitive_files": 45.0,
        "off_hours": 1.0,
        "new_device": 1.0,
        "peer_deviation": 3.8,
        "baseline_egress": 25.0,
        "behavior_drift": 1225.0,
        "session_count": 60.0
    }
    
    score_if = inference_server._score_if(test_features, "test-user", "test-evt")
    score_ae = inference_server._score_ae(test_features, "test-user", "test-evt")
    score_lstm = inference_server._score_lstm(test_features, [], "test-user", "test-evt")
    score_gnn = inference_server._score_gnn(test_features, {}, "test-user", "test-evt")
    
    prior = [
        {"anomalyScore": score_if, "confidence": 0.90},
        {"anomalyScore": score_ae, "confidence": 0.88},
        {"anomalyScore": score_lstm, "confidence": 0.86},
        {"anomalyScore": score_gnn, "confidence": 0.85}
    ]
    score_ens = inference_server._score_ens(prior, test_features, "test-user", "test-evt")
    
    logger.info(f"Verification Scores: IF={score_if:.4f}, AE={score_ae:.4f}, LSTM={score_lstm:.4f}, GNN={score_gnn:.4f}, Ensemble={score_ens:.4f}")
    assert 0.01 <= score_ens <= 0.991, f"Invalid ensemble prediction: {score_ens}"
    logger.info("Model artifacts verified and integrated into production inference path successfully!")
    return True

if __name__ == "__main__":
    from ml_training.experiments import run_experiments
    base_dir = Path(__file__).resolve().parent.parent
    data_dir = base_dir / "data"
    out_file = base_dir / "ml_training" / "experiment_results.json"
    dest_dir = base_dir / "backend" / "app" / "models" / "trained_models"
    
    run_output = run_experiments(data_dir, out_file)
    export_and_integrate_artifacts(run_output["trained_artifacts"], run_output["results"], dest_dir)
