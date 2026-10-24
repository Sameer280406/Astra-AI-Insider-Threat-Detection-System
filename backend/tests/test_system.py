import asyncio
from datetime import timedelta
from sqlalchemy import select,text
from app.core.db import SessionLocal
from app.core.security import ph
from app.domain.entities import User,Record,now
from app.domain.repository import TenantRepository
from app.models.contracts import FeatureRequest
from app.models.gateway import MockAdapter,RemoteAdapter,ModelUnavailable
from app.services.containment import policy_for,digest
from app.api.containment_schemas import ContainmentPolicy
from app.core.config import settings

def onboard(c,slug='alpha'):
    r=c.post('/api/v1/auth/onboard',json={'organization':slug,'organizationName':slug.title(),'name':'Admin User','email':'admin@example.com','password':'Strong-Test-Password-123'})
    assert r.status_code==201,r.text
    return r.json()
def entity(p):
    with SessionLocal() as db:
        row=TenantRepository(db,p['organization']['id']).add('entities',{'name':'Test Employee','department':'Engineering','riskScore':10})
        device=TenantRepository(db,p['organization']['id']).add('devices',{'name':'EP-test','managed':True},row.id)
        db.commit();return row.id

def event(e,id='evt-1',**kwargs):return {'eventId':id,'entityId':e,'timestamp':(now().replace(hour=22)-timedelta(days=1)).isoformat(),'action':'Bulk download','resource':'Restricted records','device':'EP-test','egressMb':3000,'sensitiveFiles':100,'newDevice':True,**kwargs}
def policy(c,mode='analyst_approval',**kwargs):
    payload=ContainmentPolicy(mode=mode,**kwargs).model_dump()
    r=c.put('/api/v1/response/policy',json=payload);assert r.status_code==200,r.text

def test_end_to_end_case_feedback_and_dedup(client):
    p=onboard(client);e=entity(p)
    r=client.post('/api/v1/events',json=event(e));assert r.status_code==201,r.text
    a=r.json()['alert'];assert a and r.json()['prediction']['modelName']=='Ensemble'
    duplicate=client.post('/api/v1/events',json=event(e));assert duplicate.json()['duplicate']
    c=client.post('/api/v1/cases',json={'title':'Investigate download','alertId':a['id']}).json()
    assert client.patch('/api/v1/cases/'+c['id'],json={'status':'closed'}).status_code==409
    assert client.patch('/api/v1/cases/'+c['id'],json={'status':'investigating'}).status_code==200
    assert client.post('/api/v1/cases/'+c['id']+'/notes',json={'text':'A documented finding'}).status_code==200
    assert client.post('/api/v1/feedback',json={'alertId':a['id'],'verdict':'needs_review','reason':'Investigating context'}).status_code==201
    assert client.get('/api/v1/reports/export').status_code==200

def test_custom_rule_threshold_and_severity_are_explained(client):
    p=onboard(client);e=entity(p)
    created=client.post('/api/v1/rules',json={
        'name':'Low egress policy','feature':'egress_mb','threshold':10,'severity':'critical'
    })
    assert created.status_code==201,created.text
    below=client.post('/api/v1/events',json=event(
        e,'below-rule-threshold',timestamp=(now()-timedelta(hours=1)).isoformat(),
        egressMb=5,sensitiveFiles=0,newDevice=False
    ))
    assert below.status_code==201,below.text
    assert below.json()['alert'] is None

    rule_id=created.json()['id']
    updated=client.patch('/api/v1/rules/'+rule_id,json={
        'name':'Low egress policy','feature':'egress_mb','threshold':1,
        'severity':'critical','enabled':True
    })
    assert updated.status_code==200,updated.text

    scored=client.post('/api/v1/events',json=event(
        e,'above-rule-threshold',timestamp=(now()-timedelta(hours=1)).isoformat(),
        egressMb=5,sensitiveFiles=0,newDevice=False
    ))
    assert scored.status_code==201,scored.text
    result=scored.json();alert=result['alert'];prediction=result['prediction']
    assert alert is not None
    assert prediction['riskScore']<65
    assert alert['riskScore']==prediction['riskScore']
    assert alert['severity']=='critical'
    assert alert['matchedRules']==['Low egress policy']
    match=alert['ruleMatches'][0]
    assert match=={
        'id':rule_id,'name':'Low egress policy','feature':'egress_mb',
        'operator':'>=','threshold':1.0,'observedValue':5.0,'severity':'critical'
    }
    xai=alert['xai']
    assert xai['risk_score']==prediction['riskScore']
    assert xai['rule_matches']==alert['ruleMatches']
    assert prediction['metadata']['xai']==xai
    assert 'egress_mb >= 1 (observed 5; critical severity)' in xai['rule_summary']
    assert 'did not change the model risk score' in xai['narrative']
    assert 'Low egress policy' in alert['explanation']
    event_xai=client.get('/api/v1/explain/event/'+result['event']['id'])
    assert event_xai.status_code==200
    assert event_xai.json()['rule_matches']==alert['ruleMatches']

def test_cross_tenant_ids_are_not_readable_or_mutable(client):
    first=onboard(client);e=entity(first);scored=client.post('/api/v1/events',json=event(e)).json();incident=scored['incidentId']
    detail=client.get('/api/v1/response/incidents/'+incident).json();capsule=detail['capsuleId']
    onboard(client,'beta')
    assert client.get('/api/v1/entities/'+e).status_code==404
    assert client.get('/api/v1/response/incidents/'+incident).status_code==404
    assert client.get('/api/v1/response/capsules/'+capsule).status_code==404
    assert client.post('/api/v1/response/incidents/'+incident+'/approve',json={'reason':'Unauthorized tenant'}).status_code==404
    assert client.get('/api/v1/events').json()==[]

def test_viewer_and_analyst_cannot_contain(client):
    p=onboard(client);e=entity(p);incident=client.post('/api/v1/events',json=event(e)).json()['incidentId']
    for role in ['viewer','soc_analyst']:
        with SessionLocal() as db:
            user=db.get(User,p['id']);user.role=role;db.commit()
        assert client.post('/api/v1/response/incidents/'+incident+'/approve',json={'reason':'Manual response'}).status_code==403
    assert client.post('/api/v1/events',json=event(e,'second')).status_code==403

def test_approval_release_and_management_channel(client):
    p=onboard(client);e=entity(p);policy(client)
    r=client.post('/api/v1/events',json=event(e)).json();iid=r['incidentId'];d=client.get('/api/v1/response/incidents/'+iid).json()
    assert d['state']=='pending_approval'
    assert not d['actions']
    approved=client.post('/api/v1/response/incidents/'+iid+'/approve',json={'reason':'Verified critical evidence'})
    assert approved.json()['state']=='quarantined'
    assert client.post('/api/v1/response/incidents/'+iid+'/approve',json={'reason':'Duplicate approval retry'}).status_code==200
    d=client.get('/api/v1/response/incidents/'+iid).json();assert len(d['actions'])==1
    assert d['actions'][0]['result']['managementChannels']==['astra-management','soc-console','edr-control']
    assert d['actions'][0]['result']['simulated'] is True
    assert client.post('/api/v1/response/incidents/'+iid+'/release',json={'reason':'Containment no longer needed'}).json()['state']=='released'

def test_automatic_requires_independent_rule_corroboration(client):
    p=onboard(client);e=entity(p);policy(client,'automatic_critical',minimumConsensus=1,minimumConfidence=.8)
    # A risk-derived rule is not independent corroboration.
    client.post('/api/v1/rules',json={'name':'Risk only','feature':'riskScore','threshold':80})
    r=client.post('/api/v1/events',json=event(e)).json();d=client.get('/api/v1/response/incidents/'+r['incidentId']).json()
    assert d['state']=='pending_approval';assert d['gates']['ruleCorroboration'] is False

def test_automatic_all_gates_pass(client):
    p=onboard(client);e=entity(p);policy(client,'automatic_critical',minimumConsensus=2,criticalThreshold=85,terminateSessions=True,restrictCredentials=True)
    client.post('/api/v1/rules',json={'name':'File burst','feature':'sensitive_files','threshold':70})
    r=client.post('/api/v1/events',json=event(e)).json();d=client.get('/api/v1/response/incidents/'+r['incidentId']).json()
    assert all(d['gates'].values());assert d['state']=='quarantined'
    assert {a['action'] for a in d['actions']}=={'quarantine','terminate_sessions','restrict_credentials'}

def test_advisory_never_automatically_quarantines(client):
    p=onboard(client);e=entity(p);policy(client,'advisory',requireRuleCorroboration=False,minimumConsensus=1)
    r=client.post('/api/v1/events',json=event(e)).json();d=client.get('/api/v1/response/incidents/'+r['incidentId']).json()
    assert d['state']=='advisory';assert d['actions']==[]

def test_selected_pre_and_post_evidence_are_frozen(client):
    p=onboard(client);e=entity(p);policy(client,categories=['file','network'])
    t=now()-timedelta(minutes=2)
    client.post('/api/v1/events',json=event(e,'pre',timestamp=(t-timedelta(minutes=5)).isoformat(),egressMb=10,sensitiveFiles=0,newDevice=False,category='file'))
    critical=client.post('/api/v1/events',json=event(e,'trigger',timestamp=t.isoformat(),category='network')).json()
    d=client.get('/api/v1/response/incidents/'+critical['incidentId']).json()
    cap=client.get('/api/v1/response/capsules/'+d['capsuleId']).json();assert len(cap['evidence'])==2
    assert cap['integrityVerified']
    client.post('/api/v1/events',json=event(e,'post',timestamp=(t+timedelta(minutes=1)).isoformat(),category='file'))
    client.post('/api/v1/events',json=event(e,'irrelevant',timestamp=(t+timedelta(minutes=1)).isoformat(),category='command'))
    cap=client.get('/api/v1/response/capsules/'+d['capsuleId']).json();assert len(cap['evidence'])==3
    with SessionLocal() as db:
        source=db.get(Record,cap['evidence'][0]['sourceId']);source.data={**source.data,'action':'Altered source'};db.commit()
    after=client.get('/api/v1/response/capsules/'+d['capsuleId']).json()
    assert after['evidence'][0]['snapshot']['action']!='Altered source'
    assert after['integrityVerified']

def test_secure_mutations_and_logout(client):
    p=onboard(client)
    assert client.post('/api/v1/auth/logout',headers={'X-Astra-Request':'0'}).status_code==403
    assert client.post('/api/v1/auth/logout').status_code==200
    assert client.get('/api/v1/auth/me').status_code==401

def test_model_mock_deterministic():
    r=FeatureRequest(tenantId='t',entityId='e',timestamp=now(),eventId='ev',features={'egress_mb':1800,'sensitive_files':70})
    adapter=MockAdapter('IsolationForest')
    a=asyncio.run(adapter.predict(r));b=asyncio.run(adapter.predict(r));assert a==b
    assert 0<=a.riskScore<=100

def test_remote_contract_without_ui_changes(monkeypatch,client):
    import httpx
    p=onboard(client);e=entity(p)
    real_client=httpx.AsyncClient
    async def remote(request):
        from app.models.contracts import FeatureRequest
        import json
        payload=FeatureRequest.model_validate(json.loads(request.content))
        name=request.url.path.rsplit('/',1)[-1]
        prediction=await MockAdapter(name).predict(payload)
        prediction.modelVersion='trained-contract-fixture-2.0'
        prediction.metadata={'synthetic':False,'testFixture':True}
        return httpx.Response(200,json=prediction.model_dump(mode='json'))
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(remote),**kwargs))
    s=settings();monkeypatch.setattr(s,'model_mode','remote');monkeypatch.setattr(s,'model_endpoints',{n:'https://models.example/'+n for n in s.models})
    response=client.post('/api/v1/events',json=event(e))
    assert response.status_code==201,response.text
    assert response.json()['prediction']['modelVersion']=='trained-contract-fixture-2.0'
    assert client.get('/api/v1/entities/'+e).json()['predictions'][0]['modelVersion']=='trained-contract-fixture-2.0'

def test_remote_failure_does_not_persist_partial_scoring(monkeypatch,client):
    p=onboard(client);e=entity(p);s=settings();monkeypatch.setattr(s,'model_mode','remote');monkeypatch.setattr(s,'model_endpoints',{})
    r=client.post('/api/v1/events',json=event(e));assert r.status_code==502
    assert client.get('/api/v1/events').json()==[]

def test_policy_rejects_unsafe_management_allowlist(client):
    onboard(client)
    body=ContainmentPolicy().model_dump();body['managementChannels']=['0.0.0.0/0']
    assert client.put('/api/v1/response/policy',json=body).status_code==422
