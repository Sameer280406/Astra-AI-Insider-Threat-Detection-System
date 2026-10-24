import asyncio,json
from datetime import datetime,timezone
from fastapi import APIRouter,Depends,HTTPException,Response,Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select,func,delete
from ..core.db import get_db,SessionLocal,set_tenant
from ..core.security import principal,require,ph,verify,new_session,ROLES
from ..core.config import settings
from ..domain.entities import Organization,User,Session,Record,Audit,now
from ..domain.repository import TenantRepository,serialize
from ..services.audit import append_audit
from ..services.pipeline import ingest
from ..services.entitlements import TIERS
from .schemas import *
router=APIRouter(prefix='/api/v1')

def me(user,db):
    org=db.get(Organization,user.tenant_id)
    return {'id':user.id,'name':user.name,'email':user.email,'role':user.role,'organization':{'id':org.id,'name':org.name,'slug':org.slug,'tier':org.tier},'features':TIERS[org.tier]}
def session_response(response,db,user):
    token = new_session(db,user)
    response.set_cookie('astra_session',token,httponly=True,secure=settings().cookie_secure,samesite='lax',max_age=settings().session_minutes*60,path='/')
    res = me(user,db)
    res['token'] = token
    return res
@router.post('/auth/login')
def login(body:Login,response:Response,db=Depends(get_db)):
    org=db.scalar(select(Organization).where(Organization.slug==body.organization))
    user=db.scalar(select(User).where(User.tenant_id==org.id,User.email==body.email.lower())) if org else None
    if not user:
        verify(body.password,ph.hash('dummy-password-for-timing'));raise HTTPException(401,'Invalid credentials')
    if not verify(body.password,user.password_hash): raise HTTPException(401,'Invalid credentials')
    set_tenant(db,user.tenant_id)
    append_audit(db,user.tenant_id,user.id,'auth.login',user.id)
    return session_response(response,db,user)
@router.post('/auth/onboard',status_code=201)
def onboard(body:Onboarding,response:Response,db=Depends(get_db)):
    if db.scalar(select(Organization).where(Organization.slug==body.organization)): raise HTTPException(409,'Organization slug unavailable')
    org=Organization(slug=body.organization,name=body.organizationName,tier='starter');db.add(org);db.flush();set_tenant(db,org.id)
    user=User(tenant_id=org.id,email=body.email.lower(),name=body.name,password_hash=ph.hash(body.password),role='org_admin');db.add(user);db.flush()
    append_audit(db,org.id,user.id,'organization.created',org.id)
    return session_response(response,db,user)
@router.get('/auth/me')
def current(user=Depends(principal),db=Depends(get_db)): return me(user,db)
@router.post('/auth/logout')
def logout(request:Request,response:Response,user=Depends(principal),db=Depends(get_db)):
    import jwt
    token=request.cookies.get('astra_session') or request.headers.get('authorization','')[7:]
    claims=jwt.decode(token,settings().jwt_secret,algorithms=['HS256'],audience='astra',issuer='astra')
    db.execute(delete(Session).where(Session.id==claims['jti'],Session.tenant_id==user.tenant_id))
    response.delete_cookie('astra_session');return {'ok':True}
@router.get('/auth/providers')
def providers(): return {'providers':[],'message':'OIDC provider not configured. Use organization credentials.'}

@router.get('/overview')
def overview(user=Depends(require('read')),db=Depends(get_db)):
    repo=TenantRepository(db,user.tenant_id);entities=repo.list('entities',2000);alerts=repo.list('alerts',2000)
    return {'entities':len(entities),'openAlerts':sum(a.data['status']!='closed' for a in alerts),'criticalAlerts':sum(a.data['severity']=='critical' and a.data['status']!='closed' for a in alerts),'averageRisk':round(sum(e.data.get('riskScore',0) for e in entities)/max(1,len(entities)),1),'events':db.scalar(select(func.count()).select_from(Record).where(Record.tenant_id==user.tenant_id,Record.kind=='events')),'modelMode':settings().model_mode}
@router.post('/events',status_code=201)
async def event(body:EventInput,user=Depends(require('ingest')),db=Depends(get_db)): return await ingest(db,user,body)

@router.post('/explain')
def explain_features(body: dict, user=Depends(require('read'))):
    from ..services.explainability_service import explainability_service
    features = body.get('features', {})
    score = float(body.get('riskScore', 50.0))
    model_scores = body.get('modelScores')
    entity_name = body.get('entityName')
    return explainability_service.explain(features, score, model_scores, entity_name)

@router.get('/explain/event/{event_id}')
def explain_event(event_id: str, user=Depends(require('read')), db=Depends(get_db)):
    from ..services.explainability_service import explainability_service
    repo = TenantRepository(db, user.tenant_id)
    ev = repo.get('events', event_id)
    if ev.data.get('xai'):
        return ev.data['xai']
    features = ev.data.get('features')
    if not features:
        features = {
            'egress_mb': float(ev.data.get('egressMb', 0.0)),
            'sensitive_files': float(ev.data.get('sensitiveFiles', 0.0)),
            'off_hours': 1.0 if ev.data.get('offHours') else 0.0,
            'new_device': 1.0 if ev.data.get('newDevice') else 0.0,
            'peer_deviation': float(ev.data.get('peerDeviation', 0.0)),
            'baseline_egress': float(ev.data.get('baselineEgress', 0.0)),
            'behavior_drift': float(ev.data.get('behaviorDrift', 0.0)),
            'session_count': float(ev.data.get('sessionCount', 1.0))
        }
    score = float(ev.data.get('riskScore', 50.0))
    return explainability_service.explain(features, score, None, ev.entity_id)
_simulation_locks: dict[str, float] = {}

@router.post('/demo/simulate')
async def simulate(user=Depends(require('ingest')),db=Depends(get_db)):
    if not settings().enable_demo: raise HTTPException(404,'Demo disabled')
    import time as _t
    t_now = _t.monotonic()
    lock_key = f"{user.tenant_id}:{user.id}"
    if t_now - _simulation_locks.get(lock_key, 0.0) < 0.35:
        raise HTTPException(429, 'Simulation request already processing. Please wait.', headers={'Retry-After': '1'})
    _simulation_locks[lock_key] = t_now
    repo=TenantRepository(db,user.tenant_id);entities=repo.list('entities')
    if not entities: raise HTTPException(400,'No entities available')
    from uuid import uuid4
    e=entities[0]
    return await ingest(db,user,EventInput(eventId=str(uuid4()),entityId=e.id,timestamp=now(),action='Bulk download',resource='Finance / restricted',device='EP-1042',egressMb=2600,sensitiveFiles=110,newDevice=True))

@router.post('/cases',status_code=201)
def create_case(body:CaseCreate,user=Depends(require('triage')),db=Depends(get_db)):
    repo=TenantRepository(db,user.tenant_id);alert=repo.get('alerts',body.alertId)
    assignee=body.assignee or user.id
    validate_assignee(db,user,assignee)
    case=repo.add('cases',{'title':body.title,'alertId':alert.id,'status':'open','assignee':assignee,'notes':[],'bookmarks':[]},alert.entity_id)
    repo.update(alert,{'status':'investigating','caseId':case.id})
    append_audit(db,user.tenant_id,user.id,'case.created',case.id)
    return serialize(case)
def validate_assignee(db,user,id):
    assigned=db.scalar(select(User).where(User.id==id,User.tenant_id==user.tenant_id))
    if not assigned or assigned.role not in {'soc_analyst','soc_manager','org_admin','platform_admin'}: raise HTTPException(422,'Choose an analyst in this organization')
@router.patch('/cases/{id}')
def update_case(id:str,body:CasePatch,user=Depends(require('triage')),db=Depends(get_db)):
    repo=TenantRepository(db,user.tenant_id);row=repo.get('cases',id)
    allowed={'open':{'investigating'},'investigating':{'contained','closed'},'contained':{'investigating','closed'},'closed':{'investigating'}}
    if body.status and body.status!=row.data['status'] and body.status not in allowed[row.data['status']]: raise HTTPException(409,'Invalid workflow transition')
    if body.assignee: validate_assignee(db,user,body.assignee)
    repo.update(row,body.model_dump(exclude_none=True))
    if body.status and row.data.get('alertId'): repo.update(repo.get('alerts',row.data['alertId']),{'status':'closed' if body.status=='closed' else 'investigating'})
    append_audit(db,user.tenant_id,user.id,'case.updated',id,body.model_dump(exclude_none=True));return serialize(row)
@router.post('/cases/{id}/notes')
def note(id:str,body:Note,user=Depends(require('triage')),db=Depends(get_db)):
    repo=TenantRepository(db,user.tenant_id);row=repo.get('cases',id)
    # Lock before read-modify-write to prevent lost collaborative edits.
    db.refresh(row,with_for_update=True)
    repo.update(row,{'notes':[*row.data.get('notes',[]),{'text':body.text,'author':user.name,'timestamp':now().isoformat()}]})
    append_audit(db,user.tenant_id,user.id,'case.note_added',id);return serialize(row)
@router.post('/cases/{id}/bookmarks')
def bookmark(id:str,body:Bookmark,user=Depends(require('triage')),db=Depends(get_db)):
    repo=TenantRepository(db,user.tenant_id);row=repo.get('cases',id);repo.get('events',body.eventId);db.refresh(row,with_for_update=True)
    repo.update(row,{'bookmarks':list(dict.fromkeys([*row.data.get('bookmarks',[]),body.eventId]))});append_audit(db,user.tenant_id,user.id,'case.evidence_bookmarked',id);return serialize(row)
@router.get('/cases/{id}/similar')
def similar(id:str,user=Depends(require('read')),db=Depends(get_db)):
    repo=TenantRepository(db,user.tenant_id);case=repo.get('cases',id)
    terms=set(case.data['title'].lower().split())
    return [serialize(c) for c in repo.list('cases') if c.id!=id and (c.entity_id==case.entity_id or terms & set(c.data['title'].lower().split()))]
@router.post('/feedback',status_code=201)
def feedback(body:Feedback,user=Depends(require('feedback')),db=Depends(get_db)):
    repo=TenantRepository(db,user.tenant_id);alert=repo.get('alerts',body.alertId)
    row=repo.add('feedback',{**body.model_dump(),'analystId':user.id,'predictionIds':[p.id for p in repo.list('predictions',5,alert.entity_id)]},alert.entity_id)
    append_audit(db,user.tenant_id,user.id,'feedback.recorded',row.id,{'verdict':body.verdict});return serialize(row)
@router.post('/rules',status_code=201)
def rule(body:Rule,user=Depends(require('policy')),db=Depends(get_db)):
    row=TenantRepository(db,user.tenant_id).add('rules',body.model_dump());append_audit(db,user.tenant_id,user.id,'rule.created',row.id);return serialize(row)
@router.patch('/rules/{id}')
def edit_rule(id:str,body:Rule,user=Depends(require('policy')),db=Depends(get_db)):
    repo=TenantRepository(db,user.tenant_id);row=repo.update(repo.get('rules',id),body.model_dump());append_audit(db,user.tenant_id,user.id,'rule.updated',id);return serialize(row)
@router.get('/users')
def users(user=Depends(require('read')),db=Depends(get_db)):
    return [{'id':x.id,'name':x.name,'email':x.email,'role':x.role} for x in db.scalars(select(User).where(User.tenant_id==user.tenant_id))]
@router.post('/users',status_code=201)
def add_user(body:UserCreate,user=Depends(require('admin')),db=Depends(get_db)):
    org=db.get(Organization,user.tenant_id)
    count=db.scalar(select(func.count()).select_from(User).where(User.tenant_id==user.tenant_id))
    if count>=TIERS[org.tier]['users']: raise HTTPException(402,'Seat allowance reached')
    if db.scalar(select(User).where(User.tenant_id==user.tenant_id,User.email==body.email.lower())): raise HTTPException(409,'User already exists')
    row=User(tenant_id=user.tenant_id,email=body.email.lower(),name=body.name,role=body.role,password_hash=ph.hash(body.password));db.add(row);db.flush();append_audit(db,user.tenant_id,user.id,'user.created',row.id,{'role':row.role});return {'id':row.id,'name':row.name,'role':row.role,'email':row.email}
@router.post('/integrations',status_code=201)
def integration(body:Integration,user=Depends(require('admin')),db=Depends(get_db)):
    org=db.get(Organization,user.tenant_id)
    if not TIERS[org.tier]['integrations']: raise HTTPException(403,'Integration configuration requires Team or Enterprise')
    row=TenantRepository(db,user.tenant_id).add('integrations',{**body.model_dump(),'status':'configured' if body.kind=='http' else 'adapter_required','enabled':False})
    append_audit(db,user.tenant_id,user.id,'integration.configured',row.id);return serialize(row)
@router.get('/subscription')
def subscription(user=Depends(require('read')),db=Depends(get_db)):
    org=db.get(Organization,user.tenant_id)
    month=now().replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    count=db.scalar(select(func.count()).select_from(Record).where(Record.tenant_id==user.tenant_id,Record.kind=='events',Record.created_at>=month))
    return {'tier':org.tier,'limits':TIERS[org.tier],'eventsUsed':count,'plans':TIERS,'billingStatus':'not_connected'}
@router.patch('/subscription')
def tier(body:TierUpdate,user=Depends(require('admin')),db=Depends(get_db)):
    if not settings().enable_demo: raise HTTPException(403,'Plan changes require your billing administrator')
    org=db.get(Organization,user.tenant_id);org.tier=body.tier;append_audit(db,user.tenant_id,user.id,'subscription.changed',org.id,{'tier':body.tier});return {'tier':org.tier}
@router.get('/models')
def models(user=Depends(require('read'))):
    return [{'name':n,'mode':settings().model_mode,'version':'mock-1.0' if settings().model_mode=='mock' else 'provider_defined','configured':settings().model_mode=='mock' or n in settings().model_endpoints,'status':'mock' if settings().model_mode=='mock' else 'configured' if n in settings().model_endpoints else 'unconfigured'} for n in settings().models]
@router.get('/audit')
def audit(user=Depends(require('audit')),db=Depends(get_db)):
    return [{'id':a.sequence,'actor':a.actor_id,'action':a.action,'target':a.target_id,'hash':a.hash,'previousHash':a.previous_hash,'createdAt':a.created_at.isoformat()} for a in db.scalars(select(Audit).where(Audit.tenant_id==user.tenant_id).order_by(Audit.sequence.desc()).limit(500))]
@router.post('/notifications/{id}/read')
def mark_read(id:str,user=Depends(require('read')),db=Depends(get_db)):
    repo=TenantRepository(db,user.tenant_id);row=repo.update(repo.get('notifications',id),{'read':True});return serialize(row)
@router.get('/reports/export')
def report(user=Depends(require('report')),db=Depends(get_db)):
    repo=TenantRepository(db,user.tenant_id)
    append_audit(db,user.tenant_id,user.id,'report.exported',user.tenant_id)
    return {'organization':me(user,db)['organization'],'generatedAt':now().isoformat(),'synthetic':settings().model_mode=='mock','alerts':[serialize(x) for x in repo.list('alerts',1000)],'cases':[serialize(x) for x in repo.list('cases',1000)],'feedback':[serialize(x) for x in repo.list('feedback',1000)]}
@router.get('/stream')
def stream(request:Request,after:int=0,user=Depends(require('read')),db=Depends(get_db)):
    tenant,uid=user.tenant_id,user.id
    import jwt
    token=request.cookies.get('astra_session') or request.headers.get('authorization','')[7:]
    session_id=jwt.decode(token,settings().jwt_secret,algorithms=['HS256'],audience='astra',issuer='astra')['jti']
    # Release request transaction before a long-lived stream; each poll opens a new tenant scope.
    db.commit()
    async def generate():
        cursor=max(after,int(request.headers.get('last-event-id','0')))
        for _ in range(1800):
            if await request.is_disconnected(): break
            with SessionLocal() as session:
                set_tenant(session,tenant)
                active=session.scalar(select(Session).where(Session.id==session_id,Session.tenant_id==tenant,Session.user_id==uid,Session.expires_at>now()))
                if not active: break
                rows=list(session.scalars(select(Audit).where(Audit.tenant_id==tenant,Audit.sequence>cursor).order_by(Audit.sequence).limit(100)))
                for a in rows:
                    cursor=a.sequence
                    yield f'id: {cursor}\nevent: update\ndata: '+json.dumps({'action':a.action,'targetId':a.target_id,'data':a.data})+'\n\n'
            yield ': heartbeat\n\n'
            await asyncio.sleep(2)
    return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
READABLE={'entities','events','alerts','cases','rules','integrations','notifications','feedback','predictions','features','devices','resources'}
@router.get('/{kind}')
def records(kind:str,limit:int=100,offset:int=0,entityId:str|None=None,user=Depends(require('read')),db=Depends(get_db)):
    if kind not in READABLE: raise HTTPException(404,'Unknown resource')
    if not 1<=limit<=5000 or offset<0: raise HTTPException(422,'Invalid pagination')
    if kind in {'integrations','feedback','features','predictions'} and user.role=='viewer': raise HTTPException(403,'Permission denied')
    q=select(Record).where(Record.tenant_id==user.tenant_id,Record.kind==kind)
    if entityId: q=q.where(Record.entity_id==entityId)
    return [serialize(x) for x in db.scalars(q.order_by(Record.created_at.desc()).offset(offset).limit(limit))]
@router.get('/{kind}/{id}')
def record(kind:str,id:str,user=Depends(require('read')),db=Depends(get_db)):
    if kind not in READABLE: raise HTTPException(404,'Unknown resource')
    if kind in {'integrations','feedback','features','predictions'} and user.role=='viewer': raise HTTPException(403,'Permission denied')
    return serialize(TenantRepository(db,user.tenant_id).get(kind,id))
