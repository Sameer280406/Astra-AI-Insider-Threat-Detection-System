"""Prevent evidence writes after sealing and protect audit on SQLite too."""
from alembic import op
revision='0003'
down_revision='0002'
branch_labels=None
depends_on=None
def upgrade():
    bind=op.get_bind()
    if bind.dialect.name=='postgresql':
        op.execute("""CREATE FUNCTION reject_sealed_evidence() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
          PERFORM pg_advisory_xact_lock(hashtext(NEW.capsule_id));
          IF EXISTS (SELECT 1 FROM capsule_seals WHERE capsule_id=NEW.capsule_id AND tenant_id=NEW.tenant_id) THEN RAISE EXCEPTION 'Capsule sealed'; END IF;
          RETURN NEW; END $$""")
        op.execute('CREATE TRIGGER reject_sealed_evidence BEFORE INSERT ON capsule_evidence FOR EACH ROW EXECUTE FUNCTION reject_sealed_evidence()')
        op.execute('REVOKE UPDATE, DELETE ON audit_logs,evidence_capsules,capsule_evidence,capsule_seals FROM PUBLIC')
    elif bind.dialect.name=='sqlite':
        op.execute("""CREATE TRIGGER reject_sealed_evidence BEFORE INSERT ON capsule_evidence WHEN EXISTS (SELECT 1 FROM capsule_seals WHERE capsule_id=NEW.capsule_id) BEGIN SELECT RAISE(ABORT,'Capsule sealed'); END""")
        for action in ['UPDATE','DELETE']:
            op.execute(f"CREATE TRIGGER immutable_audit_{action} BEFORE {action} ON audit_logs BEGIN SELECT RAISE(ABORT,'Audit log is append-only'); END")
def downgrade():raise RuntimeError('Evidence retention prevents destructive downgrade')
