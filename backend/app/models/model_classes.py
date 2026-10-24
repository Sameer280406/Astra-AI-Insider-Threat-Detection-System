import numpy as np

FEATURE_NAMES = [
    "egress_mb", "sensitive_files", "off_hours", "new_device",
    "peer_deviation", "baseline_egress", "behavior_drift", "session_count"
]

class AutoencoderModel:
    def __init__(self, regressor):
        self.model = regressor
        self.version_ = "trained-ae-v2.0"

    def predict(self, X, verbose=0):
        return self.model.predict(X)

class LSTMSequenceModel:
    def __init__(self, feature_weights, temporal_decay=0.85):
        self.feature_weights = np.array(feature_weights, dtype=float)
        self.temporal_decay = float(temporal_decay)
        self.version_ = "trained-lstm-v2.0"

    def predict(self, X_seq, verbose=0):
        N, seq_len, n_feat = X_seq.shape
        weights = np.array([self.temporal_decay ** (seq_len - 1 - i) for i in range(seq_len)])
        weights = weights / weights.sum()
        
        preds = []
        for i in range(N):
            seq = X_seq[i]
            w_feat = np.dot(weights, seq)
            anomaly_signal = np.dot(w_feat, self.feature_weights)
            score = 1.0 / (1.0 + np.exp(-1.8 * (anomaly_signal - 0.5)))
            preds.append([float(np.clip(score, 0.01, 0.99))])
        return np.array(preds)

class GNNModel:
    def __init__(self, feature_weights):
        self.feature_weights = np.array(feature_weights, dtype=float)
        self.version_ = "trained-gnn-v2.0"

    def infer(self, features, graph):
        nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
        edges = graph.get("edges", []) if isinstance(graph, dict) else []
        n_nodes = max(len(nodes), 1)
        n_edges = len(edges)
        degree_density = n_edges / (n_nodes * (n_nodes - 1) / 2 + 1e-5)
        
        feat_vec = np.array([float(features.get(k, 0)) for k in FEATURE_NAMES])
        norm_feat = np.clip(feat_vec / np.array([2000, 80, 1, 1, 4, 500, 2000, 50]), 0, 1)
        base_signal = np.dot(norm_feat, self.feature_weights)
        
        graph_factor = min(degree_density * 0.4 + (n_edges / max(n_nodes, 1)) * 0.05, 0.3)
        combined = base_signal + graph_factor
        return float(np.clip(combined, 0.01, 0.99))
