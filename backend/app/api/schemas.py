from pydantic import BaseModel, Field, field_validator, ConfigDict
from datetime import datetime,timezone
from typing import Literal
import math
class Strict(BaseModel): model_config=ConfigDict(extra='forbid')
class Login(Strict):
    organization: str=Field(min_length=2,max_length=80)
    email: str=Field(min_length=3,max_length=254)
    password: str=Field(min_length=1,max_length=128)
class Onboarding(Login):
    name: str=Field(min_length=2,max_length=100)
    organizationName: str=Field(min_length=2,max_length=100)
    @field_validator('password')
    @classmethod
    def strong(cls,v):
        if len(v)<12: raise ValueError('Use at least 12 characters')
        return v
    @field_validator('organization')
    @classmethod
    def slug(cls,v):
        import re
        if not re.fullmatch(r'[a-z][a-z0-9-]{1,79}',v): raise ValueError('Use a lowercase organization slug')
        return v
class EventInput(Strict):
    eventId: str=Field(min_length=1,max_length=100)
    entityId: str
    timestamp: datetime
    action: str=Field(min_length=1,max_length=100)
    resource: str=Field(min_length=1,max_length=200)
    device: str=Field(min_length=1,max_length=100)
    egressMb: float=Field(default=0,ge=0,le=1000000)
    sensitiveFiles: int=Field(default=0,ge=0,le=1000000)
    newDevice: bool=False
    category: Literal['authentication','process','command','file','usb','network','privilege','model_explanation']='network'
    details: dict[str,str|int|float|bool]=Field(default_factory=dict,max_length=30)
    @field_validator('timestamp')
    @classmethod
    def tz(cls,v):
        if v.tzinfo is None: raise ValueError('Timezone required')
        if v.timestamp()>datetime.now(timezone.utc).timestamp()+300: raise ValueError('Future event rejected')
        return v
class StreamFeatures(Strict):
    egress_mb: float=Field(ge=0,le=1000000)
    sensitive_files: float=Field(ge=0,le=1000000)
    off_hours: float=Field(ge=0,le=1)
    new_device: float=Field(ge=0,le=1)
    peer_deviation: float=Field(ge=0,le=1000000)
    baseline_egress: float=Field(ge=0,le=1000000)
    behavior_drift: float=Field(ge=0,le=1000000)
    session_count: float=Field(ge=0,le=1000000)
    @field_validator('*')
    @classmethod
    def finite_stream_feature(cls,v):
        if not math.isfinite(v):
            raise ValueError('Feature values must be finite')
        return v
class StreamEnvelope(Strict):
    schemaVersion: Literal['1.0']
    tenantId: str=Field(min_length=1,max_length=100)
    eventId: str=Field(min_length=1,max_length=100)
    entityId: str=Field(min_length=1,max_length=100)
    timestamp: datetime
    eventType: str=Field(min_length=1,max_length=100)
    action: str=Field(min_length=1,max_length=100)
    resource: str=Field(max_length=200)
    device: str=Field(min_length=1,max_length=100)
    bytes: int=Field(ge=0,le=9223372036854775807)
    bytesKnown: bool
    provenance: str=Field(min_length=1,max_length=100)
    isSynthetic: bool=False
    features: StreamFeatures
    @field_validator('timestamp')
    @classmethod
    def stream_timestamp_has_timezone(cls,v):
        if v.tzinfo is None:
            raise ValueError('Timezone required')
        return v
class CaseCreate(Strict):
    title: str=Field(min_length=3,max_length=200)
    alertId: str
    assignee: str|None=None
class CasePatch(Strict):
    status: Literal['open','investigating','contained','closed']|None=None
    assignee: str|None=None
class Note(Strict): text: str=Field(min_length=1,max_length=5000)
class Bookmark(Strict): eventId: str
class Feedback(Strict):
    alertId: str
    verdict: Literal['true_positive','false_positive','needs_review']
    reason: str=Field(min_length=3,max_length=2000)
class Rule(Strict):
    name: str=Field(min_length=3,max_length=120)
    feature: Literal['egress_mb','sensitive_files','off_hours','new_device','peer_deviation','riskScore','riskVelocity']
    threshold: float=Field(ge=0,le=1000000)
    enabled: bool=True
    severity: Literal['medium','high','critical']='high'
    @field_validator('threshold')
    @classmethod
    def finite_threshold(cls,v):
        if not math.isfinite(v):
            raise ValueError('Rule threshold must be finite')
        return v
class UserCreate(Strict):
    email: str=Field(min_length=3,max_length=254)
    name: str=Field(min_length=2,max_length=100)
    password: str=Field(min_length=12,max_length=128)
    role: Literal['org_admin','soc_manager','soc_analyst','auditor','viewer']
class Integration(Strict):
    name: str=Field(min_length=2,max_length=100)
    kind: Literal['http','kafka','webhook','siem','soar']
    endpoint: str=Field(default='',max_length=500)
class TierUpdate(Strict): tier: Literal['starter','team','enterprise']
