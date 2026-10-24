import hashlib,json
from datetime import timedelta,datetime,timezone
from sqlalchemy import select,text
from fastapi import HTTPException
from ..domain.entities import Record,now
from ..domain.forensics import Capsule,CapsuleEvidence,CapsuleSeal
from ..domain.repository import TenantRepository,serialize
from ..api.containment_schemas import ContainmentPolicy
from .audit import append_audit
from .response_adapters import response_adapters

def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def stamp(value): return datetime.fromisoformat(value).astimezone(timezone.utc)
def policy_for(repo):
    rows=repo.list('containment_policy',1)
    return ContainmentPolicy.model_validate(rows[0].data) if rows else ContainmentPolicy()
def evidence_append(db,capsule,row,phase):
    if db.bind.dialect.name=='postgresql': db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':capsule.id})
    if db.scalar(select(CapsuleSeal).where(CapsuleSeal.tenant_id==capsule.tenant_id,CapsuleSeal.capsule_id==capsule.id)):return
    exists=db.scalar(select(CapsuleEvidence).where(CapsuleEvidence.tenant_id==capsule.tenant_id,CapsuleEvidence.capsule_id==capsule.id,CapsuleEvidence.source_id==row.id))
    if exists:return
    snapshot=serialize(row)
    db.add(CapsuleEvidence(tenant_id=capsule.tenant_id,capsule_id=capsule.id,source_id=row.id,phase=phase,snapshot=snapshot,hash=digest(snapshot)));db.flush()
def seal_due(db,tenant,actor):
    if db.bind.dialect.name=='postgresql': db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:tenant))'),{'tenant':tenant})
    for capsule in db.scalars(select(Capsule).where(Capsule.tenant_id==tenant)):
        if now()<stamp(capsule.manifest['collectUntil']): continue
        if db.bind.dialect.name=='postgresql': db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':capsule.id})
        seal=db.scalar(select(CapsuleSeal).where(CapsuleSeal.tenant_id==tenant,CapsuleSeal.capsule_id==capsule.id))
        if seal:continue
        evidence=list(db.scalars(select(CapsuleEvidence).where(CapsuleEvidence.tenant_id==tenant,CapsuleEvidence.capsule_id==capsule.id).order_by(CapsuleEvidence.sequence)))
        manifest={'capsuleHash':capsule.hash,'evidence':[{'id':e.source_id,'hash':e.hash} for e in evidence],'sealedAt':now().isoformat(),'count':len(evidence)}
        db.add(CapsuleSeal(tenant_id=tenant,capsule_id=capsule.id,manifest=manifest,hash=digest(manifest)))
        append_audit(db,tenant,actor,'evidence.capsule_sealed',capsule.id,{'count':len(evidence)})
def attach_post_event(db,tenant,event):
    for capsule in db.scalars(select(Capsule).where(Capsule.tenant_id==tenant)):
        m=capsule.manifest
        if now()>stamp(m['collectUntil']):continue
        ts=stamp(event.data['timestamp'])
        related=event.entity_id==capsule.entity_id or event.data.get('device')==capsule.device
        if related and stamp(m['triggerTime'])<=ts<=stamp(m['windowEnd']) and event.data.get('category','network') in m['categories']:
            evidence_append(db,capsule,event,'post_event')
def record_action(db,user,incident,action,result,reason):
    repo=TenantRepository(db,user.tenant_id)
    record=repo.add('response_actions',{'incidentId':incident.id,'action':action,'result':result,'reason':reason,'actorId':user.id,'timestamp':now().isoformat()},incident.entity_id)
    append_audit(db,user.tenant_id,user.id,'containment.'+action,incident.id,{'actionId':record.id,'result':result})
    return record
def execute(db,user,incident,reason):
    repo=TenantRepository(db,user.tenant_id)
    if incident.data['state']=='quarantined':return incident
    if incident.data['state']=='released':raise HTTPException(409,'Released incidents cannot be quarantined again; create a new response incident')
    p=ContainmentPolicy.model_validate(incident.data['policySnapshot'])
    endpoint=incident.data['device']
    operation=incident.id+':quarantine'
    result=response_adapters.endpoint.quarantine(endpoint,p.managementChannels,operation).model_dump()
    record_action(db,user,incident,'quarantine',result,reason)
    if result['status']!='succeeded':
        repo.update(incident,{'state':'failed'});return incident
    repo.update(incident,{'state':'quarantined','quarantinedAt':now().isoformat(),'simulated':result['simulated']})
    for device in repo.list('devices'):
        if device.data['name']==endpoint:repo.update(device,{'quarantineState':'quarantined','managementChannels':p.managementChannels,'simulated':True})
    if p.terminateSessions:record_action(db,user,incident,'terminate_sessions',response_adapters.identity.terminate_sessions(incident.entity_id,incident.id+':terminate').model_dump(),reason)
    if p.restrictCredentials:record_action(db,user,incident,'restrict_credentials',response_adapters.identity.restrict(incident.entity_id,incident.id+':restrict').model_dump(),reason)
    repo.add('notifications',{'title':'P1 containment executed (simulated)','body':endpoint,'incidentId':incident.id,'read':False})
    return incident

def evaluate(db,user,event,predictions,fused,matches,velocity):
    repo=TenantRepository(db,user.tenant_id);p=policy_for(repo)
    seal_due(db,user.tenant_id,user.id);attach_post_event(db,user.tenant_id,event)
    level='critical' if fused.riskScore>=p.criticalThreshold else 'elevated' if fused.riskScore>=p.elevatedThreshold else 'baseline'
    entity=repo.get('entities',event.entity_id)
    if entity.data.get('collectionLevel')!=level:
        repo.update(entity,{'collectionLevel':level})
        if level!='baseline':
            result=response_adapters.endpoint.collect(event.data['device'],p.categories if level=='critical' else ['authentication','file','network'],event.id+':collect').model_dump()
            append_audit(db,user.tenant_id,user.id,'telemetry.collection_escalated',event.id,{'level':level,'result':result})
    if fused.riskScore<p.criticalThreshold:return None
    # Serialize same-endpoint response decisions in PostgreSQL.
    if db.bind.dialect.name=='postgresql':db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':user.tenant_id+event.data['device']})
    active=next((i for i in repo.list('incidents',1000) if i.data['device']==event.data['device'] and i.data['state']!='released'),None)
    if active:
        repo.update(active,{'riskEvolution':[*active.data['riskEvolution'],{'eventId':event.id,'timestamp':event.data['timestamp'],'riskScore':fused.riskScore}][-500:]})
        return active
    consensus=sum(x.riskScore>=p.criticalThreshold for x in predictions)
    # A riskScore/riskVelocity rule is circular evidence; require a raw-feature rule.
    deterministic=[r['name'] for r in matches if r['feature'] not in {'riskScore','riskVelocity'}]
    gates={'threshold':fused.riskScore>=p.criticalThreshold,'confidence':fused.confidence>=p.minimumConfidence,'consensus':consensus>=p.minimumConsensus,'ruleCorroboration':bool(deterministic) or not p.requireRuleCorroboration}
    state='advisory' if p.mode=='advisory' else 'pending_approval'
    incident=repo.add('incidents',{'title':'Critical insider-risk response','priority':'P1','state':state,'device':event.data['device'],'entityName':entity.data['name'],'riskScore':fused.riskScore,'confidence':fused.confidence,'consensus':consensus,'triggerEventId':event.id,'policySnapshot':p.model_dump(),'gates':gates,'corroboratingRules':deterministic,'riskEvolution':[{'eventId':event.id,'timestamp':event.data['timestamp'],'riskScore':fused.riskScore}],'collectionLevel':'critical','simulated':True},entity.id)
    trigger=stamp(event.data['timestamp']);end=trigger+timedelta(minutes=p.postWindowMinutes)
    manifest={'incidentId':incident.id,'triggerEventId':event.id,'triggerTime':trigger.isoformat(),'windowStart':(trigger-timedelta(minutes=p.preWindowMinutes)).isoformat(),'windowEnd':end.isoformat(),'collectUntil':max(end,now()+timedelta(minutes=p.postWindowMinutes)).isoformat(),'categories':p.categories,'policySnapshot':p.model_dump(),'predictions':[x.model_dump(mode='json') for x in [*predictions,fused]],'synthetic':event.data.get('synthetic',False)}
    capsule=Capsule(tenant_id=user.tenant_id,incident_id=incident.id,entity_id=entity.id,device=event.data['device'],manifest=manifest,hash=digest(manifest));db.add(capsule);db.flush()
    # Filter relevance by event time, entity/endpoint, and policy category. No machine-wide log capture.
    for row in db.scalars(select(Record).where(Record.tenant_id==user.tenant_id,Record.kind=='events')):
        if row.entity_id!=entity.id and row.data.get('device')!=capsule.device:continue
        ts=stamp(row.data['timestamp'])
        if stamp(manifest['windowStart'])<=ts<=trigger and row.data.get('category','network') in p.categories:evidence_append(db,capsule,row,'trigger' if row.id==event.id else 'pre_event')
    repo.update(incident,{'capsuleId':capsule.id})
    append_audit(db,user.tenant_id,user.id,'evidence.capsule_created',capsule.id,{'incidentId':incident.id,'manifestHash':capsule.hash})
    append_audit(db,user.tenant_id,user.id,'containment.policy_evaluated',incident.id,{'mode':p.mode,'gates':gates})
    repo.add('notifications',{'title':'P1 critical incident','body':entity.data['name']+' · '+state,'incidentId':incident.id,'read':False})
    if p.mode=='automatic_critical' and all(gates.values()):execute(db,user,incident,'Automatic policy: all required gates satisfied')
    return incident
