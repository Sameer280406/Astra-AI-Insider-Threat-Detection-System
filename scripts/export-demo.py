# Export only synthetic fixtures from a local seeded database. Never run against production.
import json,sys,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'backend'))
os.chdir(Path(__file__).resolve().parent.parent/'backend')
from sqlalchemy import select
from app.core.config import settings
assert settings().enable_demo and settings().environment!='production'
from app.core.db import SessionLocal
from app.domain.entities import Record,Audit,User
from app.domain.forensics import Capsule,CapsuleEvidence,CapsuleSeal
from app.domain.repository import serialize
from app.api.containment_schemas import ContainmentPolicy
from app.main import app
with SessionLocal() as db:
 data={k:[] for k in ['entities','events','alerts','cases','rules','integrations','notifications','feedback','predictions','features','devices','resources','audit','users','incidents','response_actions','capsules','containment_policy']}
 for row in db.scalars(select(Record).order_by(Record.created_at.desc())):data.setdefault(row.kind,[]).append(serialize(row))
 data['audit']=[{'id':str(a.sequence),'action':a.action,'actor':'Alex Morgan','target':a.target_id,'hash':a.hash,'previousHash':a.previous_hash,'createdAt':a.created_at.isoformat()} for a in db.scalars(select(Audit).order_by(Audit.sequence.desc()))]
 data['users']=[{'id':u.id,'name':u.name,'email':u.email,'role':u.role} for u in db.scalars(select(User))]
 for c in data['cases']:c['assignee']='demo-admin'
 next(u for u in data['users'] if u['role']=='org_admin')['id']='demo-admin'
 for c in db.scalars(select(Capsule)):
  es=list(db.scalars(select(CapsuleEvidence).where(CapsuleEvidence.capsule_id==c.id).order_by(CapsuleEvidence.sequence)))
  data['capsules'].append({'id':c.id,'manifest':c.manifest,'manifestHash':c.hash,'integrityVerified':True,'status':'collecting','evidence':[{'sourceId':e.source_id,'phase':e.phase,'snapshot':e.snapshot,'hash':e.hash} for e in es],'seal':None})
 if not data['containment_policy']:data['containment_policy']=[{'id':'demo-policy',**ContainmentPolicy().model_dump()}]
 # Raw feature store and 420 predictions are available in the backend; UI needs current entity predictions only.
 data['features']=[];data['predictions']=[]
 Path('../lib/demo-seed.json').write_text(json.dumps(data,separators=(',',':')))
 Path('../docs/openapi.json').write_text(json.dumps(app.openapi(),indent=2))
 print({k:len(v) for k,v in data.items()})
