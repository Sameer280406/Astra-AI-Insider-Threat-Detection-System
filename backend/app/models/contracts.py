from typing import Literal, Protocol
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
class Contribution(BaseModel):
    feature: str
    value: float
    contribution: float=Field(ge=0,le=1)
    direction: Literal['increases','decreases']='increases'
class Evidence(BaseModel):
    eventId: str
    description: str
class Prediction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    modelName: str
    modelVersion: str
    entityId: str
    timestamp: datetime
    anomalyScore: float=Field(ge=0,le=1)
    riskScore: float=Field(ge=0,le=100)
    confidence: float=Field(ge=0,le=1)
    classification: Literal['normal','suspicious','high_risk']
    threatType: str
    featureContributions: list[Contribution]
    evidence: list[Evidence]
    metadata: dict=Field(default_factory=dict)
class FeatureRequest(BaseModel):
    schemaVersion: str='1.0'
    tenantId: str
    entityId: str
    timestamp: datetime
    eventId: str
    features: dict[str,float]
    sequence: list[dict]=Field(default_factory=list)
    graph: dict=Field(default_factory=dict)
    predictions: list[Prediction]=Field(default_factory=list)
class ModelGateway(Protocol):
    async def predict(self,request:FeatureRequest)->Prediction: ...
