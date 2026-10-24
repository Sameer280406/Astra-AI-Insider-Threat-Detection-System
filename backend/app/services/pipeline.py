import asyncio
from sqlalchemy import select,func
from fastapi import HTTPException
from ..domain.entities import Record,Organization,now
from ..domain.repository import TenantRepository,serialize
from ..models.contracts import FeatureRequest
from ..models.gateway import registry
from ..core.config import settings
from .features import FeaturePipeline
from .audit import append_audit
from .entitlements import TIERS
from .policy_rules import alert_severity,evaluate_rules
async def ingest(db,user,event,features_override=None):
    repo=TenantRepository(db,user.tenant_id)
    old=db.scalar(select(Record).where(Record.tenant_id==user.tenant_id,Record.kind=='events',Record.external_id==event.eventId))
    if old: return {'event':serialize(old),'duplicate':True}
    org=db.scalar(select(Organization).where(Organization.id==user.tenant_id).with_for_update())
    month=now().replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    count=db.scalar(select(func.count()).select_from(Record).where(Record.tenant_id==user.tenant_id,Record.kind=='events',Record.created_at>=month))
    if count>=TIERS[org.tier]['events']: raise HTTPException(402,'Monthly event allowance reached')
    entity=repo.get('entities',event.entityId)
    history=repo.list('events',100,event.entityId)
    peers=repo.list('events',200)
    department=entity.data.get('department')
    peer_ids={x.id for x in repo.list('entities') if x.data.get('department')==department and x.id!=entity.id}
    features=features_override or FeaturePipeline().build(event,history,[x for x in peers if x.entity_id in peer_ids])
    request=FeatureRequest(tenantId=user.tenant_id,entityId=entity.id,timestamp=event.timestamp,eventId=event.eventId,features=features,sequence=[serialize(x) for x in reversed(history)],graph={'nodes':[entity.id,event.device,event.resource],'edges':[[entity.id,event.device],[event.device,event.resource]]})
    names=settings().models
    predictions=await asyncio.gather(*(registry.resolve(n).predict(request) for n in names if n!='Ensemble'))
    fused=await registry.resolve('Ensemble').predict(request.model_copy(update={'predictions':predictions}))
    score=fused.riskScore
    velocity=round(score-entity.data.get('riskScore',0),1)
    stale=bool(entity.data.get('lastSeen') and event.timestamp.timestamp()<__import__('datetime').datetime.fromisoformat(entity.data['lastSeen']).timestamp())
    evaluated_values={**features,'riskScore':score,'riskVelocity':velocity}
    matches=evaluate_rules(repo.list('rules'),evaluated_values)
    from .explainability_service import explainability_service
    xai_data=explainability_service.include_rule_matches(
        fused.metadata.get('xai'),matches,features,score,entity.data['name']
    )
    fused.metadata={**fused.metadata,'xai':xai_data}
    row=repo.add('events',{**event.model_dump(mode='json'),'riskScore':score,'synthetic':settings().model_mode=='mock','lateArrival':stale,'xai':xai_data},entity.id,event.eventId)
    repo.add('features',request.model_dump(mode='json'),entity.id)
    for p in [*predictions,fused]: repo.add('predictions',p.model_dump(mode='json'),entity.id)
    if not stale: repo.update(entity,{'riskScore':score,'riskVelocity':velocity,'lastSeen':event.timestamp.isoformat(),'classification':fused.classification,'threatType':fused.threatType,'features':features,'predictions':[p.model_dump(mode='json') for p in [*predictions,fused]],'xai':xai_data})
    alert=None
    if score>=settings().alert_threshold or velocity>=settings().escalation_velocity or matches:
        severity=alert_severity(score,matches)
        model_explanation=fused.evidence[0].description if fused.evidence else (fused.featureContributions[0].feature if fused.featureContributions else 'Model risk threshold')
        alert_explanation=(xai_data.get('rule_summary')+' '+model_explanation) if matches else model_explanation
        alert=repo.add('alerts',{'title':fused.threatType,'entityName':entity.data['name'],'riskScore':score,'severity':severity,'status':'open','eventId':row.id,'explanation':alert_explanation,'modelConsensus':sum(p.riskScore>=settings().alert_threshold for p in predictions),'matchedRules':[x['name'] for x in matches],'ruleMatches':matches,'riskVelocity':velocity,'synthetic':settings().model_mode=='mock','xai':xai_data},entity.id)
        repo.add('notifications',{'title':fused.threatType,'body':entity.data['name'],'alertId':alert.id,'read':False})
    from .containment import evaluate
    incident=evaluate(db,user,row,predictions,fused,matches,velocity)
    append_audit(db,user.tenant_id,user.id,'event.scored',row.id,{'entityId':entity.id,'riskScore':score,'alertId':alert.id if alert else None})
    return {'event':serialize(row),'prediction':fused.model_dump(mode='json'),'alert':serialize(alert) if alert else None,'duplicate':False,'incidentId':incident.id if incident else None}
