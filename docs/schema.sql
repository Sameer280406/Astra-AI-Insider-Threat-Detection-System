-- Reference DDL. Apply Alembic migrations for RLS and immutability guards.

CREATE TABLE organizations (
	id VARCHAR NOT NULL, 
	slug VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	tier VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (slug)
)

;

CREATE TABLE audit_logs (
	sequence SERIAL NOT NULL, 
	tenant_id VARCHAR NOT NULL, 
	actor_id VARCHAR NOT NULL, 
	action VARCHAR NOT NULL, 
	target_id VARCHAR NOT NULL, 
	data JSON NOT NULL, 
	previous_hash VARCHAR NOT NULL, 
	hash VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (sequence)
)

;
CREATE INDEX ix_audit_logs_tenant_id ON audit_logs (tenant_id);

CREATE TABLE evidence_capsules (
	id VARCHAR NOT NULL, 
	tenant_id VARCHAR NOT NULL, 
	incident_id VARCHAR NOT NULL, 
	entity_id VARCHAR NOT NULL, 
	device VARCHAR NOT NULL, 
	manifest JSON NOT NULL, 
	hash VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
)

;
CREATE INDEX ix_evidence_capsules_entity_id ON evidence_capsules (entity_id);
CREATE UNIQUE INDEX ix_evidence_capsules_incident_id ON evidence_capsules (incident_id);
CREATE INDEX ix_evidence_capsules_tenant_id ON evidence_capsules (tenant_id);

CREATE TABLE users (
	id VARCHAR NOT NULL, 
	tenant_id VARCHAR NOT NULL, 
	email VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	password_hash VARCHAR NOT NULL, 
	role VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (tenant_id, email), 
	FOREIGN KEY(tenant_id) REFERENCES organizations (id)
)

;
CREATE INDEX ix_users_tenant_id ON users (tenant_id);

CREATE TABLE records (
	id VARCHAR NOT NULL, 
	tenant_id VARCHAR NOT NULL, 
	kind VARCHAR NOT NULL, 
	external_id VARCHAR, 
	entity_id VARCHAR, 
	data JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (tenant_id, kind, external_id), 
	FOREIGN KEY(tenant_id) REFERENCES organizations (id)
)

;
CREATE INDEX ix_records_entity_id ON records (entity_id);
CREATE INDEX ix_records_tenant_id ON records (tenant_id);
CREATE INDEX ix_records_created_at ON records (created_at);
CREATE INDEX ix_records_kind ON records (kind);

CREATE TABLE capsule_evidence (
	sequence SERIAL NOT NULL, 
	tenant_id VARCHAR NOT NULL, 
	capsule_id VARCHAR NOT NULL, 
	source_id VARCHAR NOT NULL, 
	phase VARCHAR NOT NULL, 
	snapshot JSON NOT NULL, 
	hash VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (sequence), 
	UNIQUE (capsule_id, source_id), 
	FOREIGN KEY(capsule_id) REFERENCES evidence_capsules (id)
)

;
CREATE INDEX ix_capsule_evidence_capsule_id ON capsule_evidence (capsule_id);
CREATE INDEX ix_capsule_evidence_tenant_id ON capsule_evidence (tenant_id);

CREATE TABLE capsule_seals (
	id VARCHAR NOT NULL, 
	tenant_id VARCHAR NOT NULL, 
	capsule_id VARCHAR NOT NULL, 
	manifest JSON NOT NULL, 
	hash VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (capsule_id), 
	FOREIGN KEY(capsule_id) REFERENCES evidence_capsules (id)
)

;
CREATE INDEX ix_capsule_seals_tenant_id ON capsule_seals (tenant_id);

CREATE TABLE sessions (
	id VARCHAR NOT NULL, 
	tenant_id VARCHAR NOT NULL, 
	user_id VARCHAR NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
)

;
CREATE INDEX ix_sessions_tenant_id ON sessions (tenant_id);