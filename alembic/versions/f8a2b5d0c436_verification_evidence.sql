CREATE TABLE requirement_verification_evidence_sets (
 id uuid PRIMARY KEY, project_id integer NOT NULL REFERENCES projects(id),
 plan_id uuid NOT NULL REFERENCES requirement_verification_plans(id), plan_version integer NOT NULL,
 schema_version integer NOT NULL DEFAULT 1, subject_digest varchar(64) NOT NULL, checklist_digest varchar(64) NOT NULL,
 created_by integer NOT NULL REFERENCES users(id), actor_name varchar(200) NOT NULL,
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(), request_id uuid NOT NULL,
 request_digest varchar(64) NOT NULL, preview_digest varchar(64) NOT NULL, selection jsonb NOT NULL,
 status varchar(20) NOT NULL DEFAULT 'preparing', version integer NOT NULL DEFAULT 1,
 manifest jsonb, manifest_digest varchar(64), completed_at timestamptz,
 lease_id uuid, lease_expires_at timestamptz, safe_error varchar(50),
 FOREIGN KEY(plan_id,plan_version) REFERENCES requirement_verification_plan_revisions(plan_id,version),
 CONSTRAINT uq_verification_evidence_request UNIQUE(project_id,created_by,plan_id,request_id),
 CHECK(schema_version=1 AND plan_version>0 AND version>0),
 CHECK(subject_digest ~ '^[a-f0-9]{64}$' AND checklist_digest ~ '^[a-f0-9]{64}$' AND request_digest ~ '^[a-f0-9]{64}$' AND preview_digest ~ '^[a-f0-9]{64}$'),
 CHECK(status IN ('preparing','ready','failed','cancelled')),
 CHECK((lease_id IS NULL)=(lease_expires_at IS NULL)),
 CHECK((jsonb_typeof(selection)='object' AND jsonb_typeof(selection->'entries')='array' AND jsonb_array_length(selection->'entries') BETWEEN 1 AND 200 AND jsonb_typeof(selection->'files')='array' AND jsonb_array_length(selection->'files')<=20 AND jsonb_typeof(selection->'inventory')='array' AND jsonb_array_length(selection->'inventory')<=1000 AND octet_length(selection::text)<=10485760) IS TRUE),
 CHECK(manifest IS NULL OR (jsonb_typeof(manifest)='object' AND octet_length(manifest::text)<=10485760)),
 CHECK(status='ready' OR (manifest IS NULL AND manifest_digest IS NULL AND completed_at IS NULL))
);
CREATE INDEX ix_verification_evidence_page ON requirement_verification_evidence_sets(plan_id,created_at,id);
CREATE TABLE requirement_verification_evidence_objects (
 id uuid PRIMARY KEY, set_id uuid NOT NULL REFERENCES requirement_verification_evidence_sets(id),
 slot integer NOT NULL CHECK(slot BETWEEN 0 AND 19), attempt_id uuid NOT NULL,
 storage_key varchar(200) NOT NULL UNIQUE, destination_version varchar(300), state varchar(20) NOT NULL DEFAULT 'reserved',
 selected boolean NOT NULL DEFAULT false, byte_size integer, sha256 varchar(64), verified_at timestamptz,
 cleanup_checked_at timestamptz, cleanup_error varchar(50),
 CHECK(storage_key ~ '^verification-evidence-v1/[a-f0-9]{32}$'),
 CHECK(state IN ('reserved','verified','abandoned')),
 CHECK(NOT selected OR state='verified'),
 CHECK(state<>'verified' OR (byte_size BETWEEN 1 AND 20971520 AND sha256 ~ '^[a-f0-9]{64}$' AND length(destination_version)>0 AND destination_version<>'null' AND verified_at IS NOT NULL))
);
CREATE INDEX ix_requirement_verification_evidence_objects_set_id ON requirement_verification_evidence_objects(set_id);
CREATE UNIQUE INDEX uq_verification_evidence_selected ON requirement_verification_evidence_objects(set_id,slot) WHERE selected;

CREATE FUNCTION guard_verification_evidence_set() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p requirement_verification_plans; r requirement_verification_plan_revisions; e jsonb; f jsonb; n integer:=0; total bigint:=0;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Retain evidence history' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' THEN
  IF OLD.status='ready' OR OLD.status='cancelled' OR NEW.version<>OLD.version+1 OR
   (to_jsonb(NEW)-ARRAY['status','version','manifest','manifest_digest','completed_at','lease_id','lease_expires_at','safe_error']) IS DISTINCT FROM
   (to_jsonb(OLD)-ARRAY['status','version','manifest','manifest_digest','completed_at','lease_id','lease_expires_at','safe_error']) OR
   NEW.status NOT IN ('preparing','ready','failed','cancelled') THEN
   RAISE EXCEPTION 'Immutable evidence target or invalid transition' USING ERRCODE='23514';
  END IF;
 ELSE
  IF NEW.status<>'preparing' OR NEW.version<>1 OR NEW.manifest IS NOT NULL OR NEW.lease_id IS NOT NULL THEN RAISE EXCEPTION 'Invalid reservation' USING ERRCODE='23514'; END IF;
  NEW.created_at=transaction_timestamp();
 END IF;
 SELECT * INTO STRICT p FROM requirement_verification_plans WHERE id=NEW.plan_id;
 SELECT * INTO STRICT r FROM requirement_verification_plan_revisions WHERE plan_id=NEW.plan_id AND version=NEW.plan_version;
 IF p.project_id<>NEW.project_id OR p.subject_digest<>NEW.subject_digest OR r.checklist_digest<>NEW.checklist_digest OR
  (NEW.selection->>'plan_version')::integer<>NEW.plan_version OR NEW.selection->>'subject_digest'<>NEW.subject_digest OR NEW.selection->>'checklist_digest'<>NEW.checklist_digest THEN RAISE EXCEPTION 'Evidence target mismatch' USING ERRCODE='23514'; END IF;
 FOR e IN SELECT value FROM jsonb_array_elements(NEW.selection->'entries') LOOP
  IF NOT EXISTS(SELECT FROM jsonb_array_elements(r.checklist->'rows') row WHERE row->>'id'=e->>'row_id') OR
    NOT (e ? 'id' AND e ? 'kind' AND e ? 'reason') OR coalesce(length(trim(e->>'reason')),0)=0 OR coalesce(e->>'kind','') NOT IN ('execution','manual','not_applicable') THEN RAISE EXCEPTION 'Invalid evidence entry' USING ERRCODE='23514'; END IF;
 END LOOP;
 IF (SELECT count(DISTINCT value->>'id') FROM jsonb_array_elements(NEW.selection->'entries'))<>jsonb_array_length(NEW.selection->'entries') THEN RAISE EXCEPTION 'Duplicate evidence entry' USING ERRCODE='23514'; END IF;
 FOR f IN SELECT value FROM jsonb_array_elements(NEW.selection->'files') LOOP
  IF (f->>'slot')::integer IS DISTINCT FROM n OR coalesce((f->>'byte_size')::bigint,0) NOT BETWEEN 1 AND 20971520 OR coalesce(f->>'kind','') NOT IN ('test_evidence','document_attachment') OR coalesce(length(f->>'id'),0)=0 THEN RAISE EXCEPTION 'Invalid evidence file' USING ERRCODE='23514'; END IF;
  total=total+(f->>'byte_size')::bigint; n=n+1;
 END LOOP;
 IF total>104857600 THEN RAISE EXCEPTION 'Evidence byte limit' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER verification_evidence_set_guard BEFORE INSERT OR UPDATE OR DELETE ON requirement_verification_evidence_sets FOR EACH ROW EXECUTE FUNCTION guard_verification_evidence_set();

CREATE FUNCTION guard_verification_evidence_object() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE h requirement_verification_evidence_sets;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Retain copy attempt ledger' USING ERRCODE='23514'; END IF;
 SELECT * INTO STRICT h FROM requirement_verification_evidence_sets WHERE id=NEW.set_id FOR UPDATE;
 IF TG_OP='INSERT' THEN
  IF h.status<>'preparing' OR h.lease_id IS DISTINCT FROM NEW.attempt_id OR NEW.slot>=jsonb_array_length(h.selection->'files') OR NEW.state<>'reserved' OR NEW.selected THEN RAISE EXCEPTION 'Invalid object reservation' USING ERRCODE='23514'; END IF;
 ELSE
  IF (to_jsonb(NEW)-ARRAY['destination_version','state','selected','byte_size','sha256','verified_at','cleanup_checked_at','cleanup_error']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['destination_version','state','selected','byte_size','sha256','verified_at','cleanup_checked_at','cleanup_error']) OR
   (OLD.selected AND h.status='ready') OR
   (OLD.state='verified' AND (to_jsonb(NEW)-ARRAY['selected','state','cleanup_checked_at','cleanup_error']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['selected','state','cleanup_checked_at','cleanup_error'])) OR
   (OLD.state='abandoned' AND NEW.state<>'abandoned') OR
   (NEW.selected AND (h.status<>'preparing' OR h.lease_id IS DISTINCT FROM NEW.attempt_id OR h.lease_expires_at<=clock_timestamp())) THEN RAISE EXCEPTION 'Immutable or expired object attempt' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER verification_evidence_object_guard BEFORE INSERT OR UPDATE OR DELETE ON requirement_verification_evidence_objects FOR EACH ROW EXECUTE FUNCTION guard_verification_evidence_object();

CREATE FUNCTION check_verification_evidence_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target uuid; h requirement_verification_evidence_sets; f jsonb; o requirement_verification_evidence_objects; n integer;
BEGIN
 IF TG_TABLE_NAME='requirement_verification_evidence_sets' THEN target=NEW.id; ELSE target=NEW.set_id; END IF;
 SELECT * INTO STRICT h FROM requirement_verification_evidence_sets WHERE id=target;
 IF h.status<>'ready' THEN RETURN NULL; END IF;
 IF h.manifest IS NULL OR coalesce(h.manifest_digest,'') !~ '^[a-f0-9]{64}$' OR h.completed_at IS NULL OR h.lease_id IS NOT NULL OR
  (h.manifest-'files') IS DISTINCT FROM (h.selection-'files') OR jsonb_typeof(h.manifest->'files') IS DISTINCT FROM 'array' OR jsonb_array_length(h.manifest->'files')<>jsonb_array_length(h.selection->'files') THEN RAISE EXCEPTION 'Incomplete evidence manifest' USING ERRCODE='23514'; END IF;
 SELECT count(*) INTO n FROM requirement_verification_evidence_objects WHERE set_id=h.id AND selected;
 IF n<>jsonb_array_length(h.selection->'files') THEN RAISE EXCEPTION 'Incomplete selected objects' USING ERRCODE='23514'; END IF;
 FOR f IN SELECT value FROM jsonb_array_elements(h.manifest->'files') LOOP
  SELECT * INTO o FROM requirement_verification_evidence_objects WHERE set_id=h.id AND slot=(f->>'slot')::integer AND selected;
  IF NOT FOUND OR o.state<>'verified' OR (f-ARRAY['object_id','destination_version','sha256','verified_at']) IS DISTINCT FROM h.selection->'files'->o.slot OR f->>'object_id' IS DISTINCT FROM o.id::text OR f->>'sha256' IS DISTINCT FROM o.sha256 OR f->>'destination_version' IS DISTINCT FROM o.destination_version OR (f->>'byte_size')::integer IS DISTINCT FROM o.byte_size OR (f->>'verified_at')::timestamptz IS DISTINCT FROM o.verified_at THEN RAISE EXCEPTION 'Evidence object mismatch' USING ERRCODE='23514'; END IF;
 END LOOP;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER verification_evidence_set_complete AFTER INSERT OR UPDATE ON requirement_verification_evidence_sets DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_verification_evidence_complete();
CREATE CONSTRAINT TRIGGER verification_evidence_object_complete AFTER INSERT OR UPDATE ON requirement_verification_evidence_objects DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_verification_evidence_complete();
INSERT INTO permissions(code,description) VALUES ('requirement_verification:submit','固定要件へ検証証拠を提出する') ON CONFLICT(code) DO NOTHING;
INSERT INTO role_permissions(role_id,permission_id) SELECT r.id,p.id FROM roles r CROSS JOIN permissions p WHERE r.scope='project' AND r.key IN ('project_admin','manager','member') AND p.code='requirement_verification:submit' ON CONFLICT DO NOTHING;
DO $$ BEGIN
 IF EXISTS(SELECT FROM pg_roles WHERE rolname='syncnesto_app') THEN
 REVOKE ALL PRIVILEGES ON requirement_verification_evidence_sets,requirement_verification_evidence_objects FROM syncnesto_app;
 GRANT SELECT,INSERT ON requirement_verification_evidence_sets,requirement_verification_evidence_objects TO syncnesto_app;
 GRANT UPDATE(status,version,manifest,manifest_digest,completed_at,lease_id,lease_expires_at,safe_error) ON requirement_verification_evidence_sets TO syncnesto_app;
 GRANT UPDATE(destination_version,state,selected,byte_size,sha256,verified_at,cleanup_checked_at,cleanup_error) ON requirement_verification_evidence_objects TO syncnesto_app;
 END IF;
END $$;
