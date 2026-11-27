"""Allow explicitly editing the expiry of a still-active guest participation."""

from alembic import op

revision = "5fa16d2c870b"
down_revision = "8b31a94f6c20"


def _replace_functions(*, allow_expiry: bool) -> None:
    expiry = (
        """
    IF OLD.status='active' AND NEW.status='active' THEN
      IF (to_jsonb(NEW)-ARRAY['expires_at','version'])<>
         (to_jsonb(OLD)-ARRAY['expires_at','version']) OR
         NEW.version<>OLD.version+1 OR
         least(OLD.expires_at,NEW.expires_at)<=clock_timestamp() OR owner_tenant IS NULL
      THEN RAISE EXCEPTION 'invalid guest expiry change' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END IF;
    """
        if allow_expiry
        else ""
    )
    active_event = (
        "WHEN NEW.status='active' THEN 'participation_expiry_changed'"
        if allow_expiry
        else ""
    )
    op.execute(f"""
    CREATE OR REPLACE FUNCTION publication_participation_guard()
    RETURNS trigger LANGUAGE plpgsql VOLATILE AS $$
    DECLARE owner_tenant integer;
    BEGIN
      IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'participation must be retained' USING ERRCODE='55000';
      END IF;
      PERFORM publication_lock(NEW.project_id);
      SELECT tenant_id INTO owner_tenant FROM projects
      WHERE id=NEW.project_id AND deleted_at IS NULL;
      IF TG_OP='INSERT' THEN
        IF NEW.status<>'active' OR NEW.version<>1 OR
           NEW.expires_at<=clock_timestamp() OR owner_tenant IS NULL OR
           NOT EXISTS(SELECT FROM tenants WHERE id=owner_tenant AND status='active') OR
           NOT EXISTS(SELECT FROM users WHERE id=NEW.user_id AND is_active AND deleted_at IS NULL) OR
           EXISTS(SELECT FROM review_account_grants WHERE user_id=NEW.user_id) OR
           EXISTS(SELECT FROM tenant_members WHERE tenant_id=owner_tenant
                  AND user_id=NEW.user_id AND status='active') OR
           NEW.generation<>(SELECT coalesce(max(generation),0)+1
                            FROM project_guest_participations
                            WHERE project_id=NEW.project_id AND user_id=NEW.user_id) THEN
          RAISE EXCEPTION 'invalid guest participation' USING ERRCODE='23514';
        END IF;
      ELSE
        {expiry}
        IF (to_jsonb(NEW)-ARRAY['status','version','revoked_at','revoked_by','revocation_reason'])<>
           (to_jsonb(OLD)-ARRAY['status','version','revoked_at','revoked_by','revocation_reason']) OR
           OLD.status<>'active' OR NEW.status<>'revoked' OR NEW.version<>OLD.version+1 OR
           NEW.revoked_at IS NULL OR coalesce(btrim(NEW.revocation_reason),'')='' THEN
          RAISE EXCEPTION 'participation cannot be reassigned or revived' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$;

    CREATE OR REPLACE FUNCTION publication_participation_event()
    RETURNS trigger LANGUAGE plpgsql VOLATILE AS $$
    BEGIN
      PERFORM publication_add_event(NEW.project_id,NULL,NEW.id,NULL,
        CASE WHEN TG_OP='INSERT' THEN 'participation_created'
             {active_event} ELSE 'participation_revoked' END,
        NEW.status,NEW.version,NEW.revocation_reason);
      IF NEW.status='revoked' THEN
        UPDATE artifact_publication_grants SET status='revoked',version=version+1,
          revoked_at=clock_timestamp(),revoked_by=NEW.revoked_by,
          revocation_reason=NEW.revocation_reason
        WHERE participation_id=NEW.id AND status='active';
      END IF;
      RETURN NEW;
    END $$;
    """)


def upgrade() -> None:
    """Allow versioned changes while suppressing resurrection of expired generations."""
    _replace_functions(allow_expiry=True)


def downgrade() -> None:
    """Disable edits while retaining the resulting expiry and audit history."""
    _replace_functions(allow_expiry=False)
