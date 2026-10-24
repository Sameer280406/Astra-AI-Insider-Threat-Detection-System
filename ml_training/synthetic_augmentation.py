"""
Domain-Informed Synthetic Anomaly Augmentation for Astra
Generates realistic insider threat telemetry strictly for training split rebalancing.
"""

from typing import Dict, List, Tuple
import numpy as np
from sklearn.preprocessing import RobustScaler

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

SCENARIO_TYPES = [
    "data_exfiltration",
    "credential_misuse",
    "disgruntled_sabotage",
    "reconnaissance"
]

def generate_scenario_vectors(n_samples: int, scenario: str, seed: int = 42) -> np.ndarray:
    """
    Generates unscaled domain-informed insider threat feature vectors.
    """
    rng = np.random.RandomState(seed)
    
    if scenario == "data_exfiltration":
        baseline = rng.gamma(shape=5.0, scale=10.0, size=n_samples)
        egress = baseline + rng.uniform(400.0, 3500.0, size=n_samples)
        sensitive = rng.randint(15, 75, size=n_samples).astype(float)
        off_hours = rng.binomial(n=1, p=0.40, size=n_samples).astype(float)
        new_dev = rng.binomial(n=1, p=0.20, size=n_samples).astype(float)
        peer_dev = rng.uniform(2.5, 6.5, size=n_samples)
        drift = egress - baseline
        sessions = rng.randint(25, 70, size=n_samples).astype(float)

    elif scenario == "credential_misuse":
        baseline = rng.gamma(shape=5.0, scale=10.0, size=n_samples)
        egress = baseline + rng.uniform(20.0, 180.0, size=n_samples)
        sensitive = rng.randint(5, 30, size=n_samples).astype(float)
        off_hours = np.ones(n_samples, dtype=float)  # Always off-hours
        new_dev = np.ones(n_samples, dtype=float)    # Always new device
        peer_dev = rng.uniform(3.0, 7.0, size=n_samples)
        drift = egress - baseline
        sessions = rng.randint(40, 90, size=n_samples).astype(float)

    elif scenario == "disgruntled_sabotage":
        baseline = rng.gamma(shape=5.0, scale=10.0, size=n_samples)
        egress = baseline + rng.uniform(150.0, 900.0, size=n_samples)
        sensitive = rng.randint(25, 90, size=n_samples).astype(float)
        off_hours = rng.binomial(n=1, p=0.75, size=n_samples).astype(float)
        new_dev = rng.binomial(n=1, p=0.45, size=n_samples).astype(float)
        peer_dev = rng.uniform(4.0, 8.5, size=n_samples)
        drift = egress - baseline
        sessions = rng.randint(80, 150, size=n_samples).astype(float)

    elif scenario == "reconnaissance":
        baseline = rng.gamma(shape=5.0, scale=10.0, size=n_samples)
        egress = baseline + rng.uniform(5.0, 50.0, size=n_samples)  # Low egress
        sensitive = rng.randint(40, 100, size=n_samples).astype(float)  # High sensitive enumeration
        off_hours = rng.binomial(n=1, p=0.50, size=n_samples).astype(float)
        new_dev = rng.binomial(n=1, p=0.30, size=n_samples).astype(float)
        peer_dev = rng.uniform(2.0, 4.5, size=n_samples)
        drift = egress - baseline
        sessions = rng.randint(50, 110, size=n_samples).astype(float)

    else:
        raise ValueError(f"Unknown scenario: {scenario}")

    return np.column_stack([
        egress, sensitive, off_hours, new_dev, peer_dev, baseline, drift, sessions
    ])

def augment_training_split(
    X_train_scaled: np.ndarray,
    y_train: np.ndarray,
    train_metadata: List[Dict],
    scaler: RobustScaler,
    augmentation_ratio: float = 0.05,
    seed: int = 42
) -> Tuple[np.ndarray, np.ndarray, List[Dict]]:
    """
    Augments ONLY the training partition with synthetic anomalies.
    CRITICAL: Never call this on validation or test sets!
    """
    if augmentation_ratio <= 0.0:
        return X_train_scaled.copy(), y_train.copy(), list(train_metadata)

    n_synthetic = int(len(X_train_scaled) * augmentation_ratio)
    per_scenario = max(1, n_synthetic // len(SCENARIO_TYPES))

    synthetic_raw_list = []
    synthetic_meta = []

    for i, scenario in enumerate(SCENARIO_TYPES):
        vectors = generate_scenario_vectors(per_scenario, scenario, seed=seed + i * 10)
        synthetic_raw_list.append(vectors)
        for j in range(len(vectors)):
            synthetic_meta.append({
                "eventId": f"SYNTH-{scenario[:4].upper()}-{j+1}",
                "timestamp": None,
                "entityId": f"SYNTH_USER_{j % 5 + 1}",
                "provenance": f"synthetic_{scenario}",
                "is_synthetic": True
            })

    X_synth_raw = np.vstack(synthetic_raw_list)
    # Scale using the existing scaler fitted strictly on real training data
    X_synth_scaled = scaler.transform(X_synth_raw)
    y_synth = np.ones(len(X_synth_scaled), dtype=int)

    X_train_aug = np.vstack([X_train_scaled, X_synth_scaled])
    y_train_aug = np.concatenate([y_train, y_synth])
    aug_metadata = list(train_metadata) + synthetic_meta

    # Shuffle training set to prevent scenario block grouping
    rng = np.random.RandomState(seed)
    perm = rng.permutation(len(y_train_aug))

    return X_train_aug[perm], y_train_aug[perm], [aug_metadata[idx] for idx in perm]
