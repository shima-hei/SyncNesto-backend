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

## セットアップ

仮想環境を有効化します。

```bash
source venv/bin/activate
```

依存関係をインストールします。

```bash
pip install -e ".[dev]"
```

既存の `venv` を使う場合でも、依存を追加した後は上記を再実行してください。

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
alembic upgrade head
```

モデル変更からmigrationを作成する場合:

```bash
alembic revision --autogenerate -m "message"
```

Alembic は `.env` の `DATABASE_URL` を読み込みます。

## アプリ起動

```bash
uvicorn app.main:app --reload
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

## 認証・パスワード

パスワード処理は `app/core/security.py` に集約しています。

- `get_password_hash()`: 平文パスワードをハッシュ化
- `verify_password()`: 平文パスワードとハッシュ済みパスワードを照合

ハッシュ方式は `pwdlib` の `PasswordHash.recommended()` を使い、現在は Argon2id が使用されます。

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
pytest
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
pytest tests/test_health.py
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

- `POST /auth/register` のDB利用テストを追加
- `POST /auth/login` を追加
- JWT発行・検証を追加
- 認証必須API用dependencyを追加
