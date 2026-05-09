# Syncnesto Backend

Syncnesto のバックエンドAPIです。FastAPI、SQLAlchemy、Alembic、PostgreSQL を使って実装しています。

現在は認証機能の土台として、ユーザー登録API、パスワードハッシュ化、共通例外ハンドラー、ロギング、テスト用PostgreSQL環境を整備しています。

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

```env
DB_USER=admin
DB_PASSWORD=admin
DB_NAME=syncnesto
DATABASE_URL=postgresql://admin:admin@localhost:5432/syncnesto
APP_NAME=Syncnesto API
APP_ENV=development
LOG_LEVEL=INFO
LOG_FORMAT=text
SQL_ECHO=false
AUTH_COOKIE_NAME=access_token
AUTH_COOKIE_SECURE=false
AUTH_COOKIE_SAMESITE=lax
ALLOW_BEARER_TOKEN_RESPONSE=true
ALLOW_AUTHORIZATION_HEADER=true
```

テスト用の設定は `.env.test` に置きます。`tests/conftest.py` がテスト実行時に読み込みます。

```env
DATABASE_URL=postgresql://admin:admin@localhost:5433/syncnesto_test
APP_ENV=test
LOG_LEVEL=WARNING
```

主な設定:

- `DATABASE_URL`: SQLAlchemy/Alembic が使用するDB接続URL
- `APP_ENV`: 実行環境名
- `LOG_LEVEL`: `DEBUG`, `INFO`, `WARNING`, `ERROR` など
- `LOG_FORMAT`: `text` または `json`
- `LOG_FILE`: 指定した場合はファイルにもログ出力
- `SQL_ECHO`: `true` の場合 SQLAlchemy のSQLログを出力
- `AUTH_COOKIE_NAME`: 認証Cookie名
- `AUTH_COOKIE_SECURE`: `true` の場合 Secure Cookie として発行
- `AUTH_COOKIE_SAMESITE`: Cookie の SameSite 属性
- `ALLOW_BEARER_TOKEN_RESPONSE`: `true` の場合ログインレスポンスbodyにもaccess tokenを返す
- `ALLOW_AUTHORIZATION_HEADER`: `true` の場合Authorizationヘッダー認証を許可する

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

ログイン成功時はHttpOnly Cookieにもaccess tokenをセットします。開発環境ではSwagger UIや手動検証をしやすくするため、レスポンスbodyにも `access_token` を返します。本番では `ALLOW_BEARER_TOKEN_RESPONSE=false` にして、bodyにはaccess tokenを返さない運用を想定しています。

### User Register

```text
POST /auth/register
```

リクエスト:

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
  "access_token": "jwt-access-token",
  "token_type": "bearer"
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
ALLOW_BEARER_TOKEN_RESPONSE=false
ALLOW_AUTHORIZATION_HEADER=false
```

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

- 認証必須API用dependencyを追加
- `GET /auth/me` を追加
- JWT decode失敗時の例外処理を追加
