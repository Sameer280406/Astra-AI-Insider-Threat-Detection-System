from alembic import context
from app.core.db import engine
from app.domain.entities import Base
with engine.connect() as connection:
    context.configure(connection=connection,target_metadata=Base.metadata)
    with context.begin_transaction(): context.run_migrations()
