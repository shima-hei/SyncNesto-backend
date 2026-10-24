CREATE TABLE requirement_verification_plans (
    id uuid PRIMARY KEY,
    project_id integer NOT NULL REFERENCES projects(id),
    source_requirement_id integer NOT NULL,
    release_id uuid NOT NULL REFERENCES artifact_releases(id),
    target_node_id uuid NOT NULL,
    subject_digest varchar(64) NOT NULL,
    current_version integer NOT NULL,
    created_by integer NOT NULL REFERENCES users(id),
    updated_by integer NOT NULL REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    request_id uuid NOT NULL,
    request_digest varchar(64) NOT NULL,
    CONSTRAINT uq_verification_plan_request UNIQUE(project_id,created_by,request_id),
    CONSTRAINT fk_verification_target FOREIGN KEY(release_id,target_node_id) REFERENCES artifact_release_nodes(release_id,id),
    CONSTRAINT ck_verification_plan_version CHECK(current_version > 0 AND source_requirement_id > 0),
    CONSTRAINT ck_verification_plan_digest CHECK(subject_digest ~ '^[a-f0-9]{64}$' AND request_digest ~ '^[a-f0-9]{64}$')
);
CREATE INDEX ix_verification_plan_source ON requirement_verification_plans(project_id,source_requirement_id,created_at,id);
CREATE TABLE requirement_verification_plan_revisions (
    id uuid PRIMARY KEY,
    plan_id uuid NOT NULL REFERENCES requirement_verification_plans(id),
    version integer NOT NULL,
    schema_version integer NOT NULL DEFAULT 1,
    checklist jsonb NOT NULL,
    checklist_digest varchar(64) NOT NULL,
    actor_id integer NOT NULL REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    operation_id uuid NOT NULL,
    operation_digest varchar(64) NOT NULL,
    CONSTRAINT uq_verification_revision_version UNIQUE(plan_id,version),
    CONSTRAINT uq_verification_revision_operation UNIQUE(plan_id,operation_id),
    CONSTRAINT ck_verification_revision_version CHECK(version > 0 AND schema_version=1),
    CONSTRAINT ck_verification_revision_size CHECK((jsonb_typeof(checklist)='object' AND jsonb_typeof(checklist->'rows')='array' AND jsonb_array_length(checklist->'rows')<=100 AND octet_length(checklist::text)<=262144) IS TRUE),
    CONSTRAINT ck_verification_revision_digest CHECK(checklist_digest ~ '^[a-f0-9]{64}$' AND operation_digest ~ '^[a-f0-9]{64}$')
);
ALTER TABLE requirement_verification_plans ADD CONSTRAINT fk_verification_current FOREIGN KEY(id,current_version) REFERENCES requirement_verification_plan_revisions(plan_id,version) DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION guard_requirement_verification_plan() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Verification plans must be retained' USING ERRCODE='23514';
    ELSIF TG_OP='INSERT' THEN
        IF NEW.current_version<>1 OR NEW.created_by<>NEW.updated_by OR NOT EXISTS(
            SELECT 1 FROM artifact_releases r JOIN artifact_release_nodes n ON n.release_id=r.id
            WHERE r.id=NEW.release_id AND r.project_id=NEW.project_id AND r.sealed
              AND n.id=NEW.target_node_id AND n.node_kind='requirement' AND n.source_id=NEW.source_requirement_id
        ) THEN
            RAISE EXCEPTION 'Invalid fixed requirement' USING ERRCODE='23514';
        END IF;
        NEW.created_at=transaction_timestamp();
    ELSE
        IF (to_jsonb(NEW)-ARRAY['current_version','updated_by','updated_at']) IS DISTINCT FROM
           (to_jsonb(OLD)-ARRAY['current_version','updated_by','updated_at']) OR NEW.current_version<>OLD.current_version+1 THEN
            RAISE EXCEPTION 'Immutable verification target' USING ERRCODE='23514';
        END IF;
    END IF;
    NEW.updated_at=transaction_timestamp();
    RETURN NEW;
END $$;
CREATE TRIGGER verification_plan_guard BEFORE INSERT OR UPDATE OR DELETE ON requirement_verification_plans FOR EACH ROW EXECUTE FUNCTION guard_requirement_verification_plan();

CREATE FUNCTION guard_requirement_verification_revision() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE head requirement_verification_plans; last_version integer;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Immutable verification revision' USING ERRCODE='23514';
    END IF;
    SELECT * INTO STRICT head FROM requirement_verification_plans WHERE id=NEW.plan_id FOR UPDATE;
    SELECT coalesce(max(version),0) INTO last_version FROM requirement_verification_plan_revisions WHERE plan_id=NEW.plan_id;
    IF NEW.version<>last_version+1 OR (last_version=0 AND head.current_version<>1) OR (last_version>0 AND head.current_version<>last_version) THEN
        RAISE EXCEPTION 'Non-sequential verification revision' USING ERRCODE='23514';
    END IF;
    NEW.created_at=transaction_timestamp();
    RETURN NEW;
END $$;
CREATE TRIGGER verification_revision_guard BEFORE INSERT OR UPDATE OR DELETE ON requirement_verification_plan_revisions FOR EACH ROW EXECUTE FUNCTION guard_requirement_verification_revision();

CREATE FUNCTION check_requirement_verification_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target uuid; head requirement_verification_plans; revision_count bigint; latest integer; actor integer;
BEGIN
    IF TG_TABLE_NAME='requirement_verification_plans' THEN target=NEW.id; ELSE target=NEW.plan_id; END IF;
    SELECT * INTO STRICT head FROM requirement_verification_plans WHERE id=target;
    SELECT count(*),max(version) INTO revision_count,latest FROM requirement_verification_plan_revisions WHERE plan_id=target;
    SELECT actor_id INTO actor FROM requirement_verification_plan_revisions WHERE plan_id=target AND version=head.current_version;
    IF latest IS DISTINCT FROM head.current_version OR revision_count<>head.current_version OR actor IS DISTINCT FROM head.updated_by THEN
        RAISE EXCEPTION 'Incomplete verification save' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER verification_plan_complete AFTER INSERT OR UPDATE ON requirement_verification_plans DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_requirement_verification_complete();
CREATE CONSTRAINT TRIGGER verification_revision_complete AFTER INSERT ON requirement_verification_plan_revisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_requirement_verification_complete();

INSERT INTO permissions(code,description) VALUES
('requirement_verification:read','固定要件の確認表を閲覧する'),
('requirement_verification:prepare','固定要件の確認表を準備する') ON CONFLICT(code) DO NOTHING;
INSERT INTO role_permissions(role_id,permission_id)
SELECT r.id,p.id FROM roles r CROSS JOIN permissions p WHERE r.scope='project' AND (
 (p.code='requirement_verification:read' AND r.key IN ('project_admin','manager','member','viewer')) OR
 (p.code='requirement_verification:prepare' AND r.key IN ('project_admin','manager'))
) ON CONFLICT DO NOTHING;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='syncnesto_app') THEN
        REVOKE ALL PRIVILEGES ON requirement_verification_plans,requirement_verification_plan_revisions FROM syncnesto_app;
        GRANT SELECT,INSERT ON requirement_verification_plans,requirement_verification_plan_revisions TO syncnesto_app;
        GRANT UPDATE(current_version,updated_by,updated_at) ON requirement_verification_plans TO syncnesto_app;
    END IF;
END $$;
