from typing import Literal
from pydantic import Field,model_validator
from .schemas import Strict
CATEGORIES=['authentication','process','command','file','usb','network','privilege','model_explanation']
class ContainmentPolicy(Strict):
    name: str=Field(default='Critical insider-risk response',min_length=3,max_length=100)
    mode: Literal['advisory','analyst_approval','automatic_critical']='analyst_approval'
    criticalThreshold: float=Field(default=85,ge=65,le=100)
    minimumConfidence: float=Field(default=.8,ge=0,le=1)
    minimumConsensus: int=Field(default=3,ge=1,le=4)
    requireRuleCorroboration: bool=True
    preWindowMinutes: int=Field(default=60,ge=1,le=1440)
    postWindowMinutes: int=Field(default=30,ge=1,le=1440)
    categories: list[Literal['authentication','process','command','file','usb','network','privilege','model_explanation']]=Field(default_factory=lambda:CATEGORIES.copy(),min_length=1)
    managementChannels: list[str]=Field(default_factory=lambda:['astra-management','soc-console','edr-control'],min_length=1,max_length=20)
    terminateSessions: bool=False
    restrictCredentials: bool=False
    elevatedThreshold: float=Field(default=65,ge=0,le=100)
    @model_validator(mode='after')
    def valid(self):
        if self.elevatedThreshold>self.criticalThreshold: raise ValueError('Elevated threshold must not exceed critical threshold')
        if any(not c.strip() or c in {'*','0.0.0.0/0','::/0'} for c in self.managementChannels): raise ValueError('Specific management channel identifiers required')
        return self
class ResponseControl(Strict):
    reason: str=Field(min_length=5,max_length=1000)
