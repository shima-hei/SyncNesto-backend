# MCP公開審査専用Identity

2026-10-12。運営者は通常DB内の独立した審査組織とサンプルデータを作る構成を承認した。
OpenAIの審査ログイン要件に合わせ、この専用IdentityだけMFAを免除する。
通常利用者全員のMFA必須、`APP_ENV=production` / `DEMO_MODE=true`、匿名デモの別DB・StorageとMCP不可を維持する。

## 境界

- `review_account_grants` を運用CLIで新規Identityと同時に発行する。既存ユーザーを審査用へ転用しない。
- Web認証・MCP同意・Bearer/refresh/codeの利用時に、許可の期限・失効・本人の状態・所属を再検証する。
- 有効な組織所属が専用組織の `tenant_member` 1件だけ、運営Roleなし、他組織のProject所属なし、同組織の他の有効ユーザーなしを要求する。異常時は利用を拒否する。
- 組織選択を専用組織へ固定する。既存のProject RBACとOAuth同意は必要で、レビュー用の許可上限を別に適用する。
  運営・組織・Projectメンバー管理、資格情報・プロフィール・MFAの変更は許可しない。
- MFA検証済み日時・方式を偽造しない。実際のパスワード認証と既存のセッション・CSRF・PKCE S256・resource・scope・OAuth失効を維持する。
- 審査IdentityのMCP接続期限は審査許可の期限を超えない。logoutは既存セッションを終了し、サンプルを消さない。
- `/auth/me` の任意項目 `review_access_expires_at` で専用状態を示す。Frontendは期限を表示し、本人情報の編集UIを出さない。

## 移行・発行・失効

Migration `c2a7e491d6b8`（親 `b7d30e2c941a`）は空の許可テーブルだけを追加する。
通常・デモ双方のスキーマを揃えてからBackendを配布する。審査データの発行は通常DBだけで行う。
既存のMFA・Role割当・所属・既存テーブルのDB GRANTを変更しない。
新しい許可テーブルはruntime `syncnesto_app` にSELECTだけを認め、発行・失効はownerの運用CLIに限定する。

```bash
uv run python -m scripts.manage_review_account create --days 30 --output /private/location/review.json
uv run python -m scripts.manage_review_account revoke --user-id ID
```

発行先はGit外の権限700の私有ディレクトリとし、ファイルは排他的に作成し600で保存する。
平文の資格情報はstdout・ログ・Git・ZIP・動画へ出さず、OpenAIの非公開審査欄だけへ渡す。
初回の許可は30日、CLIで指定できる範囲は1〜90日。失効CLIはWebセッションとMCP接続も失効する。
期限切れと実削除は別で、サンプル・監査等の実削除運用は規約の未確定事項に残す。
発行後のcommit失敗時は資格情報ファイルが残りうるため、DBの発行有無を確認してから再発行する。

## 検証・公開適用

限定審査Identityの実ログイン、通常MFAの維持、組織切替拒否、管理・本人情報変更拒否、
7種類の隔離違反・期限切れ・失効によるWeb/code/refresh/Bearer拒否、実OAuthによるサンプル操作、logout後の保持をRouterテストで検証する。
Backend全体テストとFrontendのformat・typecheck・lint・84テスト・本番build・CSPを確認してから公開する。
実際のCI・公開revision・審査fixture発行・ホスト上のケースは、完了後に本節へ追記する。

ローカル検証: 関連120件、追加14件、ruff・pyright成功。Frontendは上記全項目成功。
両Neon DBをバックアップし、ネットワーク・公開ポートなしの隔離復元環境で移行を予行した。
既存74テーブル中、内容が変わるのは `alembic_version` だけで、新テーブルは空。
runtimeの新テーブル権限がSELECTだけであることも確認した。公開DBにはまだ適用していない。
