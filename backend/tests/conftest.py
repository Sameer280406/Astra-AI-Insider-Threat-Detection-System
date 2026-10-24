import os, sys, tempfile
from pathlib import Path
_tests_dir = Path(__file__).resolve().parent
_backend_dir = _tests_dir.parent
_root_dir = _backend_dir.parent
sys.path.insert(0, str(_backend_dir))
sys.path.insert(0, str(_root_dir))

os.environ['JWT_SECRET']='test-secret-at-least-thirty-two-characters'
os.environ['DATABASE_URL']='sqlite:///'+tempfile.mktemp(suffix='.db')
os.environ['COOKIE_SECURE']='false'
os.environ['ENABLE_DEMO']='true'
os.environ['ENVIRONMENT']='test'
os.environ['RATE_LIMIT_PER_MINUTE']='100000'
import pytest
from fastapi.testclient import TestClient
from app.main import app,limits
from app.domain.entities import Base
from app.domain.forensics import Capsule,CapsuleEvidence,CapsuleSeal
from app.core.db import engine
@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    limits.clear()
    yield
    Base.metadata.drop_all(engine)
@pytest.fixture
def client():
    with TestClient(app) as c:
        c.headers['X-Astra-Request']='1'
        yield c
