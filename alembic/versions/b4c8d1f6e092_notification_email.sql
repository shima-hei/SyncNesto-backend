CREATE TABLE notification_email_preferences (
  user_id integer PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  enabled boolean NOT NULL DEFAULT false, email_digest varchar(64),
  version integer NOT NULL DEFAULT 1, updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT ck_email_preference CHECK(version>0 AND (NOT enabled OR email_digest IS NOT NULL))
);
CREATE TABLE notification_email_deliveries (
  id uuid PRIMARY KEY, notification_id integer NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
  preference_version integer NOT NULL, email_digest varchar(64) NOT NULL,
  template_version integer NOT NULL DEFAULT 1, content_digest varchar(64) NOT NULL,
  state varchar(24) NOT NULL DEFAULT 'pending', version integer NOT NULL DEFAULT 1,
  attempt_count integer NOT NULL DEFAULT 0, claim_token uuid, lease_until timestamptz,
  next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(), expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT uq_email_delivery_notification UNIQUE(notification_id),
  CONSTRAINT ck_email_delivery_state CHECK(state IN ('pending','leased','sending','retry_wait','accepted','unknown','failed','suppressed') AND version>0 AND attempt_count>=0 AND preference_version>0)
);
CREATE INDEX ix_email_delivery_due ON notification_email_deliveries(next_attempt_at,id) WHERE state IN ('pending','retry_wait');
CREATE TABLE notification_email_attempts (
  id uuid PRIMARY KEY, delivery_id uuid NOT NULL REFERENCES notification_email_deliveries(id) ON DELETE CASCADE,
  attempt_id uuid NOT NULL, record_kind varchar(16) NOT NULL, outcome varchar(24), safe_code varchar(64),
  provider_message_id varchar(255), operator_kind varchar(24) NOT NULL, reason varchar(500),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT uq_email_attempt_record UNIQUE(attempt_id,record_kind),
  CONSTRAINT ck_email_attempt_record CHECK(record_kind IN ('started','completed','resolution') AND operator_kind IN ('worker','local_operator'))
);
CREATE INDEX ix_notification_email_attempts_delivery_id ON notification_email_attempts(delivery_id);
COMMENT ON TABLE notification_email_preferences IS '本人の承認通知メール選択。初期OFF';
COMMENT ON TABLE notification_email_deliveries IS '業務判断とは独立した配送待ち';
COMMENT ON TABLE notification_email_attempts IS '送信試行の追記記録。本文・宛先なし';
