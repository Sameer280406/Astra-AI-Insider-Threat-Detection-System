from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import String, DateTime, JSON, Float, Integer, UniqueConstraint, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

def now(): return datetime.now(timezone.utc)
def uid(): return str(uuid4())
class Base(DeclarativeBase): pass
class Organization(Base):
    __tablename__='organizations'
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    slug: Mapped[str]=mapped_column(String, unique=True)
    name: Mapped[str]=mapped_column(String)
    tier: Mapped[str]=mapped_column(String, default='enterprise')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class User(Base):
    __tablename__='users'
    __table_args__=(UniqueConstraint('tenant_id','email'),)
    id: Mapped[str]=mapped_column(String,primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(ForeignKey('organizations.id'),index=True)
    email: Mapped[str]=mapped_column(String)
    name: Mapped[str]=mapped_column(String)
    password_hash: Mapped[str]=mapped_column(String)
    role: Mapped[str]=mapped_column(String)
class Session(Base):
    __tablename__='sessions'
    id: Mapped[str]=mapped_column(String,primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(String,index=True)
    user_id: Mapped[str]=mapped_column(ForeignKey('users.id'))
    expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
class Record(Base):
    __tablename__='records'
    __table_args__=(UniqueConstraint('tenant_id','kind','external_id'),)
    id: Mapped[str]=mapped_column(String,primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(ForeignKey('organizations.id'),index=True)
    kind: Mapped[str]=mapped_column(String,index=True)
    external_id: Mapped[str|None]=mapped_column(String,nullable=True)
    entity_id: Mapped[str|None]=mapped_column(String,nullable=True,index=True)
    data: Mapped[dict]=mapped_column(JSON)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,index=True)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,onupdate=now)
class Audit(Base):
    __tablename__='audit_logs'
    sequence: Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    tenant_id: Mapped[str]=mapped_column(String,index=True)
    actor_id: Mapped[str]=mapped_column(String)
    action: Mapped[str]=mapped_column(String)
    target_id: Mapped[str]=mapped_column(String)
    data: Mapped[dict]=mapped_column(JSON)
    previous_hash: Mapped[str]=mapped_column(String)
    hash: Mapped[str]=mapped_column(String)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
