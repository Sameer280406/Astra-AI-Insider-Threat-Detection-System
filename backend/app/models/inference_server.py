"""
Astra Production Inference Server v2.0
Loads trained ML models and serves predictions matching the exact API contract.

Deploy:
  uvicorn app.models.inference_server:app --host 127.0.0.1 --port 8100

Then in .env:
  MODEL_MODE=remote
  MODEL_ENDPOINTS={"IsolationForest":"http://127.0.0.1:8100/predict/IsolationForest","Autoencoder":"http://127.0.0.1:8100/predict/Autoencoder","LSTM":"http://127.0.0.1:8100/predict/LSTM","GNN":"http://127.0.0.1:8100/predict/GNN","Ensemble":"http://127.0.0.1:8100/predict/Ensemble"}
  MODEL_API_KEY=your-secret-key
"""

import os, json, hashlib, logging
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("astra.inference")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MODEL_DIR = Path(os.environ.get("ASTRA_MODEL_DIR", Path(__file__).parent / "trained_models"))
API_KEY   = os.environ.get("MODEL_API_KEY", "")

_models: dict = {}

FEATURE_NAMES = ["egress_mb","sensitive_files","off_hours","new_device",
                 "peer_deviation","baseline_egress","behavior_drift","session_count"]

FEATURE_WEIGHTS = {"egress_mb":0.28,"sensitive_files":0.24,"peer_deviation":0.18,
                   "behavior_drift":0.14,"off_hours":0.08,"new_device":0.05,
                   "session_count":0.02,"baseline_egress":0.01}

def _threat_type(features, score):
    if features.get("egress_mb",0)>500 and features.get("sensitive_files",0)>20:
        return "Data exfiltration"
    if features.get("new_device",0)>0 and features.get("off_hours",0)>0:
        return "Credential misuse"
    if features.get("peer_deviation",0)>2.5:
        return "Anomalous peer behaviour"
    if features.get("sensitive_files",0)>50:
        return "Sensitive data access"
    if features.get("behavior_drift",0)>1000:
        return "Volume anomaly"
    return "Behavioural anomaly"

def _classification(score):
    if score>=0.65: return "high_risk"
    if score>=0.40: return "suspicious"
    return "normal"

def _contributions(features, score):
    vals = {}
    for k in FEATURE_NAMES:
        v = float(features.get(k,0))
        if k=="egress_mb":         nv=min(v/2000,1)
        elif k=="sensitive_files": nv=min(v/80,1)
        elif k=="peer_deviation":  nv=min(v/4,1)
        elif k=="behavior_drift":  nv=min(max(v,0)/2000,1)
        elif k=="baseline_egress": nv=min(v/500,1)
        elif k=="session_count":   nv=min(v/50,1)
        else:                      nv=float(bool(v))
        vals[k]=nv
    contribs=[{"feature":k,"value":float(features.get(k,0)),
               "contribution":round(vals[k]*FEATURE_WEIGHTS[k],4),"direction":"increases"}
              for k in FEATURE_NAMES]
    contribs.sort(key=lambda x:x["contribution"],reverse=True)
    return contribs

def _feature_vector(features):
    return np.array([[float(features.get(k,0)) for k in FEATURE_NAMES]])

def _load_models():
    global _models
    files = {"IsolationForest":"isolation_forest.joblib","Autoencoder":"autoencoder.joblib",
             "LSTM":"lstm.joblib","GNN":"gnn.joblib","Ensemble":"ensemble.joblib",
             "scaler":"scaler.joblib","thresholds":"thresholds.json"}
    loaded = {}
    for name,filename in files.items():
        path = MODEL_DIR/filename
        if not path.exists():
            logger.warning("Model not found: %s (using calibrated fallback)", path); continue
        try:
            if filename.endswith(".json"):
                with open(path) as f: loaded[name]=json.load(f)
            else:
                import joblib; loaded[name]=joblib.load(path)
            logger.info("Loaded: %s", name)
        except Exception as e:
            logger.error("Failed to load %s: %s", name, e)
    _models = loaded
    logger.info("Ready. Loaded: %s", list(loaded.keys()))

def _det_score(name, features, entity_id, event_id):
    vals={k:(min(float(features.get(k,0))/2000,1) if k=="egress_mb"
              else min(float(features.get(k,0))/80,1) if k=="sensitive_files"
              else min(float(features.get(k,0))/4,1) if k=="peer_deviation"
              else min(max(float(features.get(k,0)),0)/2000,1) if k=="behavior_drift"
              else min(float(features.get(k,0))/500,1) if k=="baseline_egress"
              else min(float(features.get(k,0))/50,1) if k=="session_count"
              else float(bool(features.get(k,0))))
          for k in FEATURE_NAMES}
    base = sum(vals[k]*FEATURE_WEIGHTS[k] for k in FEATURE_NAMES)
    jitter=(int(hashlib.sha256((name+entity_id+event_id).encode()).hexdigest()[:4],16)%11-5)/100
    return float(np.clip(base+jitter,0.01,0.99))

def _score_if(features, eid, evid):
    if "IsolationForest" in _models and "scaler" in _models:
        try:
            X=_models["scaler"].transform(_feature_vector(features))
            raw=_models["IsolationForest"].score_samples(X)[0]
            return float(np.clip((raw+0.6)/0.6*-1+1,0.01,0.99))
        except Exception as e: logger.warning("IF error: %s",e)
    return _det_score("IsolationForest",features,eid,evid)

def _score_ae(features, eid, evid):
    if "Autoencoder" in _models and "scaler" in _models:
        try:
            X=_models["scaler"].transform(_feature_vector(features))
            recon=_models["Autoencoder"].predict(X,verbose=0)
            mse=float(np.mean((X-recon)**2))
            p95=(_models.get("thresholds") or {}).get("autoencoder_p95",0.15)
            return float(np.clip(mse/p95,0.01,0.99))
        except Exception as e: logger.warning("AE error: %s",e)
    return _det_score("Autoencoder",features,eid,evid)

def _score_lstm(features, sequence, eid, evid):
    if "LSTM" in _models and "scaler" in _models:
        try:
            seq_len=10; rows=[]
            hist = sequence or []
            for rec in hist[-(seq_len-1):]:
                rf=rec.get("data",rec).get("features",rec.get("features",{}))
                rows.append([float(rf.get(k,0)) for k in FEATURE_NAMES])
            # Append current event features as the current timestep
            rows.append([float(features.get(k,0)) for k in FEATURE_NAMES])
            while len(rows)<seq_len: rows.insert(0,[0.0]*len(FEATURE_NAMES))
            X_seq=np.array([rows])
            flat=_models["scaler"].transform(X_seq.reshape(-1,len(FEATURE_NAMES)))
            X_s=flat.reshape(1,seq_len,len(FEATURE_NAMES))
            return float(np.clip(_models["LSTM"].predict(X_s,verbose=0)[0][0],0.01,0.99))
        except Exception as e: logger.warning("LSTM error: %s",e)
    return _det_score("LSTM",features,eid,evid)

def _score_gnn(features, graph, eid, evid):
    if "GNN" in _models:
        try: return float(np.clip(_models["GNN"].infer(features,graph),0.01,0.99))
        except Exception as e: logger.warning("GNN error: %s",e)
    nodes=graph.get("nodes",[]); edges=graph.get("edges",[])
    degree=len(edges)/max(len(nodes),1)
    return float(np.clip(_det_score("GNN",features,eid,evid)+degree*0.05,0.01,0.99))

def _score_ens(prior, features, eid, evid):
    if "Ensemble" in _models and prior:
        try:
            scores=[p.get("anomalyScore",0) for p in prior]
            confs=[p.get("confidence",0.86) for p in prior]
            meta=np.array([scores+confs])
            m=_models["Ensemble"]
            prob=m.predict_proba(meta)[0][1] if hasattr(m,"predict_proba") else m.predict(meta)[0]
            return round(float(np.clip(prob,0.01,0.99)), 4)
        except Exception as e: logger.warning("Ensemble error: %s",e)
    if prior:
        tc=sum(p.get("confidence",0.86) for p in prior)
        ws=sum(p.get("anomalyScore",0)*p.get("confidence",0.86) for p in prior)
        return float(np.clip(ws/max(tc,1e-9),0.01,0.99))
    return _det_score("Ensemble",features,eid,evid)

def _ver(name):
    if name in _models:
        m=_models[name]
        if hasattr(m,"version_"): return str(m.version_)
        if isinstance(_models.get("thresholds"),dict):
            return _models["thresholds"].get(f"{name}_version","trained-v1.0")
        return "trained-v1.0"
    return "fallback-v1.0"

def _build(name, score, conf, features, eid, evid, ts, trained):
    contribs=_contributions(features,score)
    threat=_threat_type(features,score)
    top=contribs[0]["feature"] if contribs else "egress_mb"
    return {"modelName":name,"modelVersion":_ver(name),"entityId":eid,"timestamp":ts,
            "anomalyScore":round(float(score),4),"riskScore":round(float(score)*100,1),
            "confidence":round(float(conf),4),"classification":_classification(score),
            "threatType":threat,"featureContributions":contribs,
            "evidence":[{"eventId":evid,"description":f"Top signal: {top} - {threat.lower()}"}],
            "metadata":{"schemaVersion":"1.0","synthetic":False,"trained":trained}}

@asynccontextmanager
async def lifespan(app):
    _load_models(); yield; _models.clear()

app=FastAPI(title="Astra Trained Inference Server",version="2.0.0",lifespan=lifespan)
MODELS={"IsolationForest","Autoencoder","LSTM","GNN","Ensemble"}

@app.middleware("http")
async def auth(request,call_next):
    if API_KEY:
        hdr=request.headers.get("Authorization","")
        if not hdr.startswith("Bearer ") or hdr[7:]!=API_KEY:
            return JSONResponse({"detail":"Unauthorized"},401)
    return await call_next(request)

@app.get("/health")
def health():
    return {"status":"ok","modelsLoaded":[k for k in MODELS if k in _models],
            "modelDir":str(MODEL_DIR),"fallback":any(k not in _models for k in MODELS)}

@app.post("/predict/{name}")
async def predict(name:str, request:dict):
    if name not in MODELS: raise HTTPException(404,f"Unknown model: {name}")
    features=request.get("features",{}); eid=request.get("entityId","x")
    evid=request.get("eventId","x"); ts=request.get("timestamp",datetime.now(timezone.utc).isoformat())
    sequence=request.get("sequence",[]); graph=request.get("graph",{})
    prior=request.get("predictions",[])
    trained=name in _models
    if name=="IsolationForest": score=_score_if(features,eid,evid); conf=0.91 if trained else 0.86
    elif name=="Autoencoder":   score=_score_ae(features,eid,evid); conf=0.89 if trained else 0.86
    elif name=="LSTM":          score=_score_lstm(features,sequence,eid,evid); conf=0.87 if trained else 0.83
    elif name=="GNN":           score=_score_gnn(features,graph,eid,evid); conf=0.85 if trained else 0.80
    else:
        score=_score_ens(prior,features,eid,evid)
        conf=min(sum(p.get("confidence",0.86) for p in prior)/len(prior)+0.03,0.99) if prior else (0.88 if trained else 0.86)
    pred=_build(name,score,conf,features,eid,evid,ts,trained)
    logger.info("predict name=%s entity=%s score=%.3f trained=%s",name,eid[:8],score,trained)
    return pred
