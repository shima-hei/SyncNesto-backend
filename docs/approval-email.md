# 承認メール（Phase 2B-3b、LOCAL限定）

本人がONにした後の新規承認通知だけを、既存Mailpitへ配送する。業務判断・既読・公開資格の正本は既存DBで、メールはそれらを更新しない。実SMTP/Resendへのfallbackはない。公開・デモ・審査・MCP経由では有効にしない。

## 本人設定API

`GET /notification-preferences` と `PATCH /notification-preferences`。通常セッション・MFAが必要。組織の選択や新permissionは不要で、ゲストも自分の設定だけを操作できる。

GETは未作成なら書き込まず次を返す。

```json
{"fixed_approval_email_enabled":false,"version":0,"available":false,"pause_reason":null}
```

PATCH本文は `fixed_approval_email_enabled`（厳密bool）と `version`（厳密int）のみ。他人のuser_id/emailを受け付けない。初回version0、以降は返却されたversionを使い、古い版は409 VERSION_CONFLICT/current。設定画面で取得した本人IDを `X-Syncnesto-Expected-User` に必須指定する。これは認証源ではなく、古いタブと現在Cookieの本人不一致を403で拒否するための比較値。Cookie/CSRF/Origin検証を維持する。

全応答に `Cache-Control: private, no-store` と `Referrer-Policy: no-referrer`。宛先文字列は返さず、保存はUser/emailの用途分離HMAC。email変更後の旧ONは実効OFF/identity_changedとして扱い、本人が再選択する。再ONや再公開で旧配送を復活させない。

## LOCAL起動

既存DBを保全・復元検証し、`uv run --frozen alembic upgrade head` で空の3表を追加する。既存設定を保持してBackend/Frontend server側に `FIXED_APPROVAL_EMAIL_ENABLED=true` を明示する。既存 `FIXED_APPROVAL_NOTIFICATIONS_ENABLED` も必要。Frontendの既存通知2画面に設定欄が現れる。新NEXT_PUBLIC変数は不要。

既存 `LOCAL_EMAIL_CAPTURE_RECIPIENTS` へ明示ダミーだけを追加する。LOCALのloopback DB・Mailpit（127.0.0.1:1025）・Frontend originだけを許す。workerを公開サービスとして起動しない。

```bash
uv run --frozen python -m scripts.deliver_approval_mail status
uv run --frozen python -m scripts.deliver_approval_mail run --once
uv run --frozen python -m scripts.deliver_approval_mail run
```

15秒ごとに1件を処理する。複数worker/resolveはadvisory lockで排他。Ctrl-C/SIGTERMで停止し、配送子processを回収する。CLIは件数と安全な結果だけを表示する。OS自動起動設定は追加しない。

## 配送と障害

新規notification INSERTのRETURNINGだけを同じ業務transactionで配送待ちへ保存する。通知・配送待ちの保存障害は全体rollback、SMTP障害は保存済み判断を変更しない。外部4種の固定承認も対象。種類/固定名/出来事だけを本文へ含め、件名は固定文、リンクは通知画面のみ。

送信直前に現在の本人・設定version・宛先指紋・閲覧資格・公開/参加/許可世代・期限・対象状態を再検証する。送信中は資格・設定の共有ロックを保持し、設定OFFや資格更新が最大10秒のSMTP工程を待つ場合がある。ロック喪失/期限では停止する。SMTPは期限付き子processで実行する。

開始前lease失効は再claim可能、開始耐久化後の失効はunknown。明確な4xxは1/5/30分（最大4試行）、5xxは停止、24時間/資格期限を超えた便は抑止。DATA受付不明は自動再送しない。SMTPの2xx受付は本人の受信・閲覧を証明しない。

## 結果不明の手動確認

workerを止め、lease終了後のunknownだけを期待version付きで解決する。理由と根拠は所有者だけが読める8192 bytes以下のJSONファイル（symlink不可）にする。

```bash
uv run --frozen python -m scripts.deliver_approval_mail resolve <配送UUID> --version <版> --outcome accepted --reason 'Mailpit実体を確認' --evidence-file /absolute/private/evidence.json
```

根拠は `attempt_id` と期待 `message_id`（`<sha256(approval-mail/配送UUID)@example.com>`）を必須にする。acceptedは `mailpit_id` のrawメールをloopback8025から取得しMessage-IDを照合する。not-acceptedは運用者が確認した `basis:"smtp_rejection", smtp_code:400〜599, reply_received:true` が必要で、受信一覧にないことだけでは再送しない。後者は再試行候補に戻すが、送信前に資格/設定/24時間を再検証する。根拠digestと理由をlocal_operatorとして追記し、過去の試行や人の承認を改変しない。

## 停止・戻し方

workerを終了して両serverの新flagをfalseにする。3表を保持したまま互換コードへ戻せる。既存通知/承認/招待・認証は継続する。設定または配送履歴があるDBのdowngradeは拒否し、履歴を自動破棄しない。DB全体復元は新規履歴を失う別の復旧手段。届いたメールは回収しない。
