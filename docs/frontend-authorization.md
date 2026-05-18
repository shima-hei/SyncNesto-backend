# Syncnesto Frontend Authorization Guide

このドキュメントは、フロントエンド実装で認証状態・認可状態を扱うための仕様です。

## 基本方針

バックエンドの最終認可は permission ベースで行います。フロントエンドのメニュー表示やボタン表示は role key ベースで制御します。

```text
フロントエンド:
role key を使ってメニューやボタンの表示を切り替える

バックエンド:
permission を使ってAPI実行可否を最終判定する
```

フロント側で表示制御していても、API実行時にはバックエンドが必ず再判定します。

## 認証Cookie

ログイン成功時、バックエンドは HttpOnly Cookie に JWT access token をセットします。

```http
Set-Cookie: access_token=<JWT>; HttpOnly; Path=/; SameSite=lax
```

Cookie名:

```text
access_token
```

Server Component / Server Guard からバックエンドへアクセスする場合は、ブラウザから受け取った Cookie をそのまま転送します。

```http
Cookie: access_token=<JWT>
```

## ログイン状態確認

ログイン状態確認には `/auth/me` を使います。

```http
GET /auth/me
```

未ログインの場合:

```http
401 Unauthorized
```

ログイン済みの場合:

```json
{
  "id": 1,
  "email": "admin@example.com",
  "name": "Admin",
  "version": 1,
  "department": "QA",
  "position": "テスト担当",
  "avatar_url": "https://example.com/avatar.png",
  "is_active": true,
  "last_login_at": "2026-05-17T10:00:00+09:00",
  "created_by": 1,
  "updated_by": 1,
  "system_roles": [
    {
      "key": "system_admin",
      "name": "システム管理者"
    }
  ]
}
```

## Role の扱い

role は `key` と `name` を分けています。

```text
key:
機械向けの安定識別子
UI分岐に使う
例: system_admin, project_admin, manager, member, viewer

name:
人間向けの表示名
プロフィールや管理画面の表示に使う
例: システム管理者, プロジェクト管理者, マネージャー
```

フロントエンドの条件分岐では `name` ではなく `key` を使ってください。

```ts
const isSystemAdmin = me.system_roles.some(
  (role) => role.key === "system_admin",
);
```

`roles.id` はフロントエンドの分岐に使わないでください。DBの自動採番IDは環境によって変わる可能性があります。

## System Role

`/auth/me` が返す `system_roles` は、システム全体に対するロールです。

現時点でフロントエンドが見る主な system role:

```text
system_admin
```

`system_admin` は全体管理者です。ユーザー管理、プロジェクト作成、全プロジェクト閲覧・更新・削除、全プロジェクトメンバー管理ができます。

## Project Role

プロジェクト内のロールは `project_members` に紐づきます。

主な project role:

```text
project_admin
manager
member
viewer
```

プロジェクトごとの role は、今後 `/projects` または `/projects/{project_id}/me` で返す想定です。現時点では `/auth/me` には含めません。

現在のproject role別の主な操作権限:

| role key | 表示名 | 主な操作権限 |
|---|---|---|
| `project_admin` | プロジェクト管理者 | プロジェクト閲覧/更新/削除、メンバー招待/削除、タスクCRUD、テスト設計書CRUD、テストケースCRUD/実行、ドキュメントCRUD |
| `manager` | マネージャー | プロジェクト閲覧、タスクCRUD、テスト設計書閲覧/作成/更新、テストケース閲覧/作成/更新/実行、ドキュメント閲覧/作成/更新 |
| `member` | メンバー | プロジェクト閲覧、タスク閲覧/作成/更新、テスト設計書閲覧/作成/更新、テストケース閲覧/実行、ドキュメント閲覧/作成/更新 |
| `viewer` | 閲覧者 | プロジェクト、タスク、テスト設計書、テストケース、ドキュメントの閲覧 |

## API別の認可

### Auth

```text
POST /auth/login   認可不要
POST /auth/logout  認可不要
GET  /auth/me      ログイン必須
PATCH /auth/me     ログイン必須、本人プロフィール更新、version必須
PUT   /auth/me/avatar ログイン必須、本人アイコン更新
```

### Users

`/users` 系は管理者向けのユーザー管理APIです。ログインユーザー本人のプロフィール更新には `/auth/me` を使います。

管理者用のユーザー作成・更新では、以下のプロフィール項目を扱えます。

```text
email
name
password
department
position
is_active
version
system_role_keys
```

`system_role_keys` は `role.id` ではなく role key を指定します。未指定または空配列の場合、システムロールは付与されません。`PATCH /users/{user_id}` では `system_role_keys` を送った場合だけロールを差し替えます。空配列を送るとシステムロールをすべて外します。

`created_by`, `updated_by`, `last_login_at` はサーバー側で設定します。

```text
POST   /users              user:create
GET    /users              user:read, page/page_size/q/is_active対応
GET    /users/{user_id}    user:read
PATCH  /users/{user_id}    user:update, version必須
DELETE /users/{user_id}    user:delete
```

`GET /users` は一覧用の軽量レスポンスです。

クエリ:

```text
page: 1以上。default 1
page_size: 1-100。default 20
q: email, name, department, position の部分一致検索
is_active: true/false
```

レスポンス例:

```json
{
  "items": [
    {
      "id": 1,
      "email": "user@example.com",
      "name": "User",
      "department": "QA",
      "position": "Tester",
      "avatar_url": "https://example.com/avatar.png",
      "is_active": true,
      "last_login_at": null,
      "system_roles": [
        {
          "key": "system_admin",
          "name": "システム管理者"
        }
      ]
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20
}
```

`GET /users/{user_id}` は詳細取得APIです。編集画面では詳細APIを使ってください。

詳細レスポンスでは一覧項目に加えて `version`, `created_by`, `updated_by` を返します。権限変更画面では `system_roles` を初期表示に使い、保存時は `system_role_keys` として送ってください。

### Projects

```text
POST   /projects                project:create
GET    /projects                ログイン必須
GET    /projects/{project_id}   project:read
PATCH  /projects/{project_id}   project:update, version必須
DELETE /projects/{project_id}   project:delete
```

`GET /projects` は、`project:read` を持つ system role のユーザーには全プロジェクトを返します。それ以外のログインユーザーには、所属プロジェクトのみ返します。

### Project Members

```text
POST   /projects/{project_id}/members              project:invite_member
GET    /projects/{project_id}/members              project:read
PATCH  /projects/{project_id}/members/{user_id}    project:invite_member, version必須
DELETE /projects/{project_id}/members/{user_id}    project:remove_member
```

## 排他制御

更新APIは楽観的排他制御を行います。バックエンドは取得レスポンスに `version` を含め、フロントエンドは更新時にその `version` をリクエストへ含めます。

対象:

```text
PATCH /auth/me
PATCH /users/{user_id}
PATCH /projects/{project_id}
PATCH /projects/{project_id}/members/{user_id}
```

更新リクエスト例:

```json
{
  "name": "Updated Project",
  "version": 1
}
```

更新成功時は `version` が1つ増えた最新リソースを返します。

```json
{
  "id": 1,
  "name": "Updated Project",
  "description": null,
  "version": 2
}
```

送信した `version` がDB上の最新値と一致しない場合、更新は行わず `409 Conflict` を返します。レスポンスの `current` にはDB上の最新リソースが入ります。

```http
409 Conflict
```

```json
{
  "message": "Resource version conflict",
  "code": "VERSION_CONFLICT",
  "current": {
    "id": 1,
    "name": "Latest Project",
    "description": null,
    "version": 2
  }
}
```

フロントエンドでは、`409` を受け取った場合に `current` を画面へ反映し、ユーザーに再編集または再送信を促してください。

## 本人プロフィール更新

ログインユーザー本人のプロフィール更新には `PATCH /auth/me` を使います。

更新可能フィールド:

```text
name
password
version
```

`email`, `department`, `position`, `is_active` は本人プロフィール更新では変更できません。`email` はログインIDとして扱うため、将来的に変更を許可する場合はメール確認などの追加フローを入れてから対応します。

リクエスト例:

```http
PATCH /auth/me
```

```json
{
  "name": "Updated Name",
  "password": "new-password",
  "version": 1
}
```

レスポンスは `/auth/me` の取得時と同じ形式です。

## ユーザーアイコン

ユーザーアイコン更新には `PUT /auth/me/avatar` を使います。

```http
PUT /auth/me/avatar
Content-Type: multipart/form-data
```

```text
file: image/png, image/jpeg, image/webp
```

許可する画像形式:

```text
image/png
image/jpeg
image/webp
```

最大サイズ:

```text
2MB
```

ユーザーアイコン削除には `DELETE /auth/me/avatar` を使います。

```http
DELETE /auth/me/avatar
```

削除時はDBのS3 keyを `default-avatar.png` に戻し、以前のユーザー固有画像をS3から削除します。すでにデフォルト画像の場合は、S3削除もDB更新も行いません。

バックエンドは画像をS3へ保存し、DBにはS3 keyだけを保存します。ユーザー作成時は `default-avatar.png` をデフォルトのS3 keyとして設定します。APIレスポンスの `avatar_url` は署名付きURLです。`GET /auth/me`, `GET /users`, `GET /users/{user_id}` では、ユーザーに設定されているS3 keyから署名付きURLを生成して返します。

`avatar_url` は有効期限付きなので、永続保存せず、画面表示時にAPIレスポンスから取得してください。

## フロントエンドでの表示制御例

全体管理メニュー:

```ts
const canShowAdminMenu = me.system_roles.some(
  (role) => role.key === "system_admin",
);
```

ユーザー管理メニュー:

```ts
const canManageUsers = me.system_roles.some(
  (role) => role.key === "system_admin",
);
```

プロジェクト内メニューは、今後プロジェクトごとの role key を受け取って制御します。

```ts
const canManageProject = ["system_admin", "project_admin"].includes(roleKey);
const canEditProject = ["system_admin", "project_admin"].includes(roleKey);
const canViewProject = [
  "system_admin",
  "project_admin",
  "manager",
  "member",
  "viewer",
].includes(roleKey);
```

## エラーハンドリング

未ログイン:

```http
401 Unauthorized
```

```json
{
  "message": "Invalid email or password",
  "code": "INVALID_CREDENTIALS"
}
```

権限不足:

```http
403 Forbidden
```

```json
{
  "message": "Forbidden",
  "code": "FORBIDDEN"
}
```

更新競合:

```http
409 Conflict
```

```json
{
  "message": "Resource version conflict",
  "code": "VERSION_CONFLICT",
  "current": {}
}
```

フロントエンドでは、`401` はログイン画面への誘導、`403` は権限なし表示、`409` は最新データの再表示として扱ってください。
