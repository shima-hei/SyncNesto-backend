CREATE TABLE requirement_verification_requests (
 id uuid PRIMARY KEY, project_id integer NOT NULL REFERENCES projects(id), source_requirement_id integer NOT NULL CHECK(source_requirement_id>0),
 plan_id uuid NOT NULL REFERENCES requirement_verification_plans(id), plan_version integer NOT NULL CHECK(plan_version>0),
 subject_digest varchar(64) NOT NULL CHECK(subject_digest ~ '^[a-f0-9]{64}$'), checklist_digest varchar(64) NOT NULL CHECK(checklist_digest ~ '^[a-f0-9]{64}$'),
 basis jsonb NOT NULL CHECK((jsonb_typeof(basis)='object' AND jsonb_typeof(basis->'entries')='array' AND jsonb_array_length(basis->'entries')<=1000 AND jsonb_typeof(basis->'files')='array' AND jsonb_array_length(basis->'files')<=20 AND octet_length(basis::text)<=2097152) IS TRUE),
 basis_digest varchar(64) NOT NULL CHECK(basis_digest ~ '^[a-f0-9]{64}$'),
 requested_by integer NOT NULL REFERENCES users(id), requester_name varchar(200) NOT NULL, judge_id integer NOT NULL REFERENCES users(id), judge_name varchar(200) NOT NULL,
 previous_request_id uuid REFERENCES requirement_verification_requests(id), reviewed_decision_id uuid REFERENCES requirement_verification_requests(id), reason text NOT NULL,
 request_id uuid NOT NULL, request_digest varchar(64) NOT NULL CHECK(request_digest ~ '^[a-f0-9]{64}$'), created_at timestamptz NOT NULL DEFAULT now(),
 status varchar(20) NOT NULL DEFAULT 'requested' CHECK(status IN ('requested','decided','cancelled','withdrawn')), version integer NOT NULL DEFAULT 1 CHECK(version>0),
 result varchar(20) CHECK(result IN ('passed','failed','not_applicable')), decided_by integer REFERENCES users(id), decision_reason text, decided_at timestamptz, decision_snapshot jsonb,
 review jsonb CHECK(review IS NULL OR (jsonb_typeof(review)='object' AND octet_length(review::text)<=65536)), review_version integer NOT NULL DEFAULT 0 CHECK(review_version>=0), lease_id uuid, lease_expires_at timestamptz,
 FOREIGN KEY(plan_id,plan_version) REFERENCES requirement_verification_plan_revisions(plan_id,version),
 CONSTRAINT uq_verdict_request_operation UNIQUE(project_id,requested_by,request_id),
 CONSTRAINT uq_verdict_successor UNIQUE(previous_request_id),
 CHECK(previous_request_id IS DISTINCT FROM id AND reviewed_decision_id IS DISTINCT FROM id),
 CHECK((status IN ('requested','cancelled') AND result IS NULL AND decided_by IS NULL AND decided_at IS NULL AND decision_reason IS NULL AND decision_snapshot IS NULL) OR
       (status IN ('decided','withdrawn') AND result IS NOT NULL AND decided_by=judge_id AND decided_at IS NOT NULL AND length(trim(decision_reason))>0 AND jsonb_typeof(decision_snapshot)='object' AND octet_length(decision_snapshot::text)<=2097152)),
 CHECK((lease_id IS NULL)=(lease_expires_at IS NULL))
);
CREATE UNIQUE INDEX uq_verdict_pending ON requirement_verification_requests(project_id,source_requirement_id) WHERE status='requested';
CREATE UNIQUE INDEX uq_verdict_root ON requirement_verification_requests(project_id,source_requirement_id) WHERE previous_request_id IS NULL;
CREATE INDEX ix_verdict_source ON requirement_verification_requests(project_id,source_requirement_id,created_at,id);
CREATE TABLE requirement_verification_events (
 id uuid PRIMARY KEY, request_id uuid NOT NULL REFERENCES requirement_verification_requests(id), version integer NOT NULL CHECK(version>0),
 event varchar(20) NOT NULL CHECK(event IN ('requested','decided','cancelled','withdrawn')),
 actor_id integer NOT NULL REFERENCES users(id), actor_name varchar(200) NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), reason text NOT NULL,
 operation_id uuid NOT NULL, operation_digest varchar(64) NOT NULL CHECK(operation_digest ~ '^[a-f0-9]{64}$'),
 snapshot jsonb NOT NULL CHECK((jsonb_typeof(snapshot)='object' AND octet_length(snapshot::text)<=2097152) IS TRUE),
 CONSTRAINT uq_verdict_event_version UNIQUE(request_id,version), CONSTRAINT uq_verdict_event_operation UNIQUE(request_id,operation_id)
);
COMMENT ON TABLE requirement_verification_requests IS '固定した確認対象と指定本人による要件の判断依頼';
COMMENT ON TABLE requirement_verification_events IS '依頼・判断・取消・撤回の追記履歴';

CREATE FUNCTION guard_verification_request() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p requirement_verification_plans; prev requirement_verification_requests; expected_decision uuid;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Verdict history is retained' USING ERRCODE='23514'; END IF;
 IF TG_OP='INSERT' THEN
  SELECT * INTO STRICT p FROM requirement_verification_plans WHERE id=NEW.plan_id;
  IF (p.project_id,p.source_requirement_id,p.subject_digest) IS DISTINCT FROM (NEW.project_id,NEW.source_requirement_id,NEW.subject_digest) OR
   NOT EXISTS(SELECT FROM requirement_verification_plan_revisions WHERE plan_id=NEW.plan_id AND version=NEW.plan_version AND checklist_digest=NEW.checklist_digest) OR
   NEW.status<>'requested' OR NEW.version<>1 OR NEW.result IS NOT NULL OR NEW.review IS NOT NULL OR NEW.review_version<>0 OR NEW.lease_id IS NOT NULL OR
   NEW.basis->>'subject_digest' IS DISTINCT FROM NEW.subject_digest OR NEW.basis->>'checklist_digest' IS DISTINCT FROM NEW.checklist_digest OR
   NEW.basis->>'plan_id' IS DISTINCT FROM NEW.plan_id::text OR (NEW.basis->>'plan_version')::integer IS DISTINCT FROM NEW.plan_version THEN
   RAISE EXCEPTION 'Invalid fixed verdict target' USING ERRCODE='23514';
  END IF;
  IF NEW.previous_request_id IS NOT NULL THEN
   SELECT * INTO STRICT prev FROM requirement_verification_requests WHERE id=NEW.previous_request_id FOR UPDATE;
   expected_decision=CASE WHEN prev.result IS NOT NULL THEN prev.id ELSE prev.reviewed_decision_id END;
   IF (prev.project_id,prev.source_requirement_id) IS DISTINCT FROM (NEW.project_id,NEW.source_requirement_id) OR prev.status='requested' OR NEW.reviewed_decision_id IS DISTINCT FROM expected_decision OR length(trim(NEW.reason))=0 THEN
    RAISE EXCEPTION 'Invalid review chain' USING ERRCODE='23514';
   END IF;
  ELSIF NEW.reviewed_decision_id IS NOT NULL THEN RAISE EXCEPTION 'Missing review chain' USING ERRCODE='23514'; END IF;
 ELSE
  IF (to_jsonb(NEW)-ARRAY['status','version','result','decided_by','decision_reason','decided_at','decision_snapshot','review','review_version','lease_id','lease_expires_at']) IS DISTINCT FROM
     (to_jsonb(OLD)-ARRAY['status','version','result','decided_by','decision_reason','decided_at','decision_snapshot','review','review_version','lease_id','lease_expires_at']) THEN
   RAISE EXCEPTION 'Immutable verdict basis' USING ERRCODE='23514'; END IF;
  IF NEW.status=OLD.status THEN
   IF OLD.status<>'requested' OR NEW.version<>OLD.version OR NEW.review_version<>OLD.review_version+1 OR
    (NEW.result,NEW.decided_by,NEW.decision_reason,NEW.decided_at,NEW.decision_snapshot) IS DISTINCT FROM (OLD.result,OLD.decided_by,OLD.decision_reason,OLD.decided_at,OLD.decision_snapshot) THEN
    RAISE EXCEPTION 'Invalid review progress' USING ERRCODE='23514'; END IF;
  ELSE
   IF NEW.version<>OLD.version+1 OR NOT((OLD.status='requested' AND NEW.status IN ('decided','cancelled')) OR (OLD.status='decided' AND NEW.status='withdrawn')) OR NEW.lease_id IS NOT NULL THEN
    RAISE EXCEPTION 'Invalid verdict transition' USING ERRCODE='23514'; END IF;
   IF OLD.status='decided' AND (NEW.result,NEW.decided_by,NEW.decision_reason,NEW.decided_at,NEW.decision_snapshot) IS DISTINCT FROM (OLD.result,OLD.decided_by,OLD.decision_reason,OLD.decided_at,OLD.decision_snapshot) THEN
    RAISE EXCEPTION 'Withdrawal preserves verdict' USING ERRCODE='23514'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER verification_request_guard BEFORE INSERT OR UPDATE OR DELETE ON requirement_verification_requests FOR EACH ROW EXECUTE FUNCTION guard_verification_request();

CREATE FUNCTION guard_verification_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE h requirement_verification_requests;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Append-only verdict event' USING ERRCODE='23514'; END IF;
 SELECT * INTO STRICT h FROM requirement_verification_requests WHERE id=NEW.request_id FOR UPDATE;
 IF NEW.version<>h.version OR NEW.event<>h.status OR
  (NEW.event='requested' AND NEW.actor_id<>h.requested_by) OR (NEW.event IN ('decided','withdrawn') AND NEW.actor_id<>h.judge_id) OR
  NEW.snapshot->>'basis_digest' IS DISTINCT FROM h.basis_digest OR NEW.snapshot->>'result' IS DISTINCT FROM h.result OR
  NEW.snapshot->'decision' IS DISTINCT FROM coalesce(h.decision_snapshot,'null'::jsonb) THEN
  RAISE EXCEPTION 'Verdict event mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER verification_event_guard BEFORE INSERT OR UPDATE OR DELETE ON requirement_verification_events FOR EACH ROW EXECUTE FUNCTION guard_verification_event();

CREATE FUNCTION check_verification_request_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target uuid; h requirement_verification_requests; n integer;
BEGIN
 IF TG_TABLE_NAME='requirement_verification_requests' THEN target=NEW.id; ELSE target=NEW.request_id; END IF;
 SELECT * INTO STRICT h FROM requirement_verification_requests WHERE id=target;
 SELECT count(*) INTO n FROM requirement_verification_events WHERE request_id=target;
 IF n<>h.version OR NOT EXISTS(SELECT FROM requirement_verification_events WHERE request_id=target AND version=h.version AND event=h.status AND snapshot->>'basis_digest'=h.basis_digest AND snapshot->>'result' IS NOT DISTINCT FROM h.result AND snapshot->'decision' IS NOT DISTINCT FROM coalesce(h.decision_snapshot,'null'::jsonb)) THEN
  RAISE EXCEPTION 'Incomplete verdict history' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER verification_request_complete AFTER INSERT OR UPDATE ON requirement_verification_requests DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_verification_request_complete();
CREATE CONSTRAINT TRIGGER verification_event_complete AFTER INSERT OR UPDATE ON requirement_verification_events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_verification_request_complete();
INSERT INTO permissions(code,description) VALUES ('requirement_verification:decide','指定された固定要件の最終合否を判断する') ON CONFLICT(code) DO NOTHING;
INSERT INTO role_permissions(role_id,permission_id) SELECT r.id,p.id FROM roles r CROSS JOIN permissions p WHERE r.scope='project' AND r.key IN ('project_admin','manager','member') AND p.code='requirement_verification:decide' ON CONFLICT DO NOTHING;
DO $$ BEGIN
 IF EXISTS(SELECT FROM pg_roles WHERE rolname='syncnesto_app') THEN
 REVOKE ALL PRIVILEGES ON requirement_verification_requests,requirement_verification_events FROM syncnesto_app;
 GRANT SELECT,INSERT ON requirement_verification_requests,requirement_verification_events TO syncnesto_app;
 GRANT UPDATE(status,version,result,decided_by,decision_reason,decided_at,decision_snapshot,review,review_version,lease_id,lease_expires_at) ON requirement_verification_requests TO syncnesto_app;
 END IF;
END $$;
