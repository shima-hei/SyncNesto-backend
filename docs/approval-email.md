# 承認メール

本人がONにした後の新規承認通知だけを配送する。本番は既存EMAIL_PROVIDER/SMTP設定、LOCALは明示ダミー向け既存Mailpitを使う。業務判断・既読・公開資格の正本は既存DBで、メールはそれらを更新しない。デモ・審査・MCP経由では有効にしない。一時リリースフラグ・新サービス・新しい環境変数は追加しない。

## 本人設定API

`GET /notification-preferences` と `PATCH /notification-preferences`。通常セッション・MFAが必要。組織の選択や新permissionは不要で、ゲストも自分の設定だけを操作できる。

GETは未作成なら書き込まず次を返す。

```json
{"fixed_approval_email_enabled":false,"version":0,"available":false,"pause_reason":null}
```

PATCH本文は `fixed_approval_email_enabled`（厳密bool）と `version`（厳密int）のみ。他人のuser_id/emailを受け付けない。初回version0、以降は返却されたversionを使い、古い版は409 VERSION_CONFLICT/current。設定画面で取得した本人IDを `X-Syncnesto-Expected-User` に必須指定する。これは認証源ではなく、古いタブと現在Cookieの本人不一致を403で拒否するための比較値。Cookie/CSRF/Origin検証を維持する。

全応答に `Cache-Control: private, no-store` と `Referrer-Policy: no-referrer`。宛先文字列は返さず、保存はUser/emailの用途分離HMAC。email変更後の旧ONは実効OFF/identity_changedとして扱い、本人が再選択する。再ONや再公開で旧配送を復活させない。

管理者によるメール更新・本人による新メール確認は、同じtransactionで通知設定だけをOFFにしversionを進める。元のアドレスに戻しても旧ONへ戻さない。既存APIの入出力・メール送信契約・User列・DB guardは変更しない。直接SQLや旧互換buildで宛先を保守した場合は、worker再開前に通知設定を本人が再確認する。

## LOCAL起動

既存migrationを適用した環境で、Frontendの既存通知2画面に本人設定欄を表示する。初期OFFを維持し、受信者の明示選択が必要。

LOCALは既存 `LOCAL_EMAIL_CAPTURE_RECIPIENTS` へ明示ダミーだけを追加する。loopback DB・Mailpit（127.0.0.1:1025）・Frontend originだけを許す。

```bash
uv run --frozen python -m scripts.deliver_approval_mail status
uv run --frozen python -m scripts.deliver_approval_mail run --once
uv run --frozen python -m scripts.deliver_approval_mail run
```

15秒ごとに1件を処理する。複数worker/resolveはadvisory lockで排他。Ctrl-C/SIGTERMで停止し、配送子processを回収する。CLIは件数と安全な結果だけを表示する。OS自動起動設定は追加しない。

## 本番の自動配送

承認更新が保存された応答の後に、耐久キューを最大20件・25秒の予算で処理する。1件の送信は最大10秒。途中で停止してもキューはDBに残る。既存CRON_SECRETで認可する `GET /internal/approval-mail/deliver` を日次回収に使い、追加設定は不要。HobbyのCronは日次・指定時間内の実行のため、再試行の即時性や厳密な到達時刻を保証しない。デモDBへ切り替えず、設定された通常DBだけを処理する。

SMTPは既存の接続先・STARTTLS・認証を使う。Resendも既存adapterを使い、宛先やproviderを障害時に切り替えない。providerの受付と人への到達は区別する。

## 配送と障害

新規notification INSERTのRETURNINGだけを同じ業務transactionで配送待ちへ保存する。通知・配送待ちの保存障害は全体rollback、SMTP障害は保存済み判断を変更しない。外部4種の固定承認も対象。種類/固定名/出来事だけを本文へ含め、件名は固定文、リンクは通知画面のみ。

送信直前に現在の本人・設定version・宛先指紋・閲覧資格・公開/参加/許可世代・期限・対象状態を再検証する。送信中は資格・設定の共有ロックを保持し、設定OFFや資格更新が最大10秒のSMTP工程を待つ場合がある。ロック喪失/期限では停止する。SMTPは期限付き子processで実行する。

開始前lease失効は再claim可能、開始耐久化後の失効はunknown。明確な4xxの再試行可能時刻は1/5/30分（最大4試行）で、実際の処理は次のキュー起動時。5xxは停止、24時間/資格期限を超えた便は抑止。DATA受付不明は自動再送しない。SMTPの2xx受付は本人の受信・閲覧を証明しない。

## 結果不明の手動確認

workerを止め、lease終了後のunknownだけを期待version付きで解決する。理由と根拠は所有者だけが読める8192 bytes以下のJSONファイル（symlink不可）にする。

```bash
uv run --frozen python -m scripts.deliver_approval_mail resolve <配送UUID> --version <版> --outcome accepted --reason 'Mailpit実体を確認' --evidence-file /absolute/private/evidence.json
```

根拠は `attempt_id` と期待 `message_id`（`<sha256(approval-mail/配送UUID)@example.com>`）を必須にする。acceptedは `mailpit_id` のrawメールをloopback8025から取得しMessage-IDを照合する。not-acceptedは運用者が確認した `basis:"smtp_rejection", smtp_code:400〜599, reply_received:true` が必要で、受信一覧にないことだけでは再送しない。4xxかつ4試行/24時間の上限内だけを再試行候補に戻し、5xx/回数上限はfailed、期限超過はsuppressed。送信前に資格/設定を再検証する。根拠digestと理由をlocal_operatorとして追記し、過去の試行や人の承認を改変しない。

上のMailpit根拠照合はLOCAL向け。現在のCLIは本番providerの受付根拠を取り込む操作を提供していないため、本番unknownを受付済みと推測して解決・再送しない。既存の試行記録を保持して調査する。

## 停止・戻し方

障害時は履歴を読める互換commit/deploymentへ戻す。既存通知/承認/招待・認証、本人設定と試行記録を保持する。設定または配送履歴があるDBのdowngradeは拒否し、履歴を自動破棄しない。DB全体復元は新規履歴を失う別の復旧手段。届いたメールは回収しない。
