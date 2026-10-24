"""Append-only evidence capsule, evidence snapshots, and final seal."""
from alembic import op
from app.domain.forensics import Capsule,CapsuleEvidence,CapsuleSeal
revision='0002'
down_revision='0001'
branch_labels=None
depends_on=None
def upgrade():
    bind=op.get_bind()
    for model in [Capsule,CapsuleEvidence,CapsuleSeal]:
        model.__table__.create(bind,checkfirst=True)
        table=model.__tablename__
        if bind.dialect.name=='postgresql':
            op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
            op.execute(f"CREATE POLICY tenant_isolation ON {table} USING (tenant_id=current_setting('app.tenant_id',true)) WITH CHECK (tenant_id=current_setting('app.tenant_id',true))")
            op.execute(f'CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()')
        elif bind.dialect.name=='sqlite':
            for action in ['UPDATE','DELETE']:
                op.execute(f"CREATE TRIGGER immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Evidence is append-only'); END")
def downgrade():raise RuntimeError('Evidence retention prevents destructive downgrade')
