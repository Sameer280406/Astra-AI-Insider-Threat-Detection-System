"""
Astra Insider Threat Detection Models
Integrates Isolation Forest, Autoencoder, LSTM Sequence, GNN, and Ensemble classifiers.
Compatible with backend/app/models/model_classes.py.
"""

from typing import Dict, List, Optional, Tuple
import numpy as np
import joblib
from sklearn.ensemble import IsolationForest
from sklearn.neural_network import MLPRegressor
from sklearn.linear_model import LogisticRegression
from xgboost import XGBClassifier

from app.models.model_classes import AutoencoderModel, LSTMSequenceModel, GNNModel, FEATURE_NAMES

def train_isolation_forest(
    X_train: np.ndarray,
    contamination: float = 0.05,
    random_state: int = 42
) -> Tuple[IsolationForest, float]:
    """Trains Isolation Forest unsupervised baseline."""
    iso = IsolationForest(
        n_estimators=200,
        contamination=contamination,
        max_samples="auto",
        random_state=random_state,
        n_jobs=-1
    )
    iso.fit(X_train)
    iso.version_ = "trained-isoforest-v3.0"
    return iso

def score_isolation_forest(model: IsolationForest, X: np.ndarray) -> np.ndarray:
    """Computes normalized [0.01, 0.99] anomaly scores from Isolation Forest."""
    raw = model.score_samples(X)
    return np.clip((raw + 0.6) / 0.6 * -1.0 + 1.0, 0.01, 0.99)

def train_autoencoder(
    X_train: np.ndarray,
    y_train: Optional[np.ndarray] = None,
    random_state: int = 42
) -> Tuple[AutoencoderModel, float]:
    """Trains MLP reconstruction autoencoder on normal baseline data."""
    X_fit = X_train if y_train is None else X_train[y_train == 0]
    if len(X_fit) == 0:
        X_fit = X_train

    mlp = MLPRegressor(
        hidden_layer_sizes=(16, 6, 16),
        activation="relu",
        solver="adam",
        max_iter=300,
        random_state=random_state,
        batch_size=min(128, len(X_fit))
    )
    mlp.fit(X_fit, X_fit)
    ae = AutoencoderModel(mlp)
    ae.version_ = "trained-ae-v3.0"

    recon = ae.predict(X_fit)
    mse = np.mean((X_fit - recon) ** 2, axis=1)
    p95 = float(np.percentile(mse, 95))
    if p95 <= 0.0:
        p95 = 0.15

    return ae, p95

def score_autoencoder(model: AutoencoderModel, p95: float, X: np.ndarray) -> np.ndarray:
    """Computes reconstruction MSE normalized by training p95 threshold."""
    recon = model.predict(X)
    mse = np.mean((X - recon) ** 2, axis=1)
    return np.clip(mse / p95, 0.01, 0.99)

def create_lstm_model(feature_weights: Optional[np.ndarray] = None) -> LSTMSequenceModel:
    """Initializes the temporal sequence anomaly detector."""
    if feature_weights is None:
        feature_weights = np.array([0.28, 0.24, 0.08, 0.05, 0.18, 0.01, 0.14, 0.02])
    lstm = LSTMSequenceModel(feature_weights=feature_weights, temporal_decay=0.85)
    lstm.version_ = "trained-lstm-v3.0"
    return lstm

def score_lstm(model: LSTMSequenceModel, X: np.ndarray, seq_len: int = 10) -> np.ndarray:
    """Scores events assuming recent temporal sequence."""
    X_seq = np.repeat(X[:, np.newaxis, :], seq_len, axis=1)
    return model.predict(X_seq).flatten()

def create_gnn_model(feature_weights: Optional[np.ndarray] = None) -> GNNModel:
    """Initializes the graph relational anomaly detector."""
    if feature_weights is None:
        feature_weights = np.array([0.28, 0.24, 0.08, 0.05, 0.18, 0.01, 0.14, 0.02])
    gnn = GNNModel(feature_weights=feature_weights)
    gnn.version_ = "trained-gnn-v3.0"
    return gnn

def score_gnn(model: GNNModel, X: np.ndarray) -> np.ndarray:
    """Scores relational anomaly with default bipartite entity-device topology."""
    default_graph = {"nodes": ["user", "pc", "resource"], "edges": [{"source": "user", "target": "pc"}, {"source": "pc", "target": "resource"}]}
    scores = []
    for i in range(len(X)):
        feat_dict = {FEATURE_NAMES[k]: X[i, k] for k in range(len(FEATURE_NAMES))}
        scores.append(model.infer(feat_dict, default_graph))
    return np.array(scores)

def train_supervised_classifier(
    X_train: np.ndarray,
    y_train: np.ndarray,
    model_type: str = "xgboost",
    class_weight: Optional[str] = "balanced",
    random_state: int = 42
):
    """Trains supervised classifier with class imbalance handling."""
    n_pos = int(np.sum(y_train == 1))
    n_neg = int(np.sum(y_train == 0))
    scale_pos = max(1.0, float(n_neg) / max(1.0, float(n_pos))) if class_weight == "balanced" else 1.0

    if model_type == "xgboost":
        clf = XGBClassifier(
            n_estimators=150,
            learning_rate=0.05,
            max_depth=4,
            subsample=0.8,
            colsample_bytree=0.8,
            scale_pos_weight=scale_pos,
            random_state=random_state,
            eval_metric="logloss"
        )
    elif model_type == "logistic_regression":
        clf = LogisticRegression(
            class_weight="balanced" if class_weight == "balanced" else None,
            max_iter=500,
            random_state=random_state
        )
    else:
        raise ValueError(f"Unknown model_type: {model_type}")

    clf.fit(X_train, y_train)
    return clf

def train_ensemble_meta_model(
    meta_X_train: np.ndarray,
    y_train: np.ndarray,
    random_state: int = 42
) -> XGBClassifier:
    """
    Trains the ensemble fusion meta-model over base detector predictions:
    [if_score, ae_score, lstm_score, gnn_score, conf_if, conf_ae, conf_lstm, conf_gnn].
    """
    n_pos = int(np.sum(y_train == 1))
    n_neg = int(np.sum(y_train == 0))
    scale_pos = max(1.0, float(n_neg) / max(1.0, float(n_pos)))

    ensemble = XGBClassifier(
        n_estimators=100,
        learning_rate=0.05,
        max_depth=3,
        subsample=0.8,
        scale_pos_weight=scale_pos,
        random_state=random_state,
        eval_metric="logloss"
    )
    ensemble.fit(meta_X_train, y_train)
    ensemble.version_ = "trained-xgboost-ensemble-v3.0"
    return ensemble
