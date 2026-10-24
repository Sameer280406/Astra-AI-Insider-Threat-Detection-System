from sqlalchemy import select
from fastapi import HTTPException
from .entities import Record
class TenantRepository:
    def __init__(self,db,tenant): self.db,self.tenant=db,tenant
    def list(self,kind,limit=200,entity_id=None):
        q=select(Record).where(Record.tenant_id==self.tenant,Record.kind==kind)
        if entity_id: q=q.where(Record.entity_id==entity_id)
        return list(self.db.scalars(q.order_by(Record.created_at.desc()).limit(limit)))
    def get(self,kind,id):
        row=self.db.scalar(select(Record).where(Record.id==id,Record.kind==kind,Record.tenant_id==self.tenant))
        if not row: raise HTTPException(404,'Record not found')
        return row
    def add(self,kind,data,entity_id=None,external_id=None):
        row=Record(tenant_id=self.tenant,kind=kind,data=data,entity_id=entity_id,external_id=external_id)
        self.db.add(row);self.db.flush();return row
    def update(self,row,data): row.data={**row.data,**data};self.db.flush();return row

def serialize(row): return {'id':row.id,'entityId':row.entity_id,'createdAt':row.created_at.isoformat(),**row.data}
