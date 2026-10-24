import hashlib, json
from pathlib import Path
import httpx
import joblib
import numpy as np
from .contracts import Prediction, FeatureRequest
from ..core.config import settings
class ModelUnavailable(Exception): pass
class MockAdapter:
    def __init__(self,name): self.name=name
    async def predict(self,r:FeatureRequest):
        weights={'egress_mb':0.36,'sensitive_files':0.30,'off_hours':0.10,'new_device':0.10,'peer_deviation':0.14}
        values={'egress_mb':min(r.features.get('egress_mb',0)/2000,1),'sensitive_files':min(r.features.get('sensitive_files',0)/80,1),'off_hours':r.features.get('off_hours',0),'new_device':r.features.get('new_device',0),'peer_deviation':min(r.features.get('peer_deviation',0)/4,1)}
        jitter=(int(hashlib.sha256((self.name+r.entityId+r.eventId).encode()).hexdigest()[:4],16)%11-5)/100
        score=max(0.01,min(.99,sum(values[k]*w for k,w in weights.items())+jitter))
        if self.name=='Ensemble' and r.predictions: score=sum(p.anomalyScore*p.confidence for p in r.predictions)/sum(p.confidence for p in r.predictions)
        contributions=sorted([{'feature':k,'value':r.features.get(k,0),'contribution':round(v*weights[k],4),'direction':'increases'} for k,v in values.items()],key=lambda x:x['contribution'],reverse=True)
        from ..services.explainability_service import explainability_service
        xai_report = explainability_service.explain(r.features, round(score * 100, 1), None, r.entityId)
        return Prediction(modelName=self.name,modelVersion='mock-1.0',entityId=r.entityId,timestamp=r.timestamp,anomalyScore=round(score,4),riskScore=round(score*100,1),confidence=.86,classification='high_risk' if score>=.65 else 'suspicious' if score>=.4 else 'normal',threatType='Data exfiltration' if values['egress_mb']>.5 else 'Behavioral anomaly',featureContributions=contributions,evidence=[{'eventId':r.eventId,'description':xai_report['headline']}],metadata={'synthetic':True,'schemaVersion':'1.0','xai':xai_report})
class RemoteAdapter:
    def __init__(self,name,url): self.name,self.url=name,url
    async def predict(self,r):
        try:
            async with httpx.AsyncClient(timeout=settings().model_timeout_seconds,follow_redirects=False) as client:
                response=await client.post(self.url,json=r.model_dump(mode='json'),headers={'Authorization':'Bearer '+settings().model_api_key,'X-Tenant-ID':r.tenantId,'Idempotency-Key':r.eventId+':'+self.name})
                response.raise_for_status();p=Prediction.model_validate(response.json())
                if p.modelName!=self.name or p.entityId!=r.entityId: raise ValueError('Model identity mismatch')
                if abs((p.timestamp-r.timestamp).total_seconds())>300: raise ValueError('Model timestamp mismatch')
                return p
        except Exception as e: raise ModelUnavailable(self.name+' prediction unavailable') from e
class TrainedAdapter:
    def __init__(self, name: str):
        self.name = name
        self.model_dir = Path(__file__).resolve().parent / "trained_models"
        self._models = None

    def _ensure_loaded(self):
        if self._models is None:
            files = {
                "IsolationForest": "isolation_forest.joblib",
                "Autoencoder": "autoencoder.joblib",
                "LSTM": "lstm.joblib",
                "GNN": "gnn.joblib",
                "Ensemble": "ensemble.joblib",
                "scaler": "scaler.joblib",
                "thresholds": "thresholds.json"
            }
            loaded = {}
            for k, fn in files.items():
                p = self.model_dir / fn
                if p.exists():
                    try:
                        if fn.endswith(".json"):
                            with open(p, "r", encoding="utf-8") as f:
                                loaded[k] = json.load(f)
                        else:
                            loaded[k] = joblib.load(p)
                    except Exception as e:
                        pass
            self._models = loaded
        if self.name not in self._models:
            raise ModelUnavailable(f"Trained model artifact for {self.name} is missing or corrupted")

    async def predict(self, r: FeatureRequest) -> Prediction:
        self._ensure_loaded()
        feature_names = ["egress_mb", "sensitive_files", "off_hours", "new_device",
                         "peer_deviation", "baseline_egress", "behavior_drift", "session_count"]
        
        # Build feature vector
        vec = np.array([[float(r.features.get(k, 0.0)) for k in feature_names]])
        scaler = self._models.get("scaler")
        X = scaler.transform(vec) if scaler else vec
        
        score = 0.5
        conf = 0.88
        
        if self.name == "IsolationForest":
            raw = self._models["IsolationForest"].score_samples(X)[0]
            score = float(np.clip((raw + 0.6) / 0.6 * -1.0 + 1.0, 0.01, 0.99))
            conf = 0.91
        elif self.name == "Autoencoder":
            recon = self._models["Autoencoder"].predict(X)
            mse = float(np.mean((X - recon) ** 2))
            p95 = (self._models.get("thresholds") or {}).get("autoencoder_p95", 0.22)
            score = float(np.clip(mse / max(p95, 1e-6), 0.01, 0.99))
            conf = 0.89
        elif self.name == "LSTM":
            # 10-timestep sequence
            seq_len = 10
            rows = [list(vec[0])] * seq_len
            X_seq = np.array([rows])
            flat = scaler.transform(X_seq.reshape(-1, len(feature_names))) if scaler else X_seq.reshape(-1, len(feature_names))
            X_s = flat.reshape(1, seq_len, len(feature_names))
            score = float(np.clip(self._models["LSTM"].predict(X_s)[0][0], 0.01, 0.99))
            conf = 0.87
        elif self.name == "GNN":
            score = float(np.clip(self._models["GNN"].infer(r.features, {}), 0.01, 0.99))
            conf = 0.85
        elif self.name == "Ensemble":
            if r.predictions:
                scores = [p.anomalyScore for p in r.predictions]
                confs = [p.confidence for p in r.predictions]
                # Pad to 4 prior scores + 4 confs
                while len(scores) < 4: scores.append(0.5)
                while len(confs) < 4: confs.append(0.85)
                meta = np.array([scores[:4] + confs[:4]])
                m = self._models["Ensemble"]
                if hasattr(m, "predict_proba"):
                    score = float(m.predict_proba(meta)[0][1])
                else:
                    score = float(m.predict(meta)[0])
                conf = min(0.99, float(np.mean(confs) + 0.04))
            else:
                # Compute base detectors automatically for genuine fusion
                if_raw = self._models["IsolationForest"].score_samples(X)[0]
                if_s = float(np.clip((if_raw + 0.6) / 0.6 * -1.0 + 1.0, 0.01, 0.99))
                
                recon = self._models["Autoencoder"].predict(X)
                mse = float(np.mean((X - recon) ** 2))
                p95 = (self._models.get("thresholds") or {}).get("autoencoder_p95", 0.22)
                ae_s = float(np.clip(mse / max(p95, 1e-6), 0.01, 0.99))
                
                seq_len = 10
                rows = [list(vec[0])] * seq_len
                X_seq = np.array([rows])
                flat = scaler.transform(X_seq.reshape(-1, len(feature_names))) if scaler else X_seq.reshape(-1, len(feature_names))
                X_s = flat.reshape(1, seq_len, len(feature_names))
                lstm_s = float(np.clip(self._models["LSTM"].predict(X_s)[0][0], 0.01, 0.99))
                
                gnn_s = float(np.clip(self._models["GNN"].infer(r.features, {}), 0.01, 0.99))
                
                meta = np.array([[if_s, ae_s, lstm_s, gnn_s, 0.91, 0.89, 0.87, 0.85]])
                m = self._models["Ensemble"]
                if hasattr(m, "predict_proba"):
                    score = float(m.predict_proba(meta)[0][1])
                else:
                    score = float(m.predict(meta)[0])
                conf = 0.92

        score = round(float(np.clip(score, 0.01, 0.99)), 4)
        risk_score = round(score * 100.0, 1)

        # Explainable AI (XAI) Synthesis
        from ..services.explainability_service import explainability_service
        model_scores_map = {}
        if self.name == 'Ensemble':
            if r.predictions:
                model_scores_map = {p.modelName: p.riskScore for p in r.predictions}
            else:
                model_scores_map = {
                    "IsolationForest": round(if_s * 100.0, 1),
                    "Autoencoder": round(ae_s * 100.0, 1),
                    "LSTM": round(lstm_s * 100.0, 1),
                    "GNN": round(gnn_s * 100.0, 1)
                }
        xai_report = explainability_service.explain(
            r.features,
            risk_score,
            model_scores_map if model_scores_map else None,
            r.entityId
        )

        contributions = [
            {
                "feature": attr["feature"],
                "value": attr["value"],
                "contribution": round(min(1.0, max(0.0, attr["importance_pct"] / 100.0)), 4),
                "direction": "increases" if attr["direction"] == "increases_risk" else "decreases"
            }
            for attr in xai_report["feature_attributions"]
        ]

        threat_type = (
            "Data exfiltration" if r.features.get("egress_mb", 0) > 500
            else "Credential misuse" if r.features.get("new_device", 0) > 0 and r.features.get("off_hours", 0) > 0
            else "Anomalous peer behaviour" if r.features.get("peer_deviation", 0) > 2.0
            else "Sensitive data access" if r.features.get("sensitive_files", 0) > 20
            else "Behavioural anomaly"
        )
        
        thresh_info = (self._models.get("thresholds") or {})
        high_thresh = thresh_info.get("high_risk_threshold", 0.70)
        susp_thresh = thresh_info.get("suspicious_threshold", 0.45)
        
        classification = "high_risk" if score >= high_thresh else "suspicious" if score >= susp_thresh else "normal"
        top_feature = contributions[0]["feature"] if contributions else "egress_mb"
        
        version_str = thresh_info.get(f"{self.name}_version", "trained-v3.0")

        return Prediction(
            modelName=self.name,
            modelVersion=version_str,
            entityId=r.entityId,
            timestamp=r.timestamp,
            anomalyScore=score,
            riskScore=risk_score,
            confidence=round(conf, 4),
            classification=classification,
            threatType=threat_type,
            featureContributions=contributions,
            evidence=[{"eventId": r.eventId, "description": xai_report["headline"]}],
            metadata={"synthetic": False, "trained": True, "schemaVersion": "1.0", "provenance": "cert_pipeline", "xai": xai_report}
        )

class AdapterRegistry:
    def __init__(self):
        self.factories = {
            'mock': MockAdapter,
            'remote': RemoteAdapter,
            'trained': TrainedAdapter
        }
    def register(self, mode, factory):
        self.factories[mode] = factory
    def resolve(self, name):
        s = settings()
        if s.model_mode == 'mock':
            return self.factories['mock'](name)
        elif s.model_mode == 'trained':
            return self.factories['trained'](name)
        endpoint = s.model_endpoints.get(name)
        if not endpoint:
            raise ModelUnavailable(name + ' endpoint not configured')
        if s.environment == 'production' and not endpoint.startswith('https://'):
            raise ModelUnavailable('HTTPS endpoint required')
        return self.factories['remote'](name, endpoint)

registry = AdapterRegistry()
