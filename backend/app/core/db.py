from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from .config import settings
engine=create_engine(settings().database_url, pool_pre_ping=True, **({'connect_args':{'check_same_thread':False}} if settings().database_url.startswith('sqlite') else {}))
SessionLocal=sessionmaker(engine,expire_on_commit=False)
def set_tenant(db, tenant_id):
    if engine.dialect.name=='postgresql':
        db.execute(text("SELECT set_config('app.tenant_id', :tenant, true)"),{'tenant':tenant_id})
def get_db():
    with SessionLocal() as db:
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback(); raise
