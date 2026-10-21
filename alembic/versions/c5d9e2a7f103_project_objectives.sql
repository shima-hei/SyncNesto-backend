ALTER TABLE requirement_documents
  ADD COLUMN project_goal text,
  ADD COLUMN project_scope text,
  ADD COLUMN project_completion_criteria text,
  ADD CONSTRAINT uq_requirement_document_identity UNIQUE(project_id,id),
  ADD CONSTRAINT ck_document_goal_length CHECK(char_length(project_goal)<=20000),
  ADD CONSTRAINT ck_document_scope_length CHECK(char_length(project_scope)<=20000),
  ADD CONSTRAINT ck_document_completion_length CHECK(char_length(project_completion_criteria)<=20000);
ALTER TABLE projects
  ADD COLUMN goal_document_id integer,
  ADD COLUMN goal_source_version integer NOT NULL DEFAULT 1,
  ADD CONSTRAINT ck_project_goal_version CHECK(goal_source_version>0),
  ADD CONSTRAINT fk_project_goal_document FOREIGN KEY(id,goal_document_id)
    REFERENCES requirement_documents(project_id,id);
CREATE TABLE project_goal_source_events (
  id serial PRIMARY KEY,
  project_id integer NOT NULL REFERENCES projects(id),
  previous_document_id integer,
  document_id integer,
  previous_version integer NOT NULL,
  version integer NOT NULL,
  document_version integer,
  reason text,
  changed_by integer NOT NULL REFERENCES users(id),
  changed_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_goal_event_previous FOREIGN KEY(project_id,previous_document_id) REFERENCES requirement_documents(project_id,id),
  CONSTRAINT fk_goal_event_document FOREIGN KEY(project_id,document_id) REFERENCES requirement_documents(project_id,id),
  CONSTRAINT uq_goal_source_event_version UNIQUE(project_id,version),
  CONSTRAINT ck_goal_event_changed CHECK(previous_document_id IS DISTINCT FROM document_id),
  CONSTRAINT ck_goal_event_version CHECK(previous_version>0 AND version=previous_version+1),
  CONSTRAINT ck_goal_event_document_version CHECK(
    (document_id IS NULL AND document_version IS NULL) OR
    (document_id IS NOT NULL AND document_version IS NOT NULL AND document_version>0)),
  CONSTRAINT ck_goal_event_reason CHECK(char_length(reason)<=2000)
);
CREATE INDEX ix_goal_source_event_page ON project_goal_source_events(project_id,id);
CREATE FUNCTION protect_goal_source_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Goal source history must be retained' USING ERRCODE='23514';
END $$;
CREATE TRIGGER goal_source_event_immutable BEFORE UPDATE OR DELETE ON project_goal_source_events
  FOR EACH ROW EXECUTE FUNCTION protect_goal_source_event();
INSERT INTO permissions(code,description) VALUES('project:manage_goal_source','案件共通の目標参照文書を指定する');
INSERT INTO role_permissions(role_id,permission_id)
SELECT r.id,p.id FROM roles r CROSS JOIN permissions p
WHERE r.scope='project' AND r.key IN ('project_admin','manager') AND p.code='project:manage_goal_source';
COMMENT ON TABLE project_goal_source_events IS '目標出典選択履歴：本文・承認とは別の案件共通参照の変更履歴';
COMMENT ON COLUMN requirement_documents.project_goal IS '案件ゴール：案件で実現することの正本';
COMMENT ON COLUMN requirement_documents.project_scope IS '案件スコープ：案件の対象範囲・対象外';
COMMENT ON COLUMN requirement_documents.project_completion_criteria IS '案件完了条件：案件の完了を判断する条件';
COMMENT ON COLUMN projects.goal_document_id IS '目標参照文書：概要に参照する同案件の要件定義書';
COMMENT ON COLUMN projects.goal_source_version IS '目標参照版：参照選択専用の排他制御版';
