"""Seal expired evidence windows independently of UI requests. Run one replica."""
import time
from sqlalchemy import select
from .core.db import SessionLocal,set_tenant
from .domain.entities import Organization
from .services.containment import seal_due

def run_once():
    with SessionLocal() as db: tenants=list(db.scalars(select(Organization.id)))
    for tenant in tenants:
        with SessionLocal() as db:
            set_tenant(db,tenant);seal_due(db,tenant,'forensics-worker');db.commit()
if __name__=='__main__':
    while True:
        run_once();time.sleep(5)
