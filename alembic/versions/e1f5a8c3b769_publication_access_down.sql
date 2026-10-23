DO $$ DECLARE tab text; BEGIN
  FOREACH tab IN ARRAY ARRAY['requirement_documents','requirement_sections','requirements','specifications','test_designs','project_documents','document_attachments'] LOOP
    EXECUTE format('DROP TRIGGER publication_source_boundary ON %I',tab);
    EXECUTE format('DROP TRIGGER publication_source_updated ON %I',tab);
    EXECUTE format('DROP TRIGGER publication_source_deleted ON %I',tab);
  END LOOP;
  FOREACH tab IN ARRAY ARRAY['users','tenants','projects','tenant_members'] LOOP
    EXECUTE format('DROP TRIGGER publication_qualification_boundary ON %I',tab);
    EXECUTE format('DROP TRIGGER publication_qualification_changed ON %I',tab);
  END LOOP;
END $$;
DROP TRIGGER publication_state_event ON artifact_publications;
DROP TABLE artifact_publication_events;
DROP TABLE artifact_publication_grants;
DROP TABLE project_guest_participations;
DROP FUNCTION publication_state_change();
DROP FUNCTION publication_participation_guard();
DROP FUNCTION publication_participation_event();
DROP FUNCTION publication_grant_guard();
DROP FUNCTION publication_grant_event();
DROP FUNCTION publication_event_immutable();
DROP FUNCTION publication_qualification_changed();
DROP FUNCTION publication_qualification_boundary();
DROP FUNCTION publication_source_changed();
DROP FUNCTION publication_source_boundary();
DROP FUNCTION publication_stop_project(integer,text,boolean);
DROP FUNCTION publication_add_event(integer,uuid,uuid,uuid,text,text,integer,text);
DROP FUNCTION publication_sources_alive(uuid);
DROP FUNCTION publication_references_source(uuid,text,text);
DROP FUNCTION publication_assert_lock(integer);
DROP FUNCTION publication_lock(integer,boolean);
DROP INDEX ix_artifact_release_node_source;
ALTER TABLE artifact_publications DROP CONSTRAINT uq_artifact_publication_project,
  DROP CONSTRAINT ck_artifact_publication_state, DROP COLUMN publication_state, DROP COLUMN publication_version;
CREATE OR REPLACE FUNCTION guard_artifact_publication_header() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE rel artifact_releases; pre artifact_preservations;
      BEGIN
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
DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code='artifact_publication:publish');
DELETE FROM permissions WHERE code='artifact_publication:publish';

