
CREATE TABLE project_guest_invitations (
	id UUID NOT NULL,
	project_id INTEGER NOT NULL,
	recipient_email VARCHAR(255) NOT NULL,
	recipient_id INTEGER,
	display_label VARCHAR(255) NOT NULL,
	created_by INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	participation_expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	status VARCHAR(20) NOT NULL,
	version INTEGER NOT NULL,
	token_generation INTEGER NOT NULL,
	token_hash VARCHAR(64) NOT NULL,
	delivery_state VARCHAR(20) NOT NULL,
	sent_at TIMESTAMP WITH TIME ZONE,
	provider_message_id VARCHAR(255),
	accepted_by INTEGER,
	participation_id UUID,
	request_id UUID NOT NULL,
	request_digest VARCHAR(64) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES projects (id),
	FOREIGN KEY(recipient_id) REFERENCES users (id),
	FOREIGN KEY(created_by) REFERENCES users (id),
	FOREIGN KEY(accepted_by) REFERENCES users (id),
	FOREIGN KEY(participation_id) REFERENCES project_guest_participations (id)
)

;
CREATE INDEX ix_project_guest_invitations_project_id ON project_guest_invitations (project_id);

CREATE TABLE project_guest_invitation_checks (
	id UUID NOT NULL,
	project_id INTEGER NOT NULL,
	invitation_id UUID NOT NULL,
	token_generation INTEGER NOT NULL,
	proof_hash VARCHAR(64) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	consumed_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES projects (id),
	FOREIGN KEY(invitation_id) REFERENCES project_guest_invitations (id),
	UNIQUE (proof_hash)
)

;
CREATE INDEX ix_project_guest_invitation_checks_invitation_id ON project_guest_invitation_checks (invitation_id);

CREATE TABLE project_guest_invitation_events (
	id UUID NOT NULL,
	project_id INTEGER NOT NULL,
	invitation_id UUID NOT NULL,
	kind VARCHAR(40) NOT NULL,
	actor_id INTEGER,
	actor_kind VARCHAR(30) NOT NULL,
	occurred_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL,
	operation_id UUID NOT NULL,
	request_digest VARCHAR(64) NOT NULL,
	is_root BOOLEAN NOT NULL,
	version INTEGER NOT NULL,
	state VARCHAR(20) NOT NULL,
	reason VARCHAR(1000),
	result JSONB NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES projects (id),
	FOREIGN KEY(invitation_id) REFERENCES project_guest_invitations (id),
	FOREIGN KEY(actor_id) REFERENCES users (id)
)

;
CREATE INDEX ix_project_guest_invitation_events_invitation_id ON project_guest_invitation_events (invitation_id);
ALTER TABLE project_guest_invitations ADD CONSTRAINT uq_guest_invitation_project UNIQUE(project_id,id);
ALTER TABLE project_guest_invitations ADD CONSTRAINT uq_guest_invitation_request UNIQUE(project_id,created_by,request_id);
ALTER TABLE project_guest_invitations ADD CONSTRAINT ck_guest_invitation_state CHECK(status IN ('pending','accepted','declined','revoked','expired') AND version>0 AND token_generation>0 AND delivery_state IN ('sending','sent','failed','unknown'));
ALTER TABLE project_guest_invitations ADD CONSTRAINT ck_guest_invitation_hash CHECK(token_hash ~ '^[a-f0-9]{64}$' AND request_digest ~ '^[a-f0-9]{64}$');
ALTER TABLE project_guest_invitation_checks ADD CONSTRAINT ck_guest_check_hash CHECK(proof_hash ~ '^[a-f0-9]{64}$');
ALTER TABLE project_guest_invitation_events ADD CONSTRAINT ck_guest_event_digest CHECK(request_digest ~ '^[a-f0-9]{64}$');
ALTER TABLE project_guest_invitations ADD CONSTRAINT fk_invitation_participation_project FOREIGN KEY(project_id,participation_id) REFERENCES project_guest_participations(project_id,id);
CREATE UNIQUE INDEX uq_guest_invitation_pending ON project_guest_invitations(project_id,lower(recipient_email)) WHERE status='pending';
ALTER TABLE project_guest_invitation_checks ADD CONSTRAINT fk_guest_check_project FOREIGN KEY(project_id,invitation_id) REFERENCES project_guest_invitations(project_id,id);
ALTER TABLE project_guest_invitation_events ADD CONSTRAINT fk_guest_event_project FOREIGN KEY(project_id,invitation_id) REFERENCES project_guest_invitations(project_id,id);
CREATE UNIQUE INDEX uq_guest_invitation_operation ON project_guest_invitation_events(invitation_id,operation_id) WHERE is_root;

CREATE FUNCTION guard_guest_invitation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE part project_guest_participations;
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'invitation must be retained' USING ERRCODE='55000'; END IF;
  IF TG_OP='INSERT' THEN
    IF NEW.status<>'pending' OR NEW.version<>1 OR NEW.token_generation<>1 OR NEW.expires_at<=clock_timestamp() OR NEW.participation_expires_at<NEW.expires_at OR NEW.accepted_by IS NOT NULL OR NEW.participation_id IS NOT NULL THEN
      RAISE EXCEPTION 'invalid invitation' USING ERRCODE='23514'; END IF;
  ELSE
    IF OLD.status<>'pending' OR (to_jsonb(NEW)-ARRAY['status','version','token_generation','token_hash','delivery_state','sent_at','provider_message_id','recipient_id','accepted_by','participation_id'])<>(to_jsonb(OLD)-ARRAY['status','version','token_generation','token_hash','delivery_state','sent_at','provider_message_id','recipient_id','accepted_by','participation_id']) OR
       NEW.version<>OLD.version+1 OR NEW.token_generation NOT BETWEEN OLD.token_generation AND OLD.token_generation+1 OR
       (OLD.recipient_id IS NOT NULL AND NEW.recipient_id IS DISTINCT FROM OLD.recipient_id) OR
       ((NEW.token_hash<>OLD.token_hash)<>(NEW.token_generation=OLD.token_generation+1)) THEN
      RAISE EXCEPTION 'invalid invitation transition' USING ERRCODE='23514'; END IF;
  END IF;
  IF NEW.status='accepted' THEN
    IF NEW.expires_at<=clock_timestamp() OR NEW.participation_expires_at<=clock_timestamp() THEN
      RAISE EXCEPTION 'invitation expired' USING ERRCODE='23514'; END IF;
    SELECT * INTO part FROM project_guest_participations WHERE id=NEW.participation_id;
    IF part.id IS NULL OR NEW.accepted_by IS DISTINCT FROM NEW.recipient_id OR NEW.accepted_by IS DISTINCT FROM part.user_id OR part.project_id<>NEW.project_id OR part.status<>'active' OR part.expires_at<>NEW.participation_expires_at THEN
      RAISE EXCEPTION 'invalid invitation acceptance' USING ERRCODE='23514'; END IF;
  ELSIF NEW.accepted_by IS NOT NULL OR NEW.participation_id IS NOT NULL THEN
    RAISE EXCEPTION 'unexpected participation' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER guest_invitation_guard BEFORE INSERT OR UPDATE OR DELETE ON project_guest_invitations FOR EACH ROW EXECUTE FUNCTION guard_guest_invitation();

CREATE FUNCTION guard_guest_invitation_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE invitation project_guest_invitations;
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'confirmation must be retained' USING ERRCODE='55000'; END IF;
  IF TG_OP='UPDATE' THEN
    IF OLD.consumed_at IS NOT NULL OR NEW.consumed_at IS NULL OR (to_jsonb(NEW)-'consumed_at')<>(to_jsonb(OLD)-'consumed_at') THEN
      RAISE EXCEPTION 'invalid confirmation transition' USING ERRCODE='23514'; END IF;
  ELSE
    SELECT * INTO invitation FROM project_guest_invitations WHERE id=NEW.invitation_id;
    IF invitation.status<>'pending' OR invitation.token_generation<>NEW.token_generation OR NEW.expires_at<=clock_timestamp() OR NEW.expires_at>invitation.expires_at OR NEW.expires_at>clock_timestamp()+interval '30 minutes' OR NEW.consumed_at IS NOT NULL THEN
      RAISE EXCEPTION 'invalid confirmation' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER guest_invitation_check_guard BEFORE INSERT OR UPDATE OR DELETE ON project_guest_invitation_checks FOR EACH ROW EXECUTE FUNCTION guard_guest_invitation_check();

CREATE FUNCTION guard_guest_invitation_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'invitation events must be retained' USING ERRCODE='55000'; END IF;
  IF NEW.actor_kind NOT IN ('user','email_recipient','delivery') OR (NEW.actor_kind='user' AND NEW.actor_id IS NULL) THEN
    RAISE EXCEPTION 'invalid invitation actor' USING ERRCODE='23514'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER guest_invitation_event_guard BEFORE INSERT OR UPDATE OR DELETE ON project_guest_invitation_events FOR EACH ROW EXECUTE FUNCTION guard_guest_invitation_event();
