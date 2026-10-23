-- 固定本文を変えず、参加資格・公開許可・追記履歴を追加する。
CREATE OR REPLACE FUNCTION guard_artifact_publication_header() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE rel artifact_releases; pre artifact_preservations;
      BEGIN
        IF TG_OP='UPDATE' AND OLD.sealed THEN
          IF (to_jsonb(NEW)-ARRAY['publication_state','publication_version'])<>
             (to_jsonb(OLD)-ARRAY['publication_state','publication_version']) THEN
            RAISE EXCEPTION 'publication body is immutable' USING ERRCODE='55000';
          END IF;
          IF NEW.publication_state=OLD.publication_state OR
             NEW.publication_version<>OLD.publication_version+1 THEN
            RAISE EXCEPTION 'only publication state may change' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END IF;
        IF TG_OP='DELETE' THEN
          RAISE EXCEPTION 'publication must be retained' USING ERRCODE='55000';
        END IF;
        IF TG_OP='UPDATE' AND (OLD.sealed OR NOT NEW.sealed OR
            (to_jsonb(NEW)-'sealed')<>(to_jsonb(OLD)-'sealed')) THEN
          RAISE EXCEPTION 'publication is immutable' USING ERRCODE='55000';
        END IF;
        IF TG_OP='INSERT' AND NEW.sealed THEN
          RAISE EXCEPTION 'publication must be completed before sealing' USING ERRCODE='55000';
        END IF;
        SELECT * INTO rel FROM artifact_releases WHERE id=NEW.release_id;
        IF NOT FOUND OR NOT rel.sealed OR rel.project_id<>NEW.project_id OR
           rel.content_digest<>NEW.body_digest OR NEW.source_kinds<>rel.source_kinds OR
           (NEW.source_manifest->>'release_id') IS DISTINCT FROM rel.id::text OR
           (NEW.source_manifest->>'body_digest') IS DISTINCT FROM rel.content_digest OR
           (NEW.source_manifest->'sources') IS DISTINCT FROM rel.source_refs OR
           (NEW.source_manifest->>'preservation_id') IS DISTINCT FROM NEW.preservation_id::text THEN
          RAISE EXCEPTION 'publication source does not match' USING ERRCODE='23514';
        END IF;
        IF NEW.preservation_id IS NOT NULL THEN
          SELECT * INTO pre FROM artifact_preservations WHERE id=NEW.preservation_id;
          IF NOT FOUND OR pre.release_id<>rel.id OR pre.status<>'ready' OR
             (NEW.source_manifest->>'preservation_digest') IS DISTINCT FROM pre.manifest_digest OR
             jsonb_array_length(NEW.file_manifest)<>jsonb_array_length(pre.manifest->'files') OR EXISTS(
               SELECT 1 FROM jsonb_array_elements(NEW.file_manifest) WITH ORDINALITY f(item,pos)
               WHERE (f.item-'id') IS DISTINCT FROM
                 ((pre.manifest->'files'->(f.pos::integer-1))-
                   ARRAY['document_id','attachment_id','document_version'])
             ) THEN
            RAISE EXCEPTION 'publication preservation does not match' USING ERRCODE='23514';
          END IF;
        ELSIF jsonb_array_length(NEW.file_manifest)<>0 THEN
          RAISE EXCEPTION 'publication files require preservation' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$;
ALTER TABLE artifact_publications
  ADD COLUMN publication_state varchar(20) NOT NULL DEFAULT 'prepared',
  ADD COLUMN publication_version integer NOT NULL DEFAULT 1,
  ADD CONSTRAINT uq_artifact_publication_project UNIQUE(project_id,id),
  ADD CONSTRAINT ck_artifact_publication_state CHECK
    (publication_state IN ('prepared','published','withdrawn') AND publication_version>0);
CREATE INDEX ix_artifact_release_node_source ON artifact_release_nodes(node_kind,source_id,release_id);

CREATE TABLE project_guest_participations (
	id UUID NOT NULL, 
	project_id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	generation INTEGER NOT NULL, 
	display_label VARCHAR(255) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	version INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, 
	created_by INTEGER NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	revoked_by INTEGER, 
	revocation_reason VARCHAR(1000), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_guest_participation_project UNIQUE (project_id, id), 
	CONSTRAINT uq_guest_generation UNIQUE (project_id, user_id, generation), 
	CONSTRAINT ck_guest_state CHECK (status IN ('active','revoked') AND generation>0 AND version>0), 
	FOREIGN KEY(project_id) REFERENCES projects (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(created_by) REFERENCES users (id), 
	FOREIGN KEY(revoked_by) REFERENCES users (id)
);
CREATE INDEX ix_project_guest_participations_project_id ON project_guest_participations (project_id);
CREATE INDEX ix_project_guest_participations_user_id ON project_guest_participations (user_id);
CREATE UNIQUE INDEX uq_guest_active_generation ON project_guest_participations (project_id, user_id) WHERE status='active';

CREATE TABLE artifact_publication_grants (
	id UUID NOT NULL, 
	project_id INTEGER NOT NULL, 
	publication_id UUID NOT NULL, 
	participation_id UUID NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	version INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, 
	created_by INTEGER NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	revoked_by INTEGER, 
	revocation_reason VARCHAR(1000), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_publication_grant_project UNIQUE (project_id, id), 
	CONSTRAINT fk_grant_publication_project FOREIGN KEY(project_id, publication_id) REFERENCES artifact_publications (project_id, id), 
	CONSTRAINT fk_grant_participation_project FOREIGN KEY(project_id, participation_id) REFERENCES project_guest_participations (project_id, id), 
	CONSTRAINT ck_publication_grant_state CHECK (status IN ('active','revoked') AND version>0), 
	FOREIGN KEY(created_by) REFERENCES users (id), 
	FOREIGN KEY(revoked_by) REFERENCES users (id)
);
CREATE INDEX ix_artifact_publication_grants_participation_id ON artifact_publication_grants (participation_id);
CREATE INDEX ix_artifact_publication_grants_project_id ON artifact_publication_grants (project_id);
CREATE INDEX ix_artifact_publication_grants_publication_id ON artifact_publication_grants (publication_id);
CREATE UNIQUE INDEX uq_publication_active_grant ON artifact_publication_grants (publication_id, participation_id) WHERE status='active';

CREATE TABLE artifact_publication_events (
	id UUID NOT NULL, 
	project_id INTEGER NOT NULL, 
	publication_id UUID, 
	participation_id UUID, 
	grant_id UUID, 
	event_kind VARCHAR(50) NOT NULL, 
	actor_id INTEGER, 
	actor_kind VARCHAR(20) NOT NULL, 
	occurred_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, 
	recorded_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, 
	reason VARCHAR(1000), 
	target_state VARCHAR(20), 
	target_version INTEGER, 
	public_digest VARCHAR(64), 
	operation_id UUID, 
	is_root BOOLEAN DEFAULT 'false' NOT NULL, 
	request_id UUID, 
	request_digest VARCHAR(64), 
	result JSONB, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_publication_event_project FOREIGN KEY(project_id, publication_id) REFERENCES artifact_publications (project_id, id), 
	CONSTRAINT fk_participation_event_project FOREIGN KEY(project_id, participation_id) REFERENCES project_guest_participations (project_id, id), 
	CONSTRAINT fk_grant_event_project FOREIGN KEY(project_id, grant_id) REFERENCES artifact_publication_grants (project_id, id), 
	FOREIGN KEY(actor_id) REFERENCES users (id), 
	FOREIGN KEY(operation_id) REFERENCES artifact_publication_events (id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX ix_artifact_publication_events_project_id ON artifact_publication_events (project_id);

CREATE FUNCTION publication_lock(target integer, reading boolean DEFAULT false) RETURNS void
LANGUAGE plpgsql VOLATILE AS $$ BEGIN
  IF current_setting('transaction_isolation')<>'read committed' THEN
    RAISE EXCEPTION 'publication access requires a fresh transaction' USING ERRCODE='40001';
  END IF;
  PERFORM pg_advisory_xact_lock_shared(1398362932,0);
  IF reading THEN PERFORM pg_advisory_xact_lock_shared(1398362931,target);
  ELSE PERFORM pg_advisory_xact_lock(1398362931,target); END IF;
END $$;

CREATE FUNCTION publication_assert_lock(target integer) RETURNS void LANGUAGE plpgsql VOLATILE AS $$ BEGIN
  IF current_setting('transaction_isolation')<>'read committed' OR NOT EXISTS(
      SELECT FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory' AND granted
        AND classid=1398362931 AND objid=target AND objsubid=2 AND mode='ExclusiveLock'
    ) OR NOT EXISTS(
      SELECT FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory' AND granted
        AND classid=1398362932 AND objid=0 AND objsubid=2
    ) THEN
    RAISE EXCEPTION 'publication boundary required before row writes' USING ERRCODE='55000';
  END IF;
END $$;

CREATE FUNCTION publication_sources_alive(target uuid) RETURNS boolean LANGUAGE sql VOLATILE AS $$
 SELECT EXISTS(SELECT FROM artifact_publications p
   JOIN projects pr ON pr.id=p.project_id JOIN tenants t ON t.id=pr.tenant_id
   WHERE p.id=target AND p.sealed AND pr.deleted_at IS NULL AND t.status='active')
 AND NOT EXISTS(
   SELECT FROM artifact_publications p JOIN artifact_release_nodes n ON n.release_id=p.release_id
   WHERE p.id=target AND CASE n.node_kind
     WHEN 'requirement_document' THEN NOT EXISTS(SELECT FROM requirement_documents d WHERE d.id=n.source_id AND d.project_id=p.project_id AND d.deleted_at IS NULL)
     WHEN 'section' THEN NOT EXISTS(SELECT FROM requirement_sections s JOIN requirement_documents d ON d.id=s.document_id WHERE s.id=n.source_id AND d.project_id=p.project_id AND s.deleted_at IS NULL AND d.deleted_at IS NULL)
     WHEN 'requirement' THEN NOT EXISTS(SELECT FROM requirements r JOIN requirement_documents d ON d.id=r.document_id WHERE r.id=n.source_id AND d.project_id=p.project_id AND r.deleted_at IS NULL AND d.deleted_at IS NULL)
     WHEN 'specification' THEN NOT EXISTS(SELECT FROM specifications s WHERE s.id=n.source_id AND s.project_id=p.project_id AND s.deleted_at IS NULL)
     WHEN 'test_design' THEN NOT EXISTS(SELECT FROM test_designs s WHERE s.id=n.source_id AND s.project_id=p.project_id AND s.deleted_at IS NULL)
     ELSE false END
 ) AND NOT EXISTS(
   SELECT FROM artifact_publications p JOIN artifact_preservations pre ON pre.id=p.preservation_id,
     jsonb_array_elements(pre.selection) f
   WHERE p.id=target AND NOT EXISTS(
     SELECT FROM document_attachments a JOIN project_documents d ON d.id=a.document_id
     WHERE a.id=(f->>'attachment_id')::uuid AND d.id=(f->>'document_id')::integer
       AND d.project_id=p.project_id AND a.deleted_at IS NULL AND d.deleted_at IS NULL
   )
 )
$$;

CREATE FUNCTION publication_add_event(target integer, pub uuid, part uuid, grant_identity uuid,
  kind text, state text, version integer, why text DEFAULT NULL) RETURNS void
LANGUAGE plpgsql VOLATILE AS $$ BEGIN
  INSERT INTO artifact_publication_events(id,project_id,publication_id,participation_id,grant_id,
    event_kind,actor_id,actor_kind,reason,target_state,target_version,public_digest,operation_id,is_root)
  VALUES(gen_random_uuid(),target,pub,part,grant_identity,kind,
    nullif(current_setting('syncnesto.publication_actor',true),'')::integer,
    CASE WHEN nullif(current_setting('syncnesto.publication_actor',true),'') IS NULL THEN 'system_guard' ELSE 'user' END,
    why,state,version,(SELECT public_digest FROM artifact_publications WHERE id=pub),
    nullif(current_setting('syncnesto.publication_operation',true),'')::uuid,false);
END $$;

CREATE FUNCTION publication_event_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'publication history must be retained' USING ERRCODE='55000'; END IF;
  IF NEW.actor_kind NOT IN ('user','system_guard') OR
     (NEW.actor_kind='user')<>(NEW.actor_id IS NOT NULL) OR
     (NEW.is_root AND (NEW.actor_id IS NULL OR NEW.request_id IS NULL OR NEW.request_digest IS NULL OR NEW.result IS NULL OR NEW.operation_id IS DISTINCT FROM NEW.id)) THEN
    RAISE EXCEPTION 'invalid publication event' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER publication_event_retention BEFORE INSERT OR UPDATE OR DELETE ON artifact_publication_events
FOR EACH ROW EXECUTE FUNCTION publication_event_immutable();

CREATE FUNCTION publication_qualification_boundary() RETURNS trigger LANGUAGE plpgsql VOLATILE AS $$ BEGIN
  IF current_setting('transaction_isolation')<>'read committed' THEN
    RAISE EXCEPTION 'qualification change requires a fresh transaction' USING ERRCODE='40001';
  END IF;
  PERFORM pg_advisory_xact_lock(1398362932,0);
  RETURN NULL;
END $$;

CREATE FUNCTION publication_state_change() RETURNS trigger LANGUAGE plpgsql VOLATILE AS $$ BEGIN
  IF TG_OP='INSERT' THEN
    IF NEW.publication_state<>'prepared' OR NEW.publication_version<>1 THEN
      RAISE EXCEPTION 'new publication must be prepared' USING ERRCODE='23514';
    END IF;
  ELSIF NEW.publication_state<>OLD.publication_state THEN
    PERFORM publication_assert_lock(NEW.project_id);
    IF NOT OLD.sealed OR NEW.publication_version<>OLD.publication_version+1 OR
       NOT ((OLD.publication_state='prepared' AND NEW.publication_state IN ('published','withdrawn')) OR
            (OLD.publication_state='published' AND NEW.publication_state='withdrawn')) OR
       (NEW.publication_state='published' AND NOT publication_sources_alive(NEW.id)) THEN
      RAISE EXCEPTION 'invalid publication transition' USING ERRCODE='23514';
    END IF;
    PERFORM publication_add_event(NEW.project_id,NEW.id,NULL,NULL,
      'publication_'||NEW.publication_state,NEW.publication_state,NEW.publication_version,
      nullif(current_setting('syncnesto.publication_reason',true),''));
    IF NEW.publication_state='withdrawn' THEN
      UPDATE artifact_publication_grants SET status='revoked',version=version+1,
        revoked_at=clock_timestamp(),revoked_by=nullif(current_setting('syncnesto.publication_actor',true),'')::integer,
        revocation_reason=coalesce(nullif(current_setting('syncnesto.publication_reason',true),''),'publication_withdrawn')
      WHERE publication_id=NEW.id AND status='active';
    END IF;
  ELSIF NEW.publication_version<>OLD.publication_version THEN
    RAISE EXCEPTION 'publication version only changes with state' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER publication_state_event AFTER INSERT OR UPDATE ON artifact_publications
FOR EACH ROW EXECUTE FUNCTION publication_state_change();

CREATE FUNCTION publication_participation_guard() RETURNS trigger LANGUAGE plpgsql VOLATILE AS $$
DECLARE owner_tenant integer;
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'participation must be retained' USING ERRCODE='55000'; END IF;
  PERFORM publication_lock(NEW.project_id);
  SELECT tenant_id INTO owner_tenant FROM projects WHERE id=NEW.project_id AND deleted_at IS NULL;
  IF TG_OP='INSERT' THEN
    IF NEW.status<>'active' OR NEW.version<>1 OR NEW.expires_at<=clock_timestamp() OR owner_tenant IS NULL OR
       NOT EXISTS(SELECT FROM tenants WHERE id=owner_tenant AND status='active') OR
       NOT EXISTS(SELECT FROM users WHERE id=NEW.user_id AND is_active AND deleted_at IS NULL) OR
       EXISTS(SELECT FROM review_account_grants WHERE user_id=NEW.user_id) OR
       EXISTS(SELECT FROM tenant_members WHERE tenant_id=owner_tenant AND user_id=NEW.user_id AND status='active') OR
       NEW.generation<>(SELECT coalesce(max(generation),0)+1 FROM project_guest_participations WHERE project_id=NEW.project_id AND user_id=NEW.user_id) THEN
      RAISE EXCEPTION 'invalid guest participation' USING ERRCODE='23514';
    END IF;
  ELSE
    IF (to_jsonb(NEW)-ARRAY['status','version','revoked_at','revoked_by','revocation_reason'])<>
       (to_jsonb(OLD)-ARRAY['status','version','revoked_at','revoked_by','revocation_reason']) OR
       OLD.status<>'active' OR NEW.status<>'revoked' OR NEW.version<>OLD.version+1 OR
       NEW.revoked_at IS NULL OR coalesce(btrim(NEW.revocation_reason),'')='' THEN
      RAISE EXCEPTION 'participation cannot be reassigned or revived' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER participation_boundary BEFORE INSERT OR UPDATE OR DELETE ON project_guest_participations
FOR EACH STATEMENT EXECUTE FUNCTION publication_qualification_boundary();
CREATE TRIGGER participation_guard BEFORE INSERT OR UPDATE OR DELETE ON project_guest_participations
FOR EACH ROW EXECUTE FUNCTION publication_participation_guard();

CREATE FUNCTION publication_participation_event() RETURNS trigger LANGUAGE plpgsql VOLATILE AS $$ BEGIN
  PERFORM publication_add_event(NEW.project_id,NULL,NEW.id,NULL,
    CASE WHEN TG_OP='INSERT' THEN 'participation_created' ELSE 'participation_revoked' END,
    NEW.status,NEW.version,NEW.revocation_reason);
  IF NEW.status='revoked' THEN
    UPDATE artifact_publication_grants SET status='revoked',version=version+1,
      revoked_at=clock_timestamp(),revoked_by=NEW.revoked_by,revocation_reason=NEW.revocation_reason
    WHERE participation_id=NEW.id AND status='active';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER participation_event AFTER INSERT OR UPDATE ON project_guest_participations
FOR EACH ROW EXECUTE FUNCTION publication_participation_event();

CREATE FUNCTION publication_grant_guard() RETURNS trigger LANGUAGE plpgsql VOLATILE AS $$
DECLARE part project_guest_participations;
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'grant must be retained' USING ERRCODE='55000'; END IF;
  PERFORM publication_assert_lock(NEW.project_id);
  IF TG_OP='INSERT' THEN
    SELECT * INTO part FROM project_guest_participations WHERE id=NEW.participation_id;
    IF NOT FOUND OR part.project_id<>NEW.project_id OR part.status<>'active' OR
       part.expires_at<=clock_timestamp() OR NEW.expires_at>part.expires_at OR NEW.expires_at<=clock_timestamp() OR
       NEW.status<>'active' OR NEW.version<>1 OR NOT EXISTS(
         SELECT FROM artifact_publications WHERE id=NEW.publication_id AND project_id=NEW.project_id AND publication_state='published'
       ) OR NOT publication_sources_alive(NEW.publication_id) OR
       NOT EXISTS(SELECT FROM users WHERE id=part.user_id AND is_active AND deleted_at IS NULL) OR
       EXISTS(SELECT FROM review_account_grants WHERE user_id=part.user_id) OR
       EXISTS(SELECT FROM tenant_members tm JOIN projects pr ON pr.tenant_id=tm.tenant_id
         WHERE pr.id=NEW.project_id AND tm.user_id=part.user_id AND tm.status='active') THEN
      RAISE EXCEPTION 'invalid current publication grant' USING ERRCODE='23514';
    END IF;
  ELSE
    IF (to_jsonb(NEW)-ARRAY['status','version','revoked_at','revoked_by','revocation_reason'])<>
       (to_jsonb(OLD)-ARRAY['status','version','revoked_at','revoked_by','revocation_reason']) OR
       OLD.status<>'active' OR NEW.status<>'revoked' OR NEW.version<>OLD.version+1 OR
       NEW.revoked_at IS NULL OR coalesce(btrim(NEW.revocation_reason),'')='' THEN
      RAISE EXCEPTION 'grant cannot be reassigned or revived' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER publication_grant_guard BEFORE INSERT OR UPDATE OR DELETE ON artifact_publication_grants
FOR EACH ROW EXECUTE FUNCTION publication_grant_guard();
CREATE FUNCTION publication_grant_event() RETURNS trigger LANGUAGE plpgsql VOLATILE AS $$ BEGIN
  PERFORM publication_add_event(NEW.project_id,NEW.publication_id,NEW.participation_id,NEW.id,
    CASE WHEN TG_OP='INSERT' THEN 'grant_created' ELSE 'grant_revoked' END,
    NEW.status,NEW.version,NEW.revocation_reason);
  RETURN NEW;
END $$;
CREATE TRIGGER publication_grant_event AFTER INSERT OR UPDATE ON artifact_publication_grants
FOR EACH ROW EXECUTE FUNCTION publication_grant_event();

CREATE FUNCTION publication_stop_project(target integer, why text, participants boolean DEFAULT false) RETURNS void
LANGUAGE plpgsql VOLATILE AS $$ BEGIN
  PERFORM publication_lock(target);
  PERFORM set_config('syncnesto.publication_reason',why,true);
  UPDATE artifact_publications SET publication_state='withdrawn',publication_version=publication_version+1
    WHERE project_id=target AND publication_state IN ('prepared','published');
  IF participants THEN
    UPDATE project_guest_participations SET status='revoked',version=version+1,
      revoked_at=clock_timestamp(),revocation_reason=why
    WHERE project_id=target AND status='active';
  END IF;
END $$;

CREATE FUNCTION publication_qualification_changed() RETURNS trigger LANGUAGE plpgsql VOLATILE AS $$
DECLARE target integer; affected_user integer; owner_tenant integer;
BEGIN
  IF TG_TABLE_NAME='users' THEN
    IF TG_OP<>'DELETE' AND NEW.is_active AND NEW.deleted_at IS NULL THEN RETURN NULL; END IF;
    affected_user:=OLD.id;
  ELSIF TG_TABLE_NAME='tenant_members' THEN
    IF TG_OP='DELETE' OR NEW.status<>'active' THEN RETURN NULL; END IF;
    affected_user:=NEW.user_id; owner_tenant:=NEW.tenant_id;
  ELSIF TG_TABLE_NAME='tenants' THEN
    IF TG_OP<>'DELETE' AND NEW.status='active' THEN RETURN NULL; END IF;
    FOR target IN SELECT id FROM projects WHERE tenant_id=OLD.id ORDER BY id LOOP
      PERFORM publication_stop_project(target,'tenant_unavailable',true);
    END LOOP;
    RETURN NULL;
  ELSE
    IF TG_OP<>'DELETE' AND NEW.deleted_at IS NULL THEN RETURN NULL; END IF;
    PERFORM publication_stop_project(OLD.id,'project_deleted',true);
    RETURN NULL;
  END IF;
  FOR target IN SELECT DISTINCT gp.project_id FROM project_guest_participations gp JOIN projects pr ON pr.id=gp.project_id
      WHERE gp.user_id=affected_user AND gp.status='active' AND (owner_tenant IS NULL OR pr.tenant_id=owner_tenant)
      ORDER BY gp.project_id LOOP
    PERFORM publication_lock(target);
    UPDATE project_guest_participations SET status='revoked',version=version+1,
      revoked_at=clock_timestamp(),revocation_reason=CASE WHEN owner_tenant IS NULL THEN 'user_unavailable' ELSE 'internal_membership_created' END
    WHERE project_id=target AND user_id=affected_user AND status='active';
  END LOOP;
  RETURN NULL;
END $$;

CREATE FUNCTION publication_source_boundary() RETURNS trigger LANGUAGE plpgsql VOLATILE AS $$ BEGIN
  IF current_setting('transaction_isolation')<>'read committed' THEN
    RAISE EXCEPTION 'source mutation requires a fresh transaction' USING ERRCODE='40001';
  END IF;
  PERFORM pg_advisory_xact_lock_shared(1398362932,0);
  RETURN NULL;
END $$;
CREATE FUNCTION publication_source_changed() RETURNS trigger LANGUAGE plpgsql VOLATILE AS $$
DECLARE target integer; target_sql text;
BEGIN
  IF TG_OP='UPDATE' THEN
    IF NOT EXISTS(SELECT FROM source_old o JOIN source_new n USING(id)
        WHERE o.deleted_at IS NULL AND n.deleted_at IS NOT NULL) THEN RETURN NULL; END IF;
  END IF;
  target_sql:=CASE
    WHEN TG_TABLE_NAME IN ('requirements','requirement_sections') THEN
      'SELECT DISTINCT d.project_id FROM source_old o JOIN requirement_documents d ON d.id=o.document_id ORDER BY d.project_id'
    WHEN TG_TABLE_NAME='document_attachments' THEN
      'SELECT DISTINCT d.project_id FROM source_old o JOIN project_documents d ON d.id=o.document_id ORDER BY d.project_id'
    ELSE 'SELECT DISTINCT project_id FROM source_old ORDER BY project_id' END;
  FOR target IN EXECUTE target_sql LOOP
    PERFORM publication_lock(target);
    PERFORM set_config('syncnesto.publication_reason','source_deleted',true);
    UPDATE artifact_publications SET publication_state='withdrawn',publication_version=publication_version+1
      WHERE project_id=target AND publication_state IN ('prepared','published') AND NOT publication_sources_alive(id);
  END LOOP;
  RETURN NULL;
END $$;
DO $$ DECLARE tab text; BEGIN
  FOREACH tab IN ARRAY ARRAY['requirement_documents','requirement_sections','requirements',
      'specifications','test_designs','project_documents','document_attachments'] LOOP
    EXECUTE format('CREATE TRIGGER publication_source_boundary BEFORE UPDATE OR DELETE ON %I FOR EACH STATEMENT EXECUTE FUNCTION publication_source_boundary()',tab);
    EXECUTE format('CREATE TRIGGER publication_source_updated AFTER UPDATE ON %I REFERENCING OLD TABLE AS source_old NEW TABLE AS source_new FOR EACH STATEMENT EXECUTE FUNCTION publication_source_changed()',tab);
    EXECUTE format('CREATE TRIGGER publication_source_deleted AFTER DELETE ON %I REFERENCING OLD TABLE AS source_old FOR EACH STATEMENT EXECUTE FUNCTION publication_source_changed()',tab);
  END LOOP;
END $$;

CREATE FUNCTION publication_references_source(target uuid, kind text, source_identity text) RETURNS boolean LANGUAGE sql VOLATILE AS $$
 SELECT EXISTS(SELECT FROM artifact_publications p JOIN artifact_release_nodes n ON n.release_id=p.release_id
   WHERE p.id=target AND (
     (n.node_kind=kind AND n.source_id::text=source_identity) OR
     (kind='requirement_document' AND n.node_kind='requirement' AND EXISTS(SELECT FROM requirements r WHERE r.id=n.source_id AND r.document_id::text=source_identity)) OR
     (kind='section' AND n.node_kind='requirement' AND EXISTS(SELECT FROM requirements r WHERE r.id=n.source_id AND r.section_id::text=source_identity))
   )) OR EXISTS(SELECT FROM artifact_publications p JOIN artifact_preservations pre ON pre.id=p.preservation_id,
     jsonb_array_elements(pre.selection) f
   WHERE p.id=target AND ((kind='document' AND f->>'document_id'=source_identity) OR
     (kind='attachment' AND f->>'attachment_id'=source_identity)))
$$;

INSERT INTO permissions(code,description) VALUES ('artifact_publication:publish','固定内容の本人別公開許可・停止を管理する') ON CONFLICT(code) DO NOTHING;
INSERT INTO role_permissions(role_id,permission_id)
  SELECT r.id,p.id FROM roles r CROSS JOIN permissions p WHERE r.scope='project'
    AND r.key IN ('project_admin','manager') AND p.code='artifact_publication:publish' ON CONFLICT DO NOTHING;

DO $$ DECLARE tab text; BEGIN
  FOREACH tab IN ARRAY ARRAY['users','tenants','projects','tenant_members'] LOOP
    EXECUTE format('CREATE TRIGGER publication_qualification_boundary BEFORE INSERT OR UPDATE OR DELETE ON %I FOR EACH STATEMENT EXECUTE FUNCTION publication_qualification_boundary()',tab);
    EXECUTE format('CREATE TRIGGER publication_qualification_changed AFTER INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION publication_qualification_changed()',tab);
  END LOOP;
END $$;
CREATE UNIQUE INDEX uq_publication_operation_request ON artifact_publication_events (project_id, actor_id, request_id) WHERE is_root;

