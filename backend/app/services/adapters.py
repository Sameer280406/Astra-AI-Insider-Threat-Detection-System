"""External-system ports. Kafka/Spark deployment is supplied by the operator."""
from typing import Protocol
from ..api.schemas import EventInput
class EventQueue(Protocol):
    async def publish(self,tenant_id:str,event:EventInput)->None: ...
class EventConnector(Protocol):
    name:str
    def normalize(self,payload:dict)->EventInput: ...
class HttpConnector:
    name='http'
    def normalize(self,payload): return EventInput.model_validate(payload)
class ConnectorRegistry:
    def __init__(self): self.adapters={'http':HttpConnector()}
    def register(self,adapter:EventConnector): self.adapters[adapter.name]=adapter
    def resolve(self,name): return self.adapters[name]
connectors=ConnectorRegistry()
