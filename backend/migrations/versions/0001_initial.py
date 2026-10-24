"""Initial versioned schema, tenant RLS and append-only audit."""
from alembic import op
from app.domain.entities import Base
revision='0001'
down_revision=None
branch_labels=None
depends_on=None
def upgrade():
    bind=op.get_bind();Base.metadata.create_all(bind)
    if bind.dialect.name=='postgresql':
        for table in ['records','audit_logs']:
            op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
            op.execute(f"CREATE POLICY tenant_isolation ON {table} USING (tenant_id = current_setting('app.tenant_id',true)) WITH CHECK (tenant_id = current_setting('app.tenant_id',true))")
        op.execute("""CREATE FUNCTION reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Audit log is append-only'; END $$""")
        op.execute('CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON audit_logs FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()')
        op.execute('CREATE INDEX records_tenant_kind_time ON records (tenant_id,kind,created_at DESC)')
def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore an approved backup')
