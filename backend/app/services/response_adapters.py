"""Containment adapters simulate actions only. Models never import this module."""
from typing import Protocol
from pydantic import BaseModel,Field
class ResponseResult(BaseModel):
    status: str
    adapter: str
    simulated: bool
    operationId: str
    managementChannels: list[str]=Field(default_factory=list)
    detail: str
class EndpointAdapter(Protocol):
    def quarantine(self,endpoint:str,channels:list[str],operation_id:str)->ResponseResult: ...
    def release(self,endpoint:str,operation_id:str)->ResponseResult: ...
    def collect(self,endpoint:str,categories:list[str],operation_id:str)->ResponseResult: ...
class IdentityAdapter(Protocol):
    def restrict(self,entity_id:str,operation_id:str)->ResponseResult: ...
    def terminate_sessions(self,entity_id:str,operation_id:str)->ResponseResult: ...
class MockEndpointAdapter:
    def quarantine(self,endpoint,channels,operation_id):
        if not channels: raise ValueError('Management allowlist must not be empty')
        return ResponseResult(status='succeeded',adapter='mock-endpoint',simulated=True,operationId=operation_id,managementChannels=channels,detail='Simulated isolation from internet and internal resources; management allowlist preserved. No device was changed.')
    def release(self,endpoint,operation_id):
        return ResponseResult(status='succeeded',adapter='mock-endpoint',simulated=True,operationId=operation_id,detail='Simulated network isolation release. Identity restrictions require separate recovery.')
    def collect(self,endpoint,categories,operation_id):
        return ResponseResult(status='succeeded',adapter='mock-endpoint',simulated=True,operationId=operation_id,detail='Requested selective telemetry: '+', '.join(categories))
class MockIdentityAdapter:
    def restrict(self,entity_id,operation_id):
        return ResponseResult(status='succeeded',adapter='mock-identity',simulated=True,operationId=operation_id,detail='Simulated credential restriction; no credentials changed.')
    def terminate_sessions(self,entity_id,operation_id):
        return ResponseResult(status='succeeded',adapter='mock-identity',simulated=True,operationId=operation_id,detail='Simulated session termination; no sessions changed.')
class ResponseAdapters:
    def __init__(self): self.endpoint=MockEndpointAdapter();self.identity=MockIdentityAdapter()
response_adapters=ResponseAdapters()
