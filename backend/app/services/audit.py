import json,hashlib
from sqlalchemy import select,text
from ..domain.entities import Audit

def append_audit(db,tenant,actor,action,target,data=None):
    if db.bind.dialect.name=='postgresql': db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:tenant))'),{'tenant':tenant})
    previous=db.scalar(select(Audit).where(Audit.tenant_id==tenant).order_by(Audit.sequence.desc()).limit(1))
    prev=previous.hash if previous else '0'*64
    payload={'actor':actor,'action':action,'target':target,'data':data or {}}
    digest=hashlib.sha256((prev+json.dumps(payload,sort_keys=True)).encode()).hexdigest()
    row=Audit(tenant_id=tenant,actor_id=actor,action=action,target_id=target,data=data or {},previous_hash=prev,hash=digest)
    db.add(row);db.flush();return row
