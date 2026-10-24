from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy import select
from ..core.db import get_db
from ..core.security import require
from ..domain.entities import now
from ..domain.forensics import Capsule,CapsuleEvidence,CapsuleSeal
from ..domain.repository import TenantRepository,serialize
from ..services.audit import append_audit
from ..services.containment import policy_for,execute,record_action,seal_due,digest
from ..services.response_adapters import response_adapters
from .containment_schemas import ContainmentPolicy,ResponseControl
router=APIRouter(prefix='/api/v1/response',tags=['Containment & Forensics'])
@router.get('/policy')
def get_policy(user=Depends(require('read')),db=Depends(get_db)):return policy_for(TenantRepository(db,user.tenant_id)).model_dump()
@router.put('/policy')
def save_policy(body:ContainmentPolicy,user=Depends(require('policy')),db=Depends(get_db)):
    repo=TenantRepository(db,user.tenant_id);current=repo.list('containment_policy',1)
    row=repo.update(current[0],body.model_dump()) if current else repo.add('containment_policy',body.model_dump())
    append_audit(db,user.tenant_id,user.id,'containment.policy_changed',row.id,body.model_dump());return body
@router.get('/incidents')
def incidents(user=Depends(require('read')),db=Depends(get_db)):
    seal_due(db,user.tenant_id,user.id)
    return [serialize(x) for x in TenantRepository(db,user.tenant_id).list('incidents',1000)]
@router.get('/incidents/{id}')
def incident(id:str,user=Depends(require('read')),db=Depends(get_db)):
    repo=TenantRepository(db,user.tenant_id);row=repo.get('incidents',id)
    return {**serialize(row),'actions':[serialize(a) for a in repo.list('response_actions',1000) if a.data['incidentId']==id]}
@router.post('/incidents/{id}/{action}')
def control(id:str,action:str,body:ResponseControl,user=Depends(require('contain')),db=Depends(get_db)):
    repo=TenantRepository(db,user.tenant_id);row=repo.get('incidents',id);db.refresh(row,with_for_update=True)
    if action=='approve':
        if row.data['state'] not in {'advisory','pending_approval','quarantined'}:raise HTTPException(409,'Incident cannot be approved in its current state')
        row=execute(db,user,row,'Analyst approved: '+body.reason)
    elif action=='release':
        if row.data['state']=='released':return serialize(row)
        if row.data['state']!='quarantined':raise HTTPException(409,'Endpoint is not quarantined')
        result=response_adapters.endpoint.release(row.data['device'],id+':release').model_dump()
        record_action(db,user,row,'release',result,body.reason)
        if result['status']=='succeeded':
            repo.update(row,{'state':'released','releasedAt':now().isoformat()})
            for device in repo.list('devices'):
                if device.data['name']==row.data['device']:repo.update(device,{'quarantineState':'released','simulated':True})
    elif action=='escalate':
        if row.data.get('escalated'):return serialize(row)
        repo.update(row,{'escalated':True,'escalationReason':body.reason})
        record_action(db,user,row,'escalate',{'status':'succeeded','simulated':True,'detail':'Escalated to SOC management queue'},body.reason)
    else:raise HTTPException(404,'Unknown response action')
    repo.add('notifications',{'title':'Incident '+action,'body':row.data['device'],'incidentId':id,'read':False})
    return serialize(row)
@router.get('/capsules/{id}')
def capsule(id:str,user=Depends(require('read')),db=Depends(get_db)):
    row=db.scalar(select(Capsule).where(Capsule.id==id,Capsule.tenant_id==user.tenant_id))
    if not row:raise HTTPException(404,'Capsule not found')
    seal_due(db,user.tenant_id,user.id)
    entries=list(db.scalars(select(CapsuleEvidence).where(CapsuleEvidence.capsule_id==id,CapsuleEvidence.tenant_id==user.tenant_id).order_by(CapsuleEvidence.sequence)))
    seal=db.scalar(select(CapsuleSeal).where(CapsuleSeal.capsule_id==id,CapsuleSeal.tenant_id==user.tenant_id))
    valid=row.hash==digest(row.manifest) and all(e.hash==digest(e.snapshot) for e in entries)
    if seal:valid=valid and seal.hash==digest(seal.manifest) and seal.manifest['evidence']==[{'id':e.source_id,'hash':e.hash} for e in entries]
    return {'id':row.id,'manifest':row.manifest,'manifestHash':row.hash,'integrityVerified':valid,'status':'sealed' if seal else 'collecting','evidence':[{'sourceId':e.source_id,'phase':e.phase,'snapshot':e.snapshot,'hash':e.hash} for e in entries],'seal':{'manifest':seal.manifest,'hash':seal.hash} if seal else None}
