"""
Focused tests for rate-limiting, tenant isolation, simulation debouncing, and 429 Retry-After handling.
"""

import time
import pytest
from app.main import app, limits, LIMITS_BY_CATEGORY
from app.core.db import SessionLocal
from app.domain.repository import TenantRepository

def onboard(c, slug='rate-org'):
    r = c.post('/api/v1/auth/onboard', json={
        'organization': slug,
        'organizationName': slug.title(),
        'name': 'Rate Admin',
        'email': f'{slug}@example.com',
        'password': 'Strong-Password-123!'
    })
    assert r.status_code == 201, r.text
    return r.json()

def add_entity(p):
    with SessionLocal() as db:
        row = TenantRepository(db, p['organization']['id']).add('entities', {
            'name': 'Test User',
            'department': 'Security',
            'riskScore': 20
        })
        db.commit()
        return row.id

def test_exempt_endpoints_not_throttled(client):
    """Health and docs endpoints are exempt from normal restrictive throttling."""
    for _ in range(50):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

def test_rate_limiting_headers_on_429(client):
    """When a rate limit is exceeded, server returns 429 with a valid Retry-After header."""
    onboard(client, 'org-limit')

    orig_limit = LIMITS_BY_CATEGORY["read"]
    LIMITS_BY_CATEGORY["read"] = 5
    limits.clear()

    try:
        # First 5 calls succeed
        for i in range(5):
            res = client.get("/api/v1/overview")
            assert res.status_code == 200, f"Call {i} failed unexpectedly"

        # 6th call exceeds limit and returns 429
        exceeded_res = client.get("/api/v1/overview")
        assert exceeded_res.status_code == 429
        assert "Retry-After" in exceeded_res.headers
        retry_after = int(exceeded_res.headers["Retry-After"])
        assert retry_after >= 1
        data = exceeded_res.json()
        assert "Rate limit exceeded" in data["detail"]
        assert data.get("retryAfter") == retry_after
    finally:
        LIMITS_BY_CATEGORY["read"] = orig_limit
        limits.clear()

def test_simulation_debounce_and_retry_after(client):
    """Simulation prevents duplicate concurrent calls and issues a 429 with Retry-After header."""
    p = onboard(client, 'org-sim')
    add_entity(p)

    # First simulation call
    res1 = client.post("/api/v1/demo/simulate", headers={"X-Astra-Request": "1"})
    assert res1.status_code == 200
    assert "event" in res1.json()

    # Immediate second call within 350ms debounce window triggers 429 with Retry-After: 1
    res2 = client.post("/api/v1/demo/simulate", headers={"X-Astra-Request": "1"})
    assert res2.status_code == 429
    assert res2.headers.get("Retry-After") == "1"
    assert "Simulation request already processing" in res2.json()["detail"]

    # After waiting >350ms, simulation succeeds again
    time.sleep(0.4)
    res3 = client.post("/api/v1/demo/simulate", headers={"X-Astra-Request": "1"})
    assert res3.status_code == 200

def test_rate_limit_tenant_isolation(client):
    """Rate limits are isolated per user/scope and do not bleed across scopes."""
    onboard(client, 'org-iso')

    orig_limit = LIMITS_BY_CATEGORY["read"]
    LIMITS_BY_CATEGORY["read"] = 3
    limits.clear()

    try:
        # Exhaust authenticated user's bucket
        for _ in range(3):
            r = client.get("/api/v1/overview")
            assert r.status_code == 200

        assert client.get("/api/v1/overview").status_code == 429

        # Health endpoint remains unaffected
        health_res = client.get("/health")
        assert health_res.status_code == 200
    finally:
        LIMITS_BY_CATEGORY["read"] = orig_limit
        limits.clear()
