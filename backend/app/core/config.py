from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'sqlite:///./astra.db'
    jwt_secret: str
    environment: Literal['development','production','test'] = 'development'
    model_mode: Literal['mock','remote','trained'] = 'trained'
    model_endpoints: dict[str,str] = {}
    model_api_key: str = ''
    model_timeout_seconds: float = 10
    kafka_bootstrap_servers: str = 'localhost:9092'
    kafka_scoring_enabled: bool = False
    internal_ingest_token: str = ''
    cookie_secure: bool = False
    allowed_origins: list[str] = ['http://localhost:3000']
    session_minutes: int = 1440
    redis_url: str = ''
    rate_limit_per_minute: int = 120
    demo_password: str = ''
    enable_demo: bool = False
    alert_threshold: float = 65
    escalation_velocity: float = 20
    @property
    def models(self): return ['IsolationForest','Autoencoder','LSTM','GNN','Ensemble']
@lru_cache
def settings():
    s=Settings()
    if len(s.jwt_secret)<32: raise ValueError('JWT_SECRET must be at least 32 characters')
    if s.kafka_scoring_enabled and len(s.internal_ingest_token)<32:
        raise ValueError('KAFKA_SCORING_ENABLED requires an INTERNAL_INGEST_TOKEN of at least 32 characters')
    if s.environment=='production' and (not s.cookie_secure or not s.redis_url or s.database_url.startswith('sqlite')):
        raise ValueError('Production requires secure cookies, Redis and PostgreSQL')
    return s
