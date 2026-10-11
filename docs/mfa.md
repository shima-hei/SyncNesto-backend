# 通常アカウントの追加認証（MFA）

2026-10-11実装。認識合わせの記録は [決定記録](decisions/2026-10-10-mfa-authorization.md)、
APIとFrontendの契約は [認証・認可仕様](frontend-authorization.md) を参照する。
2026-10-11に公開用MFA・Gmail SMTP変数を登録し、Backend PR #19とFrontend PR #25をマージ・公開配布した。
通常・デモDBのmigrationと、公開URLでの通常MFA要求・匿名デモの分離/ログアウト破棄を確認済み。
本人の認証器登録・実機Passkey・公開環境からのメール配送は未確認で、公開配布とは区別する。
ローカルの既存DB（loopback:5432）は2026-10-11にバックアップ後、`a4f92c1e806d`へ適用済み。
ユーザー・組織・Project・要件・タスクの件数を前後比較し、変更がないことを確認した。
専用鍵はGit対象外・権限600の`.env`に保存した。本人の認証器登録は行っていない。

## 対象とログイン

- 通常アカウント全員が対象。システム管理者、組織管理者、構成員、外部協力者、閲覧・テスト実行専用にも例外を設けない。
- パスワードの後にTOTPまたはPasskeyで本人を確認する。Passkey単独のパスワードレスログインには変更しない。
- 初回パスワード変更の制限を維持し、変更後のログインでMFAを登録する。
- 匿名デモは対象外。通常アカウントの既存DB/Storage、`APP_ENV=production`、`DEMO_MODE`、デモ専用資源への分離を維持する。
- MFA完了はRBAC権限を増やさない。運営・組織・Project業務権限の分離を維持する。

パスワード成功後は10分間のHttpOnly・SameSite=Strict限定Cookieだけを発行する。
このCookieでプロフィール・業務API・MCP同意は利用できない。登録済みの場合は追加認証を、未登録の場合は認証器登録を要求する。
初回登録では復旧コードの保存確認後に通常セッションを発行する。ブラウザの永続StorageやQuery Cacheへ秘密を保存しない。

TOTPは30秒間隔、前後1区間まで許容し、使用済みコードを同じ認証器で再利用できない。
Passkeyは固定したRP・Frontend origin・本人・一度限りのchallengeへ拘束し、User Verificationを必須にする。
限定状態の検証は5失敗まで、発行し直しても本人10回/分・信頼できるIP30回/分の共有上限を適用する。

## アカウント設定と失効

アカウント画面の「追加認証」で認証器と未使用復旧コード数を確認する。
複数のTOTPアプリ・Passkeyを合計10件まで登録でき、認証器ごとの名前を変更できる。
登録・削除・名前変更・コード再発行には5分以内の追加認証を要求する。
期限を過ぎた場合はパスワードと登録済みTOTP/Passkeyで再認証する。復旧コードだけでは再認証を完了できない。

| 操作 | 既存セッション・MCPへの影響 |
| --- | --- |
| 予備の認証器を追加 / 表示名変更 | 維持する |
| 認証器を削除 | 本人の全通常セッション・限定状態・MCP接続を失効し、再ログインを要求する。最後の認証器は削除不可 |
| 復旧コードを再発行 | 旧復旧コードを全て無効化。通常セッション・MCP接続は維持する |
| 運営者の本人確認による復旧支援 | 全セッション・限定状態・MCP接続・認証器・復旧コードを失効し、再登録待ちにする |

復旧コードは128bitのランダム値を10件、一度だけ表示し、DBにはハッシュのみ保存する。
パスワードと未使用コードでログインでき、使用すると消費する。並行利用でも一度だけ成功する。
認証器紛失時は、このログインから予備を追加して古い認証器を削除できる。
コードを再発行したら新しい一覧を保存する。画面更新後の平文再表示はできない。

MCPは同意時の追加認証とMFA security versionを保存し、認可コード・refresh・access・Bearer利用時に現在のIdentityと照合する。
MFA前の旧セッション・旧MCP接続を検証済みに補完しない。再ログイン・再接続が必要。

## 全ての認証手段を失った場合

事前に独立した予備アプリまたはPasskeyと、復旧コードを保管することを基本にする。
全て失った場合は、**別のシステム運営者**が本人確認し、ユーザー詳細画面から復旧を支援する。
運営者にはsystem `user:mfa_recover` と直近のTOTP/Passkey認証が必要。
組織管理者の単独解除、メールだけの解除、本人による自己承認は提供しない。

1. 対面または事前に本人確認した連絡経路で本人確認し、方式・理由を記録する。メール受信できることだけを根拠にしない。
2. 復旧申請を承認すると既存接続を失効し、登録済みメールへ30分有効・一度限りの再登録リンクを送る。
3. 本人がリンクと現在のパスワードを確認し、新しい認証器を登録する。コード保存を完了するまで業務アクセスを再開しない。

通知を送信できない場合は承認処理全体をrollbackし、失効だけを確定させない。
重要な認証変更と監査保存は同じDB transactionで確定する。監査には秘密・コード・Cookieを含めない。
その他の登録・削除・コード使用等の通知失敗は監査に残し、確定済みの操作は取り消さない。

最後の運営者が全て紛失した場合に備え、予備運営者と独立した認証器を準備する。
隠しHTTPバイパスや常設のMFA解除機能は設けない。例外的な復旧は、事前バックアップ・本人確認・変更記録を伴う運用として別途判断する。

## 環境設定と適用手順

| Backend変数 | 内容 |
| --- | --- |
| `MFA_REQUIRED` | 既定`true`。通常アカウントのセッション・MCPに必須化 |
| `MFA_ENCRYPTION_KEYS` | Fernet鍵のカンマ区切り。先頭で暗号化し、旧鍵でも復号する。Git対象外の秘密として保管 |
| `WEBAUTHN_RP_ID` | Frontendの固定ホスト名。ポートを含めない |
| `WEBAUTHN_ORIGIN` | Frontendの固定origin。公開環境はHTTPSかつ`FRONTEND_PUBLIC_URL`と一致必須 |

ローカルだけは`http://localhost:<port>`を許容する。公開環境でMFA必須の場合、鍵や固定originの不備は起動時に拒否する。
鍵はDBとは別にバックアップし、紛失・不用意な上書きを避ける。ローテーション時は旧鍵を残し、既存秘密を再暗号化してから旧鍵を除く。

### ローカル開発での一時的な回避

既存の `MFA_REQUIRED=false` を使えるため、専用バイパスAPIや固定コードは追加しない。
`APP_ENV=development`、loopbackでのみ待ち受けるBackend、ローカルDBを使う開発環境に限り、
Git対象外のBackend `.env` に設定して再起動する。公開Vercel・共有環境・通常Neon DBには適用しない。
この設定は登録済み認証器を削除しない。追加認証の画面・失効を確認するときは `true` に戻して再起動する。
MCPの認証強度確認も `true` の状態で行う。公開環境は全通常アカウント必須という合意を維持する。

新しいローカル環境で鍵が未設定なら、`.env`作成後、次のコマンドでGit対象外のファイルへ生成できる。
既存の鍵を上書きせず、標準出力へ値を出さない。

```bash
uv run python - <<'PY'
from pathlib import Path
from cryptography.fernet import Fernet
from dotenv import dotenv_values, set_key
path = Path('.env')
if not dotenv_values(path).get('MFA_ENCRYPTION_KEYS'):
    set_key(path, 'MFA_ENCRYPTION_KEYS', Fernet.generate_key().decode())
path.chmod(0o600)
PY
```

公開適用は次の順序で行う。無料の既存構成を維持し、新しい外部認証サービスや有料SMSには依存しない。

1. DBのバックアップと、通常・デモの接続先を照合する。
2. **通常DBとデモDBの両方**へ `alembic upgrade head` を適用する（`a4f92c1e806d`）。新しい必須User列にはserver defaultを設け、migration先行中の旧アプリのINSERTも維持する。
3. 専用暗号鍵と公開FrontendのRP/originを設定する。
4. `MFA_REQUIRED=true`のままBackend、Frontendの順で反映する。未登録の通常アカウントは次のログインで認証器を登録する。既存通常セッションとMCPは再認証・再接続が必要。
5. 運営者が予備認証器・復旧コードを保存し、本人操作でTOTP/実機Passkey・再ログインを確認する。
6. 通常ユーザー・MCP・匿名デモのログインと、ログアウト/期限切れ時のデモ全破棄を確認する。公開環境に準備用のMFA除外期間を設けない。

### 2026-10-11の公開適用記録

- Backend: [PR #19](https://github.com/shima-hei/SyncNesto-backend/pull/19)、実装head `42dcc3d`、merge `6813a3f`。
  [main CI/deploy](https://github.com/shima-hei/SyncNesto-backend/actions/runs/38102968545)が成功し、公開aliasのREADY deploymentとcommitの一致を確認した。
- Frontend: [PR #25](https://github.com/shima-hei/SyncNesto-frontend/pull/25)、head `2776dc2`、merge `b3516a5`。
  [main CI/deploy](https://github.com/shima-hei/SyncNesto-frontend/actions/runs/38148137846)が成功し、公開aliasのREADY deploymentとcommitの一致を確認した。
- 通常・デモ両Neon DBのバックアップをGit対象外・所有者専用の保存先へ取得した。暗号鍵はDBとは別に保管した。
- 両バックアップを隔離したPostgreSQL 17へ復元し、`a4f92c1e806d`へのmigration、業務データ件数の維持、旧セッションをMFA済みにしないことを確認した。検証用DBは削除済み。
- 公開Backendは `MFA_REQUIRED=true`、固定RP `syncnesto.vercel.app` とorigin `https://syncnesto.vercel.app`、専用暗号鍵を使用する。公開の準備用false期間は設けない。
- 通常・デモ両DBのrevision `a4f92c1e806d`、既存のユーザー・組織・Project件数の維持、runtime roleのMFA4表CRUD権限を読み取り専用接続で確認した。
  DB role・GRANT・RLSの境界は[Infraの確認記録](../../syncnesto-infra/README.md#2026-10-11のneon権限確認)を参照する。
- 公開URLで通常アカウントのパスワード確認後、限定CookieのSecure/HttpOnly/SameSite=Strict、MFA画面、業務APIの401、CSRFなしcancelの403、正規cancel後の失効を確認した。個人の認証器数は変更していない。
- 匿名デモの開始・業務参照・通常MFA管理の拒否、通常DBへの混入なし、ログアウト後の旧Cookie拒否と専用DBの所有データ削除を確認した。今回の確認はログアウト経路で、時間経過による期限切れの公開再実行は含まない。
- 公開MCPの未認証initializeは401。protected resourceとOAuth metadataは正規HTTPS URL、PKCE S256、scope `mcp:work`。本人のMFA後の実OAuth再接続は未確認。
- ローカルで既に使用していたSyncnesto用Gmail SMTP（587/STARTTLS）を公開Backendにも設定した。既存資格情報によるSTARTTLS認証を確認し、試験メールは送信していない。新しい有料providerは追加していない。
- 本人の認証器は代わりに登録しない。TOTP・実機Passkey・予備認証器・復旧コードの保存と、公開環境からのメール配送は本人の初回登録時に確認する。

## 検証状態と残タスク

- Backend全体: `926 passed, 5 skipped, 3 xfailed`。対象経路は63件成功。ruff・pyright・format・diff検査も通過。
- Frontend: MFA境界4件を含む79件成功。format・typecheck・lint・本番build通過。production依存のnpm auditは既知脆弱性0件。
- 専用DBでmigrationのdowngrade/upgrade、ユーザー件数保持、旧形式INSERT、旧セッションをMFA済みに補完しないことを検証した。
- 実DB・実TOTP・実ECDSA署名によるPasskey検証、同時復旧コード利用、失効、所有者/権限、通知/監査失敗時のtransactionを自動テストする。
- 専用の使い捨てローカルDBで、未登録ログインの登録案内、キャンセル、TOTPログインからホームとアカウント設定への遷移をブラウザ確認した。
- 公開環境への適用と上記の限定ログイン・匿名デモ確認は完了。実機のPasskey登録・生体認証、個人の認証アプリへの登録、公開メール配送とMFA後の本人によるMCP再接続は未確認。署名検証テストと実機確認を混同しない。
- 次の開発は運営/組織管理/Project業務の表示と名称の整理。システム設定・組織ごとの機能許可は後続で、今回のMFAに混ぜない。
- MCP公開審査の再開条件・提出手順は [Codex連携の記録](decisions/2026-10-10-codex-plugin.md) を参照する。
