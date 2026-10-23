# Project限定のゲスト招待（Phase 2B-1）

この段階はLOCALの明示ダミーだけ。`GUEST_INVITATIONS_ENABLED=true` と `LOCAL_EMAIL_CAPTURE_RECIPIENTS=ux-...@example.com` の完全一致リストを必要とする。既存メール設定を変更せず、対象宛先の招待・通常MFA通知だけ127.0.0.1:1025へ捕捉する。非対象の既存メールは従来adapter。公開環境の捕捉設定、遠隔捕捉先、非ダミーallowlistは拒否する。送信失敗時に遠隔へfallbackしない。

内部 `/projects/{project_id}/guest-invitations` のGET/POST、`/{id}/resend` POST、`/{id}/events` GETは既存 `project:invite_member`。`/{id}/revoke` POSTは既存 `project:remove_member`。既存組織管理の経路も再検査する。新permission/role割当なし。POSTはrequest_id、更新時version、取消時reasonを送る。送信受付は受諾を意味しない。再送は旧token/確認Cookieを失効し、招待期限/参加期限を延長しない。結果不明を自動再送しない。

受信者の `/guest-invitations/{id}/inspect` POSTはfragmentのtokenを一度だけ送る。確認後の別乱数は `guest_invitation_check` HttpOnly Cookie、Path `/api/guest-invitations/{id}`、SameSite=Lax、既存Secure設定、最長1799秒へ交換する。生tokenはURLから直ちに消し、next/query/QueryClient/永続下書き/logへ保存しない。管理APIは生tokenを返さない。

`/{id}` GETは確認Cookieと現在sessionから次の操作を案内する。`/{id}/register` POSTは未登録の招待メール確認者自身が氏名/12〜128文字passwordを指定する。通常のlogin/TOTPまたはpasskey/復旧コード確認を完了してから `/{id}/accept` POSTで参加する。`/{id}/decline` POSTはメール確認だけでも辞退可能で、未ログインならemail_recipientとして記録し、認証済み本人と偽らない。公開入口は選択Tenantを送らず、POSTは設定Frontendの正確なOrigin、認証/MFA Cookieがあれば従来CSRFも要求する。422は入力原文を含まない。

受諾時には招待/確認期限・世代・宛先本人・通常MFA・招待者の現在権限・Project/組織/Identityの状態を再検査する。同じ組織の社内所属者はゲスト参加しない。別組織internalのUser区分/他所属は維持する。曖昧な大小文字のIdentity候補を新規登録/自動紐付けしない。active参加があれば期限切れでも明示失効を必要とする。再参加は新世代で、古い公開許可を復活させない。

通常LOCALの既存 `MFA_REQUIRED=false` は保持する。新しい受諾ではこの全体flagと独立して追加認証済みsessionを必須にする。ログイン済みで未検証なら新GETのnext_step=mfaを表示し、既存 `/auth/mfa/reauth` と `/mfa` で設定/確認する。既存の任意MFA再認証はsessionのsecurity_versionを複写しないため、その場合だけ5分以内の再認証を認める。認証器/認証情報変更時の既存session一括失効と、確定時のsession/本人再照合を維持する。通常login/MFAのAPIや設定は変更しない。

受諾は参加資格のみ。TenantMember/ProjectMember/role/grant/承認権限を追加しない。公開資料と承認は別工程。各URLはprivate,no-store/no-referrer。feature停止時は新操作を拒否し、内部一覧・履歴と既存の共有閲覧/参加失効を保持する。証跡があるmigrationのdowngradeは拒否し、停止flagで戻す。
