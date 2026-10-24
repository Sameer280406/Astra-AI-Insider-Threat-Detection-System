import os,subprocess,sqlite3,json,sys
from pathlib import Path
import pytest

def test_versioned_migrations_enforce_immutable_capsules(tmp_path):
    path=tmp_path/'migrated.db'
    env={**os.environ,'DATABASE_URL':'sqlite:///'+str(path)}
    cwd=Path(__file__).resolve().parent.parent
    subprocess.run([sys.executable,'-m','alembic','upgrade','head'],cwd=cwd,env=env,check=True,capture_output=True)
    db=sqlite3.connect(path)
    db.execute("INSERT INTO evidence_capsules (id,tenant_id,incident_id,entity_id,device,manifest,hash,created_at) VALUES ('c','t','i','e','d','{}','hash','2026-09-30')")
    db.execute("INSERT INTO capsule_evidence (tenant_id,capsule_id,source_id,phase,snapshot,hash,created_at) VALUES ('t','c','ev','trigger','{}','hash','2026-09-30')")
    db.execute("INSERT INTO capsule_seals (id,tenant_id,capsule_id,manifest,hash,created_at) VALUES ('s','t','c','{}','hash','2026-09-30')")
    db.commit()
    for query in ["UPDATE evidence_capsules SET hash='tampered'", "DELETE FROM capsule_evidence", "UPDATE capsule_seals SET hash='tampered'", "INSERT INTO capsule_evidence (tenant_id,capsule_id,source_id,phase,snapshot,hash,created_at) VALUES ('t','c','late','post_event','{}','hash','2026-09-30')"]:
        with pytest.raises(sqlite3.IntegrityError): db.execute(query)
        db.rollback()
    assert db.execute('SELECT version_num FROM alembic_version').fetchone()[0]=='0003'
