"""
Astra Controlled 4-Way ML Experimentation Framework
Compares:
  - Exp A: Real data only (unaugmented baseline)
  - Exp B: Real data with class weighting / algorithmic rebalancing
  - Exp C: Real data + domain-informed synthetic anomaly augmentation (1%, 5%, 10%)
  - Exp D: Calibrated multi-detector fusion ensemble
Evaluates strictly on held-out, unaugmented real test partition.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import json
import logging
from typing import Dict, List
import numpy as np
import pandas as pd

from ml_training.cert_schema import validate_cert_directory
from ml_training.feature_pipeline import (
    extract_features_from_cert, train_val_test_chronological_split, FEATURE_NAMES
)
from ml_training.synthetic_augmentation import augment_training_split
from ml_training.models import (
    train_isolation_forest, score_isolation_forest,
    train_autoencoder, score_autoencoder,
    create_lstm_model, score_lstm,
    create_gnn_model, score_gnn,
    train_supervised_classifier, train_ensemble_meta_model
)
from ml_training.evaluator import evaluate_predictions, calibrate_decision_threshold, EvaluationMetrics
from app.services.cert_dataset import resolve_dataset

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("astra.experiments")

def run_experiments(
    data_dir: Path,
    output_file: Path,
    seed: int = 42,
    sample_size: int = 500_000,
) -> Dict:
    logger.info("=" * 70)
    logger.info("ASTRA CONTROLLED 4-WAY EXPERIMENTATION RUNNER")
    logger.info("=" * 70)

    # 1. Dataset Resolution & Validation
    target_path, data_provenance = resolve_dataset(data_dir)
    logger.info("Using CERT source %s (%s)", target_path, data_provenance)

    validation_report = validate_cert_directory(target_path)
    if not validation_report.is_valid:
        raise ValueError("CERT source validation failed: " + "; ".join(validation_report.errors))
    logger.info(f"Schema Validation Passed: {validation_report.is_valid}")
    logger.info(
        "Schema sample: up to 500 rows/file; sampled unique users=%d",
        validation_report.users_count,
    )

    # 2. Feature Extraction & Chronological Splitting
    logger.info("Streaming source events into causal features (reservoir size %d)...", sample_size)
    df_features = extract_features_from_cert(target_path, sample_size=sample_size, seed=seed)
    is_real_cert = not bool(df_features["is_synthetic"].any())
    splits = train_val_test_chronological_split(df_features, train_ratio=0.70, val_ratio=0.15)
    
    n_train_pos = int(np.sum(splits.y_train == 1))
    n_val_pos = int(np.sum(splits.y_val == 1))
    n_test_pos = int(np.sum(splits.y_test == 1))
    if not all((0 < positives < size) for positives, size in (
        (n_train_pos, len(splits.y_train)),
        (n_val_pos, len(splits.y_val)),
        (n_test_pos, len(splits.y_test)),
    )):
        raise ValueError(
            "Each chronological partition must contain positive and negative labels; "
            "increase sample_size or verify the r4.2 labels and date windows."
        )

    logger.info(f"Chronological Split: Train={len(splits.y_train)} (pos={n_train_pos}), Val={len(splits.y_val)} (pos={n_val_pos}), Test={len(splits.y_test)} (pos={n_test_pos})")
    logger.info(f"Split Timestamps: Val Start={splits.split_dates[0]}, Test Start={splits.split_dates[1]}")

    results = {
        "metadata": {
            "is_real_cert_logs": is_real_cert,
            "source_is_synthetic": not is_real_cert,
            "data_provenance": data_provenance,
            "data_path": str(target_path),
            "source_events_scanned": df_features.attrs["source_events_scanned"],
            "sampled_events": df_features.attrs["sampled_events"],
            "sample_method": df_features.attrs["sampling"],
            "events_without_measured_bytes": df_features.attrs["events_without_measured_bytes"],
            "train_size": len(splits.y_train),
            "val_size": len(splits.y_val),
            "test_size": len(splits.y_test),
            "train_pos": n_train_pos,
            "test_pos": n_test_pos,
            "seed": seed
        },
        "experiments": {}
    }

    # Helper for converting metrics to dict
    def to_dict(m: EvaluationMetrics) -> Dict:
        return {
            "model": m.model_name, "threshold": m.threshold,
            "precision": m.precision, "recall": m.recall, "f1": m.f1,
            "pr_auc": m.pr_auc, "roc_auc": m.roc_auc,
            "tp": m.true_positives, "fp": m.false_positives,
            "tn": m.true_negatives, "fn": m.false_negatives,
            "false_alerts_per_user_day": m.false_alerts_per_user_day,
            "incident_recall": m.incident_recall
        }

    # =========================================================================
    # EXPERIMENT A: Real CERT Data Only (Unaugmented Baseline)
    # =========================================================================
    logger.info("\n>>> EXPERIMENT A: Real Data Only (Unaugmented)")
    # Model 1: Isolation Forest
    iso_a = train_isolation_forest(splits.X_train, contamination=0.05, random_state=seed)
    if_scores_test = score_isolation_forest(iso_a, splits.X_test)
    m_if_a = evaluate_predictions(splits.y_test, if_scores_test, splits.test_metadata, model_name="IsolationForest_ExpA", threshold=0.50)

    # Model 2: Autoencoder
    ae_a, p95_a = train_autoencoder(splits.X_train, splits.y_train, random_state=seed)
    ae_scores_test = score_autoencoder(ae_a, p95_a, splits.X_test)
    m_ae_a = evaluate_predictions(splits.y_test, ae_scores_test, splits.test_metadata, model_name="Autoencoder_ExpA", threshold=0.50)

    # Model 3: Unweighted XGBoost
    xgb_a = train_supervised_classifier(splits.X_train, splits.y_train, model_type="xgboost", class_weight=None, random_state=seed)
    xgb_scores_test = xgb_a.predict_proba(splits.X_test)[:, 1] if hasattr(xgb_a, "predict_proba") else xgb_a.predict(splits.X_test)
    m_xgb_a = evaluate_predictions(splits.y_test, xgb_scores_test, splits.test_metadata, model_name="XGBoost_Unweighted_ExpA", threshold=0.50)

    results["experiments"]["Exp_A_Real_Only"] = {
        "IsolationForest": to_dict(m_if_a),
        "Autoencoder": to_dict(m_ae_a),
        "XGBoost_Unweighted": to_dict(m_xgb_a)
    }

    # =========================================================================
    # EXPERIMENT B: Real Data with Class Weighting / Resampling
    # =========================================================================
    logger.info("\n>>> EXPERIMENT B: Real Data with Class Weighting")
    # Weighted XGBoost
    xgb_b = train_supervised_classifier(splits.X_train, splits.y_train, model_type="xgboost", class_weight="balanced", random_state=seed)
    xgb_b_scores = xgb_b.predict_proba(splits.X_test)[:, 1]
    m_xgb_b = evaluate_predictions(splits.y_test, xgb_b_scores, splits.test_metadata, model_name="XGBoost_Weighted_ExpB", threshold=0.50)

    # Balanced Logistic Regression
    lr_b = train_supervised_classifier(splits.X_train, splits.y_train, model_type="logistic_regression", class_weight="balanced", random_state=seed)
    lr_b_scores = lr_b.predict_proba(splits.X_test)[:, 1]
    m_lr_b = evaluate_predictions(splits.y_test, lr_b_scores, splits.test_metadata, model_name="LogisticRegression_Balanced_ExpB", threshold=0.50)

    results["experiments"]["Exp_B_Class_Weighted"] = {
        "XGBoost_Weighted": to_dict(m_xgb_b),
        "LogisticRegression_Balanced": to_dict(m_lr_b)
    }

    # =========================================================================
    # EXPERIMENT C: Real Data + Domain-Informed Synthetic Augmentation
    # =========================================================================
    logger.info("\n>>> EXPERIMENT C: Real Data + Synthetic Augmentation at Multiple Ratios")
    ratios = [0.01, 0.05, 0.10]
    exp_c_res = {}

    best_aug_ratio = 0.05
    best_aug_f1 = -1.0
    best_aug_data = None

    for ratio in ratios:
        X_train_c, y_train_c, meta_c = augment_training_split(
            splits.X_train, splits.y_train, splits.train_metadata,
            splits.scaler, augmentation_ratio=ratio, seed=seed
        )
        xgb_c = train_supervised_classifier(X_train_c, y_train_c, model_type="xgboost", class_weight="balanced", random_state=seed)
        # Select augmentation ratios on validation only; reserve test for final reporting.
        val_scores = xgb_c.predict_proba(splits.X_val)[:, 1]
        test_scores = xgb_c.predict_proba(splits.X_test)[:, 1]
        m_val = evaluate_predictions(
            splits.y_val, val_scores, splits.val_metadata,
            model_name=f"XGBoost_Aug_{int(ratio*100)}pct_ExpC_Val", threshold=0.50
        )
        m_test = evaluate_predictions(
            splits.y_test, test_scores, splits.test_metadata,
            model_name=f"XGBoost_Aug_{int(ratio*100)}pct_ExpC_Test", threshold=0.50
        )
        exp_c_res[f"ratio_{int(ratio*100)}pct"] = {
            "validation": to_dict(m_val),
            "test": to_dict(m_test),
        }

        if m_val.f1 > best_aug_f1:
            best_aug_f1 = m_val.f1
            best_aug_ratio = ratio
            best_aug_data = (X_train_c, y_train_c, meta_c)

    results["experiments"]["Exp_C_Synthetic_Augmented"] = exp_c_res

    # =========================================================================
    # EXPERIMENT D: Calibrated Multi-Detector Fusion Ensemble
    # =========================================================================
    logger.info("\n>>> EXPERIMENT D: Calibrated Multi-Detector Fusion")
    # Base detector models trained on best training set
    opt_X_train, opt_y_train, _ = best_aug_data if best_aug_data else (splits.X_train, splits.y_train, splits.train_metadata)
    
    iso_d = train_isolation_forest(opt_X_train, random_state=seed)
    ae_d, p95_d = train_autoencoder(opt_X_train, opt_y_train, random_state=seed)
    lstm_d = create_lstm_model()
    gnn_d = create_gnn_model()

    # Meta-features for training ensemble on TRAINING split (not sparse validation)
    # Training on val (69 pos) causes severe underfitting; training split has 221 pos
    if_tr = score_isolation_forest(iso_d, opt_X_train)
    ae_tr = score_autoencoder(ae_d, p95_d, opt_X_train)
    lstm_tr = score_lstm(lstm_d, opt_X_train)
    gnn_tr = score_gnn(gnn_d, opt_X_train)
    conf_tr = np.full_like(if_tr, 0.88)
    meta_tr = np.column_stack([if_tr, ae_tr, lstm_tr, gnn_tr, conf_tr, conf_tr, conf_tr, conf_tr])
    ensemble_d = train_ensemble_meta_model(meta_tr, opt_y_train, random_state=seed)

    # Meta-features for validation and final test splits
    if_val = score_isolation_forest(iso_d, splits.X_val)
    ae_val = score_autoencoder(ae_d, p95_d, splits.X_val)
    lstm_val = score_lstm(lstm_d, splits.X_val)
    gnn_val = score_gnn(gnn_d, splits.X_val)
    conf_val = np.full_like(if_val, 0.88)
    meta_val = np.column_stack([if_val, ae_val, lstm_val, gnn_val, conf_val, conf_val, conf_val, conf_val])

    if_test = score_isolation_forest(iso_d, splits.X_test)
    ae_test = score_autoencoder(ae_d, p95_d, splits.X_test)
    lstm_test = score_lstm(lstm_d, splits.X_test)
    gnn_test = score_gnn(gnn_d, splits.X_test)
    conf_t = np.full_like(if_test, 0.88)
    meta_test = np.column_stack([if_test, ae_test, lstm_test, gnn_test, conf_t, conf_t, conf_t, conf_t])

    ens_raw_test = ensemble_d.predict_proba(meta_test)[:, 1] if hasattr(ensemble_d, "predict_proba") else ensemble_d.predict(meta_test)

    ens_raw_val = ensemble_d.predict_proba(meta_val)[:, 1] if hasattr(ensemble_d, "predict_proba") else ensemble_d.predict(meta_val)
    cal_thresh, m_val_cal = calibrate_decision_threshold(
        splits.y_val, ens_raw_val, splits.val_metadata,
        model_name="Ensemble_Calibrated_ExpD", target_max_false_alert_rate=2.0
    )
    m_test_cal = evaluate_predictions(
        splits.y_test, ens_raw_test, splits.test_metadata,
        model_name="Ensemble_Calibrated_ExpD_Test", threshold=cal_thresh
    )

    results["experiments"]["Exp_D_Calibrated_Fusion"] = {
        "calibrated_threshold": cal_thresh,
        "autoencoder_p95": round(p95_d, 6),
        "best_augmentation_ratio": best_aug_ratio,
        "validation_calibration": to_dict(m_val_cal),
        "Ensemble_Test": to_dict(m_test_cal),
    }

    # 3. Print Summary Table
    logger.info("\n" + "=" * 90)
    logger.info(f"{'Experiment':<22} | {'Model':<25} | {'Prec':<6} | {'Recall':<6} | {'F1':<6} | {'PR-AUC':<6} | {'FA/day':<6} | {'Inc.Rec':<6}")
    logger.info("-" * 90)
    for exp_name, m_dict in results["experiments"].items():
        if isinstance(m_dict, dict):
            for model_k, v in m_dict.items():
                if isinstance(v, dict) and "precision" in v:
                    logger.info(f"{exp_name:<22} | {v['model']:<25} | {v['precision']:<6.4f} | {v['recall']:<6.4f} | {v['f1']:<6.4f} | {v['pr_auc']:<6.4f} | {v['false_alerts_per_user_day']:<6.2f} | {v['incident_recall']:<6.2f}")
    logger.info("=" * 90)

    # Save to disk
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    logger.info(f"Experiment results saved to: {output_file}")

    return {
        "results": results,
        "trained_artifacts": {
            "isolation_forest": iso_d,
            "autoencoder": ae_d,
            "lstm": lstm_d,
            "gnn": gnn_d,
            "ensemble": ensemble_d,
            "scaler": splits.scaler,
            "p95": p95_d,
            "calibrated_threshold": cal_thresh
        }
    }

if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent.parent
    data_dir = base_dir / "data"
    out_file = base_dir / "ml_training" / "experiment_results.json"
    run_experiments(data_dir, out_file)
