"""Standalone deterministic remote fixture. Replace predict internals with trained inference."""
from fastapi import FastAPI,HTTPException
from .contracts import FeatureRequest,Prediction
from .gateway import MockAdapter
app=FastAPI(title='Astra remote model contract fixture',version='1.0')
MODELS={'IsolationForest','Autoencoder','LSTM','GNN','Ensemble'}
@app.post('/predict/{name}',response_model=Prediction)
async def predict(name:str,request:FeatureRequest):
    if name not in MODELS:raise HTTPException(404,'Unknown model')
    result=await MockAdapter(name).predict(request)
    result.modelVersion='remote-fixture-1.0'
    result.metadata={'synthetic':True,'fixture':True,'schemaVersion':'1.0'}
    return result
