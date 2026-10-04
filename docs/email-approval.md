# メール本人確認と認証情報の変更

組織管理者は本人のパスワードを指定せず、登録済みメールへ再設定リンクを送る。パスワードを忘れた本人もログイン不要で申請・再設定できる。メール変更は現在のメールの承認後、新しいメールの所有確認を行う。変更前にログイン情報やセッションを変更しない。すべての組織で共用するIdentityへの変更であることをメール・画面に表示する。

## API

| 操作 | POST path | body | 認可 |
| --- | --- | --- | --- |
| 忘れたパスワードの申請 | `/auth/password-reset/request` | `{email}` | 公開 |
| リンク内容の表示 | `/auth/account-actions/inspect` | `{token}` | 公開・リンク所持 |
| 本人がパスワードを再設定 | `/auth/password-reset/confirm` | `{token,password}` | 公開・登録メールのリンク所持 |
| 本人のメール変更申請 | `/auth/email-change/request` | `{new_email}` | ログイン本人 |
| 現メールでの承認 | `/auth/email-change/approve` | `{token}` | 公開・現メールのリンク所持 |
| 新メールでの確認・確定 | `/auth/email-change/confirm` | `{token}` | 公開・新メールのリンク所持 |
| 管理者が再設定メールを申請 | `/tenants/current/members/{user_id}/password-reset` | なし | 現在組織のOwner/Admin |
| 管理者がメール変更を申請 | `/tenants/current/members/{user_id}/email-change` | `{new_email}` | 現在組織のOwner/Admin |

申請は202 `{message}`、確定は200 `{message}`。inspectは `{purpose,expires_at,new_email}`。生トークンはAPIレスポンスに含めない。本人profile PATCHはname/versionだけを受け付ける。既存のシステム運営者用 `/users` は明示的な運営permissionに基づく緊急Identity管理として維持し、組織管理者からは利用できない。

管理者の申請先は現在組織に有効所属する有効なIdentityだけ。AdminはOwnerを対象に申請できず、Ownerは組織内の全Roleを対象にできる。確認時にも申請者の現在権限・所属、対象の所属、組織の状態を再検証し、降格・停止・削除後は未使用リンクを拒否する。本人の公開復旧と本人メール変更は組織所属に依存しない。

## 安全性と失敗時

- 256bitのランダムトークンを使い、DB `account_actions` にはSHA-256だけを保存する。確認要求は共通Identityデータで、tenant_idは申請元の記録と権限再検証に使用する。
- リンクは設定済み `FRONTEND_PUBLIC_URL` の固定originから作る。HTTP Hostやユーザー入力URLは使用しない。本番はHTTPS、ローカルだけlocalhost/127.0.0.1のHTTPを許可する。
- 生トークンはURL fragmentで渡す。ブラウザが読み取った後にURLから削除し、コンポーネントメモリにだけ保持する。query、Cookie、永続ストレージ、QueryClient、監査ログへ格納しない。
- リンクGETやinspectだけでは変更しない。本人のボタン操作によるPOSTで確定するので、メールのリンクスキャナーで消費しない。
- 有効期限は標準30分。送信受付成功・未使用・未失効・当時のメール/PWに一致する要求だけ有効。現在のログイン情報が変われば旧リンクも無効になる。
- User行を先にロックし、同じIdentityの申請・確認を直列化する。最終変更、リンク消費、他の確認要求の失効、全セッション失効、旧ログインロック解除は同一DBトランザクション。新しいログインは本人が行い、自動ログインしない。
- 新PWは12〜128文字、画面で確認入力も求める。完了通知にPWや確認リンクを含めない。
- 忘れたPWはアカウント有無によらず同じ202を応答し、登録照会と送信はFastAPI BackgroundTasksで応答後に行う。アドレス/IPはHMAC化して送信申請を2回/分・5回/分に制限する。制限超過は429 `RATE_LIMITED` とRetry-After。完了APIは公開APIの既存IP制限でも保護する。
- Cookieがある更新POSTは従来のCSRF検証を維持する。未ログインの確認POSTは高エントロピーのリンクで認証し、ログインCookieを要求しない。公開認証ページで `/auth/me` の401が発生してもloginへ転送しない。
- 新メールの未承認予約はしない。承認前の予約による他人のアドレス妨害を避け、申請時・旧メール承認時・最終確認時に重複を検証し、DB UNIQUEで同時確定も拒否する。
- disabled/設定不足は503 `EMAIL_UNAVAILABLE`。認証済み申請と承認段階の送信失敗も503。送信失敗では認証情報・既存リンクを変更しない。公開復旧では配信失敗や登録有無を応答に反映せず、受信できない場合は再申請・運営者への連絡で対応する。
- 無効・期限切れ・使用済み・用途違いのリンクは400 `ACCOUNT_ACTION_INVALID`。ログインJWTの `INVALID_TOKEN` と混同しない。

メールAPIの受付は配送完了の保証ではない。現在は短いタイムアウトで同期送信し、受付IDをDBへ記録する。送信受付後にDB commitが失敗すると使えないメールが届く場合があり、再申請で復旧する。BackgroundTasksは耐久キューではないため、実行中断時の自動再送を保証しない。生トークンを平文のoutboxに保存しない。耐久配送、バウンス/Webhook、署名検証は将来拡張する。

運営者の緊急Identity更新もUser行をロックし、メール・パスワード・有効状態の変更と全セッション・未使用リンクの失効を同じトランザクションで確定する。途中の失効処理に失敗した場合は全体をrollbackする。停止後に再有効化しても、停止前のリンクは復活しない。ログイン時も同じUser行のロックをセッション作成まで保持し、認証情報変更との並行ログインで古い認証情報の有効セッションが残らないようにする。

### Vercel Pythonでの応答と送信の順序

今回のローカルbuildは `@vercel/python 19.0.0` / `vercel-runtime 0.23.2` のFluid/IPC ASGI経路を使用した。[ASGIMiddleware](https://github.com/vercel/vercel/blob/c628be7835e03a965b93e9cf9e2bd5ac2acbf5eb/python/vercel-runtime/src/vercel_runtime/vc_init.py#L737-L795) はアプリの終了まで待ってからIPC endを出し、Starletteはレスポンス本文送信後にBackgroundTasksを待つ。DBや外部通信を使わない実TCP検証で、同梱のHTTP bridgeとNode `fetch().arrayBuffer()` が本文取得を完了した後も、送信相当のBackgroundTaskは実行中で、Function終了はその完了後になる順序を確認した。このため現在の公開復旧APIは、送信処理を切り離した未追跡のtaskへ置き換えない。

これはローカルのランタイム経路の検証であり、本番gateway/CDNの応答時間を保証しない。本番適用前にFluid経路を確認し、previewのBFF経由で登録済み・未登録メールの応答時間、Gmailへの到達、送信失敗とタイムアウトを確認する。legacyの非IPC経路にはこの検証結果を適用しない。ランタイム更新時も順序を再検証する。

## ローカル

infraで `make mailpit-up` を実行するとSMTP `127.0.0.1:1025` と受信UI `http://localhost:8025` を起動する。停止は `make mailpit-stop`。

backendのGit管理外 `.env` に `EMAIL_PROVIDER=smtp`、`EMAIL_FROM=Syncnesto <noreply@syncnesto.local>`、`FRONTEND_PUBLIC_URL=http://localhost:3000`、`SMTP_HOST=127.0.0.1`、`SMTP_PORT=1025`、`SMTP_STARTTLS=false` を設定し、Backendを再読み込みする。Mailpitは外部へメールを転送しない。`admin@example.com` などの開発用アドレスでもリンクを確認できる。テストでは配信アダプターをfakeへ差し替え、外部へ送信しない。

## ドメイン購入なしのGmail SMTP

現在の実送信候補は、指定された送信用アドレス `syncnesto@gmail.com` のSMTP。無料の `@gmail.com` アドレスを送信元に使うため、独自ドメインの購入やDNS設定は不要。送信元として受信者にこのGmailアドレスが表示される。アカウントの準備・所有と実際の送信可否は別途確認する。

1. 送信用アカウントでGoogleの2段階認証を有効にし、[アプリパスワード](https://support.google.com/accounts/answer/185833?hl=ja) をSyncnesto専用に作る。通常のGoogleログインパスワードは使用しない。16文字を表示上の区切り空白なしで設定する。
2. ローカルで実送信を確認するときだけ、BackendのGit管理外 `.env` に下記を設定して再起動する。Mailpitの既定設定は残し、Gmail確認には実際に受信できるアドレスを使う。
3. 公開環境ではBackendだけに同じSMTP変数とHTTPSの `FRONTEND_PUBLIC_URL` を設定する。アプリパスワードをチャット、Git、`NEXT_PUBLIC_`、ログへ貼らない。Vercel登録は既存Terraformのephemeral/write-only管理に合わせる。設定後の再デプロイと実際の承認・復旧メール検証は別途行う。

```env
EMAIL_PROVIDER=smtp
EMAIL_FROM=Syncnesto <syncnesto@gmail.com>
FRONTEND_PUBLIC_URL=https://your-frontend.vercel.app
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_STARTTLS=true
SMTP_USERNAME=syncnesto@gmail.com
SMTP_PASSWORD=<空白なし16文字の専用アプリパスワード>
```

ローカル実送信では `FRONTEND_PUBLIC_URL=http://localhost:3000` にできる。公開環境は `smtp.gmail.com:587` のSTARTTLSと認証を必須にし、`EMAIL_FROM` のアドレスと `SMTP_USERNAME` の一致を確認する。開発環境でも遠隔SMTP接続とSMTP認証にはSTARTTLSを要求する。TLS証明書を検証し、STARTTLS失敗時は平文へ切り替えない。設定不足は送信前に拒否する。Mailpitの無認証・loopback接続は開発環境で引き続き利用できる。

`EMAIL_TIMEOUT_SECONDS` はSMTP socketの各I/Oに適用するタイムアウトで、接続から配送完了までの総処理時間の上限ではない。公開Functionの実行時間設定と合わせ、preview環境で遅延・失敗も確認する。

Googleアカウントの保護設定や組織設定によりアプリパスワードを作れない場合がある。Googleログインパスワードを変更するとアプリパスワードは失効する。Gmailには [送信上限](https://support.google.com/mail/answer/22839?hl=ja) があり、大量配信を保証するサービスではない。SMTPのMessage-IDは固定するが、SMTPサーバーでの重複排除やバウンスの自動処理は保証しない。小規模運用の実送信を確認後、利用増加時はSES等へ移行する。

[Vercelは587番のSMTP接続を制限していない](https://vercel.com/kb/guide/serverless-functions-and-smtp)。ただし公開環境のPython Runtime、FastAPI BackgroundTasksの実行完了、タイムアウト、受信側への到達はローカルSMTPだけで検証できないため、公開前にpreview環境で確認する。2026-10-04にローカルBackendだけへGmailの資格情報を設定し、TLS接続とSMTP認証の成功を確認した。その後ユーザーの送信指示を受け、`syncnesto@gmail.com` 宛に件名「[Syncnesto] Gmail SMTP接続確認」の確認メールを1通送り、SMTPサーバーの受付成功とユーザーによる受信確認を完了した。本番適用は未実施。

## Resendと将来のSES

Resendは独立した配信サービスで、Vercelでも利用できる。Backendだけに `EMAIL_PROVIDER=resend`、認証済みの送信元 `EMAIL_FROM`、`RESEND_API_KEY`、本番Frontendの `FRONTEND_PUBLIC_URL=https://...` を設定する。APIキーをNEXT_PUBLIC変数やGitに保存しない。Vercel本番設定は既存Terraformのephemeral/value_woで追加し、アカウント/ドメイン準備後に本番への適用を別途行う。

2026-10-04の公式料金ではFreeは3,000通/月・100通/UTC日・認証済み3ドメイン。一般利用者への配信には所有する独自ドメインのDNS認証が必要。Vercel Native MarketplaceはVercelで購入したドメインが前提で、それ以外のドメインは直接Resendアカウントで認証してAPIを使う。vercel.appは送信ドメインとして使えない。独自ドメイン前のonboarding@resend.devはResend登録本人宛だけで、全利用者の承認用には使えない。

ResendへはHTTPS APIで1宛先を送信し、確認要求IDをIdempotency-Keyとして使う。HTTP redirectは拒否し、エラー本文やAPIキーを公開・記録しない。将来SESへ移行するときは `EmailService` の送信アダプターを追加し、本人確認モデル・トークン・認可・監査・テンプレートを維持する。既存S3資格情報を流用せず、SES専用IAMを設定する。

参照: [Resend料金](https://resend.com/pricing)、[アカウント上限](https://resend.com/docs/knowledge-base/account-quotas-and-limits)、[Marketplace](https://resend.com/docs/guides/vercel-marketplace-integration)、[ドメイン認証](https://resend.com/docs/add-a-domain)、[テスト送信制限](https://resend.com/docs/knowledge-base/403-error-resend-dev-domain)、[OWASP Password Recovery](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html)。
