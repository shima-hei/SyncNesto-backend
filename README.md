# Syncnesto Backend

FastAPI を使って API を実装し、SQLAlchemy で DB モデルを扱い、Alembic でマイグレーションを管理する学習用バックエンドです。

このリポジトリは「まず土台を作り、機能は自分で実装していく」前提の最小構成です。README では、各ディレクトリの役割と、学習しながら実装を進める順番を整理します。

## ディレクトリ構成

```text
.
├── app/
│   ├── core/
│   ├── db/
│   ├── models/
│   ├── repositories/
│   ├── routers/
│   ├── schemas/
│   ├── services/
│   └── main.py
├── alembic/
├── alembic.ini
├── docker-compose.yml
└── README.md
```

## 各ディレクトリの役割

### `app/`

アプリケーション本体です。FastAPI の API 実装は基本的にこの配下に置きます。

### `app/main.py`

FastAPI アプリの起点です。

- `FastAPI()` の生成
- CORS などの共通設定
- Router の登録
- 起動時に読み込まれる `app` オブジェクトの公開

ここには細かい業務ロジックを書かず、全体の組み立てだけを書くのが基本です。

### `app/core/`

アプリ共通の設定を置く層です。

例:

- 環境変数の読み込み
- アプリ名や設定値
- 共通定数

今は `config.py` だけですが、後で認証設定や例外ハンドリングの共通処理を置くこともあります。

### `app/db/`

DB 接続まわりをまとめる層です。

- `base.py`: SQLAlchemy の `Base` 定義
- `session.py`: DB エンジンとセッション生成

DB に接続するための共通土台をここに置きます。

### `app/models/`

SQLAlchemy のモデルを置く層です。

例:

- `User`
- `Post`
- `Task`

ここは DB テーブルの構造を Python のクラスで表現する場所です。Pydantic の入出力定義とは分けます。

### `app/schemas/`

リクエストとレスポンスの型を置く層です。Pydantic を使います。

例:

- 作成用の `UserCreate`
- 更新用の `UserUpdate`
- 返却用の `UserRead`

`models` が DB 用、`schemas` が API 通信用、という役割分担です。

### `app/repositories/`

DB アクセス処理をまとめる層です。

例:

- 1件取得
- 一覧取得
- 作成
- 更新
- 削除

SQL を直接触る責務や SQLAlchemy のクエリを書く責務をここに寄せます。

### `app/services/`

業務ロジックを置く層です。

例:

- ユーザー作成時の重複チェック
- 作成前のバリデーション
- 複数テーブルにまたがる処理

「DB に保存するだけ」で終わらないロジックはここに集めると整理しやすくなります。

### `app/routers/`

API エンドポイントを定義する層です。

例:

- `GET /users`
- `POST /users`
- `GET /health`

HTTP リクエストを受けて、必要な service を呼び出し、レスポンスを返します。

### `alembic/`

Alembic のマイグレーション管理用ディレクトリです。

- `env.py`: Alembic の実行設定
- `versions/`: マイグレーションファイル

DB スキーマの変更履歴をここで管理します。

### `alembic.ini`

Alembic の設定ファイルです。DB 接続情報やマイグレーションスクリプトの場所を持ちます。

### `docker-compose.yml`

ローカル開発用の DB 起動設定です。今は PostgreSQL と pgAdmin を起動するために使います。

## この構成での責務の流れ

基本の流れは以下です。

1. `routers` がリクエストを受ける
2. `services` が業務ロジックを処理する
3. `repositories` が DB を読み書きする
4. `models` がテーブル構造を表現する
5. `schemas` が API の入出力を表現する

この分け方にしておくと、責務が混ざりにくくなります。

## 学習しながら実装するおすすめ手順

以下の順番で進めると、FastAPI と SQLAlchemy と Alembic のつながりが理解しやすいです。

### 1. アプリを起動できる状態にする

まずは FastAPI の最小 API が動く状態を確認します。

やること:

- `app/main.py` で FastAPI を起動
- `app/routers/health.py` のような疎通確認用 API を作る
- ブラウザで `/docs` を開いて Swagger UI を確認する

ここでは DB 接続まで頑張らず、API サーバが立つことだけ確認すれば十分です。

### 2. DB 接続の仕組みを理解する

次に `app/db/session.py` の意味を理解します。

やること:

- `create_engine` が何をしているか確認する
- `SessionLocal` が何かを調べる
- dependency として DB セッションを router に渡せるようにする

この段階では、まだ複雑な CRUD は要りません。

### 3. 1つのモデルを自分で実装する

最初の題材として `User` 1つに絞るのが妥当です。

やること:

- `app/models/user.py` にカラムを追加する
- `id`, `email`, `name` など最小限で始める
- DB テーブルとしてどう表現されるかを意識する

最初から多くのテーブルを作ると理解が散るので避けた方がいいです。

### 4. Pydantic schema を分けて作る

次に API の入力と出力を分けます。

やること:

- `UserCreate`
- `UserRead`
- 必要なら `UserUpdate`

「DB モデルと API の型は別物」と理解するのが重要です。

### 5. Repository を自分で書く

CRUD のうち、まずは `create` と `get_by_id` だけ実装します。

やること:

- `create_user`
- `get_user_by_id`
- `get_user_by_email`

この層では SQLAlchemy のクエリに慣れるのが目的です。

### 6. Service で業務ロジックを追加する

次に repository を直接 router から呼ばず、service を挟みます。

やること:

- 重複 email をチェックする
- エラー時の振る舞いを決める
- 今後ロジックが増えても router が肥大化しない形にする

ここで「なぜ service 層が必要か」が見えやすくなります。

### 7. Router で API を公開する

ここで初めて本格的な API を作ります。

おすすめ順:

1. `POST /users`
2. `GET /users/{user_id}`
3. `GET /users`
4. `PUT /users/{user_id}`
5. `DELETE /users/{user_id}`

最初は 2 本だけでも十分です。

### 8. Alembic でマイグレーションを管理する

モデルを書いたら、次は DB に反映します。

やること:

- `alembic/env.py` をアプリ構成に合わせる
- `Base.metadata` を Alembic に認識させる
- revision を作る
- upgrade で DB に適用する

ここで「モデルを書いただけでは DB は変わらない」ことを理解できます。

### 9. 1機能ずつ増やす

最初の `User` が動いたら、次の機能を足します。

候補:

- 認証
- Todo
- 投稿
- コメント

ただし毎回、以下の流れを守ると構成が崩れません。

1. model を作る
2. schema を作る
3. repository を作る
4. service を作る
5. router を作る
6. migration を作る

## 学習用としての進め方

このリポジトリは、完成品を一気に作るよりも、1機能ずつ自分で書いて理解する進め方が向いています。

おすすめは以下です。

- まず `User` の新規作成 API だけ自力で作る
- 次に 1 件取得 API を追加する
- その後に一覧取得へ進む
- 更新と削除は最後でよい

最初から全部作ろうとすると、FastAPI、Pydantic、SQLAlchemy、Alembic を同時に追うことになって負荷が高いです。最小の 1 本を通してから広げる方が学習効率は高いです。

## Docstring の書き方

関数やメソッドには Google style の Docstring を使います。

```python
def get_password_hash(password: str) -> str:
    """平文パスワードをbcryptでハッシュ化する。

    Args:
        password: ハッシュ化する平文パスワード。

    Returns:
        bcryptでハッシュ化されたパスワード文字列。
    """
```

`models` や `schemas` のクラスには、`Args` や `Returns` は基本的に不要です。クラスの役割が分かる短い説明だけを書きます。

```python
class UserCreate(UserBase):
    """ユーザー登録リクエストで受け取るschema。"""
```

フィールドは型名と変数名で意味が分かる場合、個別に説明を書きすぎないようにします。説明が多すぎると、型定義とコメントの二重管理になりやすいためです。

## 実装時の意識ポイント

- `routers` に業務ロジックを書きすぎない
- `services` に SQL を直接書きすぎない
- `models` と `schemas` を混ぜない
- マイグレーションは Alembic で管理する
- 1回で完璧を目指さず、まず動く最小単位を作る

## 次にやるとよいこと

最初の自習テーマとしては次が妥当です。

1. `User` モデルの項目を自分で決める
2. `UserCreate` と `UserRead` を自分で調整する
3. `POST /users` を実装する
4. Alembic で `users` テーブルの migration を作る
5. PostgreSQL に適用して動作確認する

必要になったら、その次の段階として `alembic/env.py` のつなぎ込みや、最初の `POST /users` 実装も一緒に進められます。
