from sqlalchemy import String,JSON,Integer,DateTime,UniqueConstraint,ForeignKey
from sqlalchemy.orm import Mapped,mapped_column
from .entities import Base,now,uid
class Capsule(Base):
    __tablename__='evidence_capsules'
    id: Mapped[str]=mapped_column(String,primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(String,index=True)
    incident_id: Mapped[str]=mapped_column(String,index=True,unique=True)
    entity_id: Mapped[str]=mapped_column(String,index=True)
    device: Mapped[str]=mapped_column(String)
    manifest: Mapped[dict]=mapped_column(JSON)
    hash: Mapped[str]=mapped_column(String)
    created_at: Mapped[object]=mapped_column(DateTime(timezone=True),default=now)
class CapsuleEvidence(Base):
    __tablename__='capsule_evidence'
    __table_args__=(UniqueConstraint('capsule_id','source_id'),)
    sequence: Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    tenant_id: Mapped[str]=mapped_column(String,index=True)
    capsule_id: Mapped[str]=mapped_column(ForeignKey('evidence_capsules.id'),index=True)
    source_id: Mapped[str]=mapped_column(String)
    phase: Mapped[str]=mapped_column(String)
    snapshot: Mapped[dict]=mapped_column(JSON)
    hash: Mapped[str]=mapped_column(String)
    created_at: Mapped[object]=mapped_column(DateTime(timezone=True),default=now)
class CapsuleSeal(Base):
    __tablename__='capsule_seals'
    id: Mapped[str]=mapped_column(String,primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(String,index=True)
    capsule_id: Mapped[str]=mapped_column(ForeignKey('evidence_capsules.id'),unique=True)
    manifest: Mapped[dict]=mapped_column(JSON)
    hash: Mapped[str]=mapped_column(String)
    created_at: Mapped[object]=mapped_column(DateTime(timezone=True),default=now)
