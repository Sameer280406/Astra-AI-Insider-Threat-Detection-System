import asyncio
from datetime import timedelta
from sqlalchemy import select
from .core.db import SessionLocal,set_tenant
from .core.config import settings
from .core.security import ph
from .domain.entities import Organization,User,now
from .domain.repository import TenantRepository
from .api.schemas import EventInput
from .services.pipeline import ingest
from .services.audit import append_audit
NAMES=[('Maya Chen','Engineering'),('James Wilson','Finance'),('Aarav Mehta','Engineering'),('Olivia Parker','Operations'),('Noah Williams','Sales'),('Sofia Rivera','Finance'),('Ethan Brooks','Engineering'),('Isabella Rossi','People'),('Liam Carter','Sales'),('Amara Okafor','Operations'),('Lucas Martin','Engineering'),('Zoe Kim','Finance')]
async def seed():
    if not settings().enable_demo or len(settings().demo_password)<12: raise RuntimeError('Set ENABLE_DEMO=true and DEMO_PASSWORD with at least 12 characters')
    with SessionLocal() as db:
        if db.scalar(select(Organization).where(Organization.slug=='meridian')): return
        org=Organization(slug='meridian',name='Meridian Technologies',tier='enterprise');db.add(org);db.flush();set_tenant(db,org.id)
        admin=None
        for role in ['org_admin','soc_manager','soc_analyst','auditor','viewer','platform_admin']:
            u=User(tenant_id=org.id,email=role+'@astra.demo',name='Alex Morgan' if role=='org_admin' else role.replace('_',' ').title(),role=role,password_hash=ph.hash(settings().demo_password));db.add(u);db.flush()
            if role=='org_admin': admin=u
        repo=TenantRepository(db,org.id)
        entities=[]
        for i,(name,dept) in enumerate(NAMES):
            e=repo.add('entities',{'name':name,'department':dept,'email':name.lower().replace(' ','.')+'@meridian.example','role':'Senior analyst' if dept=='Finance' else 'Team member','riskScore':12,'riskVelocity':0,'synthetic':True,'location':'Mumbai, IN','status':'active'});entities.append(e)
            repo.add('devices',{'name':f'EP-{1042+i}','owner':name,'platform':'macOS' if i%2 else 'Windows 11','managed':True},e.id)
        for name in ['Finance / restricted','Engineering / source','Sales / CRM','People / records']: repo.add('resources',{'name':name,'sensitivity':'restricted','synthetic':True})
        repo.add('rules',{'name':'Sensitive data burst','feature':'sensitive_files','threshold':70,'enabled':True,'severity':'high'})
        repo.add('rules',{'name':'Rapid risk escalation','feature':'riskVelocity','threshold':25,'enabled':True,'severity':'critical'})
        for k in range(7):
            for i,e in enumerate(entities):
                high=(i in [0,1,5] and k>=5)
                await ingest(db,admin,EventInput(eventId=f'seed-{i}-{k}',entityId=e.id,timestamp=now()-timedelta(hours=(6-k)*4,minutes=i*3),action='Bulk download' if high else ['File access','Login','Repository pull'][i%3],resource='Finance / restricted' if high else 'Engineering / source',device=f'EP-{1042+i}',egressMb=2400+i*50 if high else 25+i*8+k*3,sensitiveFiles=95 if high else i%5,newDevice=high))
        alerts=repo.list('alerts')
        for i,a in enumerate(alerts[:3]):
            c=repo.add('cases',{'title':['Unusual outbound transfer','Restricted document access','Off-hours activity review'][i],'alertId':a.id,'status':'investigating' if i==0 else 'open','assignee':admin.id,'notes':[{'text':'Reviewing the activity against the employee baseline. Synthetic investigation.','author':'Alex Morgan','timestamp':now().isoformat()}],'bookmarks':[a.data['eventId']]},a.entity_id)
            repo.update(a,{'status':'investigating','caseId':c.id})
        for name,kind in [('HTTP event API','http'),('Apache Kafka','kafka'),('Microsoft Sentinel','siem')]:repo.add('integrations',{'name':name,'kind':kind,'endpoint':'','status':'available' if kind=='http' else 'adapter_required','enabled':kind=='http'})
        append_audit(db,org.id,admin.id,'demo.seeded',org.id,{'synthetic':True})
        db.commit()
if __name__=='__main__': asyncio.run(seed())
