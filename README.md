# Syncnesto Backend

Syncnesto のバックエンドAPIです。FastAPI、SQLAlchemy、Alembic、PostgreSQL を使って実装しています。

現在は認証・認可機能の土台として、ログインAPI、RBACによる管理者向けユーザー作成API、パスワードハッシュ化、共通例外ハンドラー、ロギング、テスト用PostgreSQL環境を整備しています。

## 公開範囲とライセンス

このリポジトリは公開状態でも参照できるようにしていますが、ソースコードおよびドキュメントの権利は著作者が保持します。

```text
Copyright (c) 2026 shima-hei
All rights reserved.
```

著作者の明示的な許可なく、本ソフトウェアおよび関連ドキュメントの複製、再配布、改変、販売、公開、または派生物の作成を禁止します。

このリポジトリを参照できることは、利用・改変・再配布の許諾を意味しません。利用条件が必要な場合は、著作者から個別に許可を得てください。


## 技術スタック

- Python 3.14
- FastAPI
- Pydantic v2
- SQLAlchemy 2
- Alembic
- PostgreSQL 16
- pytest
- Docker Compose
- uv

## ディレクトリ構成

```text
.
├── app/
│   ├── core/          # 設定、ロギング、例外、Middleware、security
│   ├── db/            # DB engine/session/Base
│   ├── models/        # SQLAlchemy models
│   ├── repositories/  # DBアクセス
│   ├── routers/       # FastAPI routers
│   ├── schemas/       # Pydantic schemas
│   ├── services/      # 業務ロジック
│   └── main.py
├── alembic/           # migration管理
├── tests/             # pytest tests
├── docker-compose.yml
├── pyproject.toml
├── uv.lock
└── README.md
```

## 環境変数

開発用の設定は `.env` に置きます。

以下はローカル開発用のサンプルです。公開リポジトリには実際の `.env` をコミットしません。共有用のテンプレートは `.env.example` を更新してください。本番環境では `SECRET_KEY`, `INITIAL_ADMIN_PASSWORD`, DBパスワード, AWS認証情報を必ず安全な値に差し替えてください。

```env
# ローカル開発用DB設定。
DB_USER=admin
DB_PASSWORD=admin
DB_NAME=syncnesto
DATABASE_URL=postgresql://admin:admin@localhost:5432/syncnesto

# アプリケーション実行設定。
APP_NAME=Syncnesto API
APP_ENV=development

# ログ設定。
LOG_LEVEL=INFO
LOG_FORMAT=text
SQL_ECHO=false

# JWT署名設定。
SECRET_KEY=change-me-at-least-32-bytes
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# サーバー側セッション設定。
SESSION_IDLE_TIMEOUT_MINUTES=30
SESSION_REFRESH_THRESHOLD_MINUTES=10
SESSION_ABSOLUTE_TIMEOUT_MINUTES=480

# ログイン試行制限設定。
LOGIN_MAX_FAILED_ATTEMPTS=5
LOGIN_LOCK_MINUTES=15

# 監査ログ保持設定。
AUDIT_LOG_RETENTION_DAYS=1095
AUDIT_LOG_CLEANUP_MIN_DAYS=30

# 認証Cookie設定。
AUTH_COOKIE_NAME=access_token
AUTH_COOKIE_SECURE=false
AUTH_COOKIE_SAMESITE=lax

# CSRF対策設定。
CSRF_COOKIE_NAME=csrf_token
CSRF_HEADER_NAME=X-CSRF-Token
CSRF_COOKIE_SECURE=false
CSRF_COOKIE_SAMESITE=lax

# 開発互換用の切り替え設定。
ALLOW_BEARER_TOKEN_RESPONSE=true
ALLOW_AUTHORIZATION_HEADER=true

# 初期システム管理者seed設定。
INITIAL_ADMIN_EMAIL=admin@example.com
INITIAL_ADMIN_PASSWORD=change-me
INITIAL_ADMIN_NAME=Initial Admin

# ユーザーアイコン用S3 / LocalStack設定。
AWS_REGION=ap-northeast-1
AWS_S3_BUCKET_NAME=syncnesto-local-app-bucket
AWS_S3_ENDPOINT_URL=http://localhost:4566
AWS_S3_PRESIGNED_URL_EXPIRES_SECONDS=3600
DEFAULT_AVATAR_KEY=default-avatar.png
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
```

テスト用の設定は `.env.test` に置きます。`tests/conftest.py` がテスト実行時に読み込みます。

```env
DATABASE_URL=postgresql://admin:admin@localhost:5433/syncnesto_test
APP_ENV=test
LOG_LEVEL=WARNING
SECRET_KEY=test-secret-key-at-least-32-bytes
```

主な設定:

- `DATABASE_URL`: SQLAlchemy/Alembic が使用するDB接続URL
- `APP_ENV`: 実行環境名
- `LOG_LEVEL`: `DEBUG`, `INFO`, `WARNING`, `ERROR` など
- `LOG_FORMAT`: `text` または `json`
- `LOG_FILE`: 指定した場合はファイルにもログ出力
- `SQL_ECHO`: `true` の場合 SQLAlchemy のSQLログを出力
- `SECRET_KEY`: JWT署名に使用する秘密鍵
- `ALGORITHM`: JWT署名アルゴリズム
- `ACCESS_TOKEN_EXPIRE_MINUTES`: セッションIDを使わないJWTの有効期限。通常ログインではDBセッションの期限を使用
- `SESSION_IDLE_TIMEOUT_MINUTES`: 一定時間操作がない場合にセッション期限切れにする分数
- `SESSION_REFRESH_THRESHOLD_MINUTES`: 残り時間がこの分数以下の場合にsliding expirationで延長
- `SESSION_ABSOLUTE_TIMEOUT_MINUTES`: 操作が続いていても必ず期限切れにする最大セッション時間
- `LOGIN_MAX_FAILED_ATTEMPTS`: ログイン失敗で一時ロックするまでの連続失敗回数
- `LOGIN_LOCK_MINUTES`: ログイン失敗閾値到達後の一時ロック分数
- `AUDIT_LOG_RETENTION_DAYS`: 監査ログ削除コマンドのデフォルト保持日数
- `AUDIT_LOG_CLEANUP_MIN_DAYS`: 誤削除防止のため、削除対象に指定できる最小経過日数
- `AUTH_COOKIE_NAME`: 認証Cookie名
- `AUTH_COOKIE_SECURE`: `true` の場合 Secure Cookie として発行
- `AUTH_COOKIE_SAMESITE`: Cookie の SameSite 属性
- `CSRF_COOKIE_NAME`: CSRF tokenを保存するCookie名
- `CSRF_HEADER_NAME`: 更新系APIで送信するCSRF tokenヘッダー名
- `CSRF_COOKIE_SECURE`: `true` の場合 CSRF CookieをSecure Cookieとして発行
- `CSRF_COOKIE_SAMESITE`: CSRF Cookie の SameSite 属性
- `ALLOW_BEARER_TOKEN_RESPONSE`: `true` の場合ログインレスポンスbodyにもaccess tokenを返す
- `ALLOW_AUTHORIZATION_HEADER`: `true` の場合Authorizationヘッダー認証を許可する
- `INITIAL_ADMIN_EMAIL`: 初期管理者seedで作成する管理者email
- `INITIAL_ADMIN_PASSWORD`: 初期管理者seedで使用する管理者password
- `INITIAL_ADMIN_NAME`: 初期管理者seedで使用する管理者名
- `AWS_REGION`: S3クライアントで使用するAWSリージョン
- `AWS_S3_BUCKET_NAME`: ユーザーアイコンを保存するS3バケット名
- `AWS_ENDPOINT_URL` / `AWS_S3_ENDPOINT_URL`: LocalStackなどのS3互換エンドポイントURL。本番AWSでは未設定
- `AWS_S3_PRESIGNED_URL_EXPIRES_SECONDS`: 署名付きURLの有効期限秒数
- `DEFAULT_AVATAR_KEY`: ユーザー作成時に設定するデフォルトアイコンのS3 key
- `AWS_ACCESS_KEY_ID`: S3アクセスキー。LocalStackでは `test`
- `AWS_SECRET_ACCESS_KEY`: S3シークレットキー。LocalStackでは `test`

## セットアップ

依存関係は `uv` で管理します。

```bash
uv sync --extra dev
```

以後のコマンドは `uv run ...` で実行します。

```bash
uv run pytest
```

依存を追加する場合:

```bash
uv add package-name
uv add --dev package-name
```

## 開発用DB

PostgreSQL と pgAdmin を起動します。

```bash
docker compose up -d postgres pgadmin
```

pgAdmin:

```text
http://localhost:81
Email: admin@example.com
Password: admin
```

pgAdminからPostgreSQLへ接続する場合:

```text
Host: postgres
Port: 5432
Username: admin
Password: admin
Database: syncnesto
```

## Migration

DBへ最新migrationを適用します。

```bash
uv run alembic upgrade head
```

モデル変更からmigrationを作成する場合:

```bash
uv run alembic revision --autogenerate -m "message"
```

Alembic は `.env` の `DATABASE_URL` を読み込みます。

## 初期管理者seed

管理者用APIを使い始めるため、RBAC初期データと最初の `system_admin` ユーザーはseedで作成します。

```bash
uv run python -m scripts.seed_rbac
```

`INITIAL_ADMIN_EMAIL` と `INITIAL_ADMIN_PASSWORD` が必要です。既に同じemailのユーザーが存在する場合は、そのユーザーに `system_admin` ロールを付与します。

## アプリ起動

```bash
uv run uvicorn app.main:app --reload
```

Swagger UI:

```text
http://localhost:8000/docs
```

## 現在のAPI

### Health Check

```text
GET /
```

レスポンス:

```json
{
  "message": "health check OK"
}
```

ログイン成功時はHttpOnly Cookieにもaccess tokenをセットします。開発環境ではSwagger UIや手動検証をしやすくするため、レスポンスbodyにも `access_token` を返します。本番では `ALLOW_BEARER_TOKEN_RESPONSE=false` にして、bodyには成功メッセージだけを返す運用を想定しています。

認証CookieはDBセッションの `sid` と紐づきます。期限が近い状態で認証済みAPIを呼び出すと、sliding expirationによりDBセッションとCookieを更新します。`TOKEN_EXPIRED` または `INVALID_TOKEN` の401レスポンスでは、BFFがブラウザへ中継できるように削除用の `Set-Cookie` を返します。

システムロールやプロジェクトメンバーロールなど、ユーザーの権限が変わる操作では対象ユーザーの既存セッションを `permission_changed` として失効します。対象ユーザーは次回API呼び出し時に再ログインが必要になります。

### Users

```text
POST   /users
GET    /users
GET    /users/{user_id}
PATCH  /users/{user_id}
DELETE /users/{user_id}
```

必要な権限:

```text
POST   /users              user:create
GET    /users              user:read
GET    /users/{user_id}    user:read
PATCH  /users/{user_id}    user:update
DELETE /users/{user_id}    user:delete
```

作成リクエスト:

```json
{
  "email": "user@example.com",
  "name": "User Name",
  "password": "password123"
}
```

レスポンス:

```json
{
  "id": 1,
  "email": "user@example.com",
  "name": "User Name"
}
```

`password` は平文のまま保存せず、Argon2idでハッシュ化して `users.hashed_password` に保存します。レスポンスには `password` と `hashed_password` を含めません。

### Projects

```text
POST   /projects
GET    /projects
GET    /projects/{project_id}
PATCH  /projects/{project_id}
DELETE /projects/{project_id}
```

必要な権限:

```text
POST   /projects                project:create
GET    /projects                ログイン必須、page/page_size/q/status対応
GET    /projects/{project_id}   project:read
PATCH  /projects/{project_id}   project:update
DELETE /projects/{project_id}   project:delete
```

`GET /projects` は、システム権限で `project:read` を持つユーザーには全プロジェクトを返し、それ以外のログインユーザーには所属プロジェクトのみ返します。

`project_code` は必須かつ一意です。プロジェクト一覧はユーザー一覧と同じくページングレスポンスを返し、一覧には `version` を含めません。

```text
GET /projects?page=1&page_size=20&q=sync&status=active
```

### Project Members

```text
POST   /projects/{project_id}/members
GET    /projects/{project_id}/members
PATCH  /projects/{project_id}/members/{user_id}
DELETE /projects/{project_id}/members/{user_id}
```

必要な権限:

```text
POST   /projects/{project_id}/members              project:invite_member
GET    /projects/{project_id}/members              project:read
PATCH  /projects/{project_id}/members/{user_id}    project:invite_member
DELETE /projects/{project_id}/members/{user_id}    project:remove_member
```

メンバー追加・更新では `role_id` ではなく project role の `role_key` を使います。

```json
{
  "user_id": 10,
  "role_key": "member"
}
```

メンバー削除は物理削除です。同じユーザーを再度メンバー追加できます。

### User Login

```text
POST /auth/login
```

リクエスト:

```json
{
  "email": "user@example.com",
  "password": "password123"
}
```

レスポンス:

```json
{
  "message": "Login successful",
  "access_token": "jwt-access-token",
  "token_type": "bearer"
}
```

`ALLOW_BEARER_TOKEN_RESPONSE=false` の場合:

```json
{
  "message": "Login successful"
}
```

### User Logout

```text
POST /auth/logout
```

認証Cookieを削除します。レスポンスbodyはありません。

### Current User

```text
GET /auth/me
```

認証Cookie、または許可設定時のAuthorization Bearer tokenから現在のユーザー情報を返します。

レスポンス:

```json
{
  "id": 1,
  "email": "user@example.com",
  "name": "User Name"
}
```

## 認証・パスワード

パスワード処理は `app/core/security.py` に集約しています。

- `get_password_hash()`: 平文パスワードをハッシュ化
- `verify_password()`: 平文パスワードとハッシュ済みパスワードを照合
- `create_access_token()`: JWT access tokenを作成
- `decode_access_token()`: JWT access tokenをdecode

ハッシュ方式は `pwdlib` の `PasswordHash.recommended()` を使い、現在は Argon2id が使用されます。

本番環境の認証Cookie設定例:

```env
APP_ENV=production
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SAMESITE=lax
CSRF_COOKIE_SECURE=true
CSRF_COOKIE_SAMESITE=lax
ALLOW_BEARER_TOKEN_RESPONSE=false
ALLOW_AUTHORIZATION_HEADER=false
SESSION_IDLE_TIMEOUT_MINUTES=30
SESSION_REFRESH_THRESHOLD_MINUTES=10
SESSION_ABSOLUTE_TIMEOUT_MINUTES=480
LOGIN_MAX_FAILED_ATTEMPTS=5
LOGIN_LOCK_MINUTES=15
```

## 認可

認可はRBACをベースにしています。ロール、権限、ロール権限はDBで管理し、初期値は `scripts.seed_rbac` で投入します。

主なテーブル:

- `roles`: `system_admin`, `project_admin`, `manager`, `member`, `viewer` など
- `permissions`: `user:create`, `project:read`, `task:update` など
- `role_permissions`: ロールと権限の対応
- `user_roles`: ユーザーとシステムロールの対応
- `projects`: プロジェクト
- `project_members`: プロジェクト所属とプロジェクト内ロール

未ログインは `401`、権限不足は `403` を返します。

## 例外ハンドリング

アプリ独自例外は `app/core/exceptions.py` に定義します。

例:

```python
class EmailAlreadyRegisteredError(BadRequestError):
    message = "Email already registered"
```

HTTPレスポンスへの変換は `app/core/exception_handlers.py` に集約しています。例外クラスは業務エラー名と `message` を持ち、HTTP status code は handler 側で決定します。

## ロギング

ロギング設定は `app/core/logging.py` にあります。

対応している機能:

- `LOG_LEVEL` による出力レベル制御
- `LOG_FORMAT=text|json`
- `LOG_FILE` による任意のファイル出力
- `X-Request-ID` の受け取り・自動生成
- ログへの `request_id` 付与
- レスポンスヘッダーへの `X-Request-ID` 付与
- `SQL_ECHO` によるSQLAlchemyログ制御

業務ログは必要な場所に絞って追加します。パスワード、ハッシュ値、トークンなどの秘匿情報はログに出しません。

## 監査ログ

重要操作は `audit_logs` テーブルへ監査ログとして保存します。通常のアプリケーションログとは別に、誰が、いつ、何を対象に、どの操作をしたかを追跡するための証跡です。

現時点の主な記録対象:

- ログイン成功、ログイン失敗、ログアウト
- 権限変更によるセッション失効
- ユーザー作成、更新、削除、システムロール変更
- プロジェクト作成、更新、削除
- プロジェクトメンバー追加、ロール変更、削除

監査ログには `request_id`, IPアドレス, User-Agent, 操作ユーザーID, 対象ユーザーID, プロジェクトID, リソース種別, リソースID, metadata を保存します。metadataには補足情報を入れますが、password、token、secret、cookie などの秘匿情報は保存しません。

古い監査ログは手動コマンドで削除できます。デフォルトはdry-runで、削除対象件数だけ確認します。

```bash
uv run python -m scripts.cleanup_audit_logs
```

実際に削除する場合は `--execute` を付けます。

```bash
uv run python -m scripts.cleanup_audit_logs --older-than-days 1095 --execute
```

一度に削除する件数を制限する場合:

```bash
uv run python -m scripts.cleanup_audit_logs --older-than-days 1095 --limit 1000 --execute
```

誤削除を防ぐため、`AUDIT_LOG_CLEANUP_MIN_DAYS` 未満の経過日数は指定できません。cronなどの自動実行はまだ設定していません。

## テスト

テストは専用のPostgreSQLコンテナで実行します。通常の `docker compose up` ではテストDBは起動しません。

テストDBは `postgres_test` service として定義されており、`profiles: ["test"]` が付いています。

```bash
docker compose --profile test up -d postgres_test
```

通常は直接起動せず、`pytest` を実行します。

```bash
uv run pytest
```

pytestの共通設定は `tests/conftest.py` にあります。pytest実行時に以下を行います。

1. `postgres_test` を起動
2. DB readyを待機
3. `.env.test` を読み込み
4. `alembic upgrade head` を実行
5. `pytest` を実行
6. `postgres_test` を停止・削除

`postgres_test` は `tmpfs` を使うため、毎回空のDBとして起動します。したがって、テスト実行のたびに最新migrationがクリーンなDBへ適用されます。

特定のテストだけ実行する場合:

```bash
uv run pytest tests/test_health.py
```

## Docstring 方針

関数やメソッドには Google style の Docstring を使います。

```python
def get_password_hash(password: str) -> str:
    """平文パスワードをハッシュ化する。

    Args:
        password: ハッシュ化する平文パスワード。

    Returns:
        ハッシュ化されたパスワード文字列。
    """
```

`models` や `schemas` のクラスには、`Args` や `Returns` は基本的に不要です。クラスの役割が分かる短い説明だけを書きます。

## 今後の主なTODO

- パスワードポリシーを実装する
  - 12文字以上
  - emailやユーザー名と同一のpassword禁止
  - 推測されやすいpassword禁止
- ファイルアップロード時の画像再エンコードや追加検証を検討する
