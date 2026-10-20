CREATE TABLE fixed_approval_guest_bindings (
  approval_id uuid PRIMARY KEY REFERENCES fixed_approval_requests(id),
  project_id integer NOT NULL REFERENCES projects(id),
  publication_id uuid NOT NULL,
  public_target_node_id uuid NOT NULL,
  participation_id uuid NOT NULL,
  grant_id uuid NOT NULL,
  projection_version varchar(50) NOT NULL,
  public_scope_digest varchar(64) NOT NULL,
  public_context jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(project_id,publication_id) REFERENCES artifact_publications(project_id,id),
  FOREIGN KEY(project_id,participation_id) REFERENCES project_guest_participations(project_id,id),
  FOREIGN KEY(project_id,grant_id) REFERENCES artifact_publication_grants(project_id,id),
  FOREIGN KEY(publication_id,public_target_node_id) REFERENCES artifact_publication_nodes(publication_id,id)
);
CREATE INDEX ix_fixed_approval_guest_bindings_project_id ON fixed_approval_guest_bindings(project_id);
CREATE INDEX ix_fixed_approval_guest_bindings_publication_id ON fixed_approval_guest_bindings(publication_id);
CREATE INDEX ix_fixed_approval_guest_bindings_participation_id ON fixed_approval_guest_bindings(participation_id);
CREATE INDEX ix_fixed_approval_guest_bindings_grant_id ON fixed_approval_guest_bindings(grant_id);
COMMENT ON TABLE fixed_approval_guest_bindings IS '外部固定承認の対応：判断正本と公開内容・本人許可の不変な接続';
ALTER TABLE fixed_approval_requests DROP CONSTRAINT ck_fixed_approval_kind,
 ADD CONSTRAINT ck_fixed_approval_kind CHECK(target_kind IN ('requirement','requirement_document','specification','test_design'));

CREATE FUNCTION guest_approval_has_access(target uuid, account integer) RETURNS boolean
LANGUAGE sql VOLATILE AS $$
 SELECT EXISTS(
  SELECT FROM fixed_approval_guest_bindings b JOIN fixed_approval_requests a ON a.id=b.approval_id
  JOIN artifact_publications p ON p.id=b.publication_id AND p.project_id=b.project_id
  JOIN artifact_publication_grants g ON g.id=b.grant_id AND g.project_id=b.project_id
    AND g.publication_id=p.id AND g.participation_id=b.participation_id
  JOIN project_guest_participations gp ON gp.id=b.participation_id AND gp.project_id=b.project_id
  JOIN users u ON u.id=gp.user_id JOIN projects pr ON pr.id=b.project_id JOIN tenants t ON t.id=pr.tenant_id
  WHERE b.approval_id=target AND a.approver_id=account AND gp.user_id=account
   AND p.sealed AND p.publication_state='published' AND gp.status='active' AND g.status='active'
   AND g.expires_at>clock_timestamp() AND gp.expires_at>clock_timestamp()
   AND u.is_active AND u.deleted_at IS NULL AND NOT u.password_change_required
   AND pr.deleted_at IS NULL AND t.status='active' AND publication_sources_alive(p.id)
   AND NOT EXISTS(SELECT FROM tenant_members tm WHERE tm.tenant_id=pr.tenant_id AND tm.user_id=account AND tm.status='active')
   AND NOT EXISTS(SELECT FROM review_account_grants rg WHERE rg.user_id=account)
 );
$$;

CREATE FUNCTION guest_approval_binding_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a fixed_approval_requests; p artifact_publications; gp project_guest_participations; g artifact_publication_grants;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'guest approval binding must be retained' USING ERRCODE='55000'; END IF;
 SELECT * INTO a FROM fixed_approval_requests WHERE id=NEW.approval_id;
 SELECT * INTO p FROM artifact_publications WHERE id=NEW.publication_id;
 SELECT * INTO gp FROM project_guest_participations WHERE id=NEW.participation_id;
 SELECT * INTO g FROM artifact_publication_grants WHERE id=NEW.grant_id;
 PERFORM publication_assert_lock(NEW.project_id);
 IF a.id IS NULL OR a.sealed OR a.policy_version<>'fixed-approval-guest-v1' OR a.status<>'requested'
   OR a.project_id IS DISTINCT FROM NEW.project_id OR p.project_id IS DISTINCT FROM NEW.project_id
   OR a.release_id IS DISTINCT FROM p.release_id OR a.preservation_id IS DISTINCT FROM p.preservation_id
   OR gp.user_id IS DISTINCT FROM a.approver_id OR g.publication_id IS DISTINCT FROM p.id
   OR g.participation_id IS DISTINCT FROM gp.id OR NEW.projection_version IS DISTINCT FROM p.projection_version
   OR p.source_manifest->'id_map'->>NEW.public_target_node_id::text IS DISTINCT FROM a.target_node_id::text
   OR NEW.public_scope_digest IS DISTINCT FROM a.scope_manifest->>'public_scope_digest'
   OR NEW.public_context->>'public_digest' IS DISTINCT FROM p.public_digest THEN
   RAISE EXCEPTION 'invalid guest approval binding' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guest_approval_binding_retention BEFORE INSERT OR UPDATE OR DELETE ON fixed_approval_guest_bindings
 FOR EACH ROW EXECUTE FUNCTION guest_approval_binding_guard();

CREATE FUNCTION guest_approval_check_complete(target uuid) RETURNS void LANGUAGE plpgsql AS $$
DECLARE a fixed_approval_requests; b fixed_approval_guest_bindings;
BEGIN
 SELECT * INTO a FROM fixed_approval_requests WHERE id=target;
 SELECT * INTO b FROM fixed_approval_guest_bindings WHERE approval_id=target;
 IF a.policy_version='fixed-approval-guest-v1' THEN
   IF b.approval_id IS NULL OR b.project_id IS DISTINCT FROM a.project_id
     OR b.public_scope_digest IS DISTINCT FROM a.scope_manifest->>'public_scope_digest'
     OR b.public_context->>'request_note' IS DISTINCT FROM a.request_note
     OR b.public_context->>'title' IS DISTINCT FROM a.scope_manifest->>'title'
     OR b.public_context->>'target_kind' IS DISTINCT FROM a.target_kind
     OR b.public_context->>'target_node_id' IS DISTINCT FROM b.public_target_node_id::text
     OR b.public_context->'exception_count' IS DISTINCT FROM a.scope_manifest->'exception_count'
     OR jsonb_array_length(b.public_context->'bases') IS DISTINCT FROM (a.scope_manifest->>'basis_count')::integer
     OR EXISTS(SELECT FROM fixed_approval_requirement_bases rb JOIN artifact_release_nodes rn ON rn.id=rb.target_node_id AND rn.release_id=rb.release_id
        WHERE rb.approval_id=a.id AND NOT EXISTS(
          SELECT FROM jsonb_array_elements(b.public_context->'bases') pb
          JOIN artifact_publication_nodes pn ON pn.id=(pb->>'node_id')::uuid AND pn.publication_id=b.publication_id
          JOIN artifact_publications pp ON pp.id=b.publication_id
          WHERE pp.source_manifest->'id_map'->>pn.id::text=rb.target_node_id::text
            AND pb->>'state'=rb.state_at_request AND pb->>'reason' IS NOT DISTINCT FROM rb.reason
            AND pb->>'title'=rn.payload->>'title'))
     OR (a.status='requested' AND NOT guest_approval_has_access(a.id,a.approver_id)) THEN
     RAISE EXCEPTION 'incomplete external approval' USING ERRCODE='55000';
   END IF;
 ELSIF b.approval_id IS NOT NULL THEN
   RAISE EXCEPTION 'internal approval cannot have guest binding' USING ERRCODE='55000';
 END IF;
END $$;
