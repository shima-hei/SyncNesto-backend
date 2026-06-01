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

本番相当の `ALLOW_BEARER_TOKEN_RESPONSE=false` では、ログインレスポンスbodyには成功メッセージだけを返します。

```json
{
  "message": "Login successful"
}
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
| `project_admin` | プロジェクト管理者 | プロジェクト閲覧/更新/削除、メンバー招待/削除、タスクCRUD、テスト設計書CRUD、テストケースCRUD/実行、ドキュメントCRUD、要件定義CRUD/コメント/レビュー/承認/リンク |
| `manager` | マネージャー | プロジェクト閲覧、タスクCRUD、テスト設計書閲覧/作成/更新、テストケース閲覧/作成/更新/実行、ドキュメント閲覧/作成/更新、要件定義閲覧/作成/更新/コメント/レビュー/リンク |
| `member` | メンバー | プロジェクト閲覧、タスク閲覧/作成/更新、テスト設計書閲覧/作成/更新、テストケース閲覧/実行、ドキュメント閲覧/作成/更新、要件定義閲覧/作成/更新/コメント/リンク |
| `viewer` | 閲覧者 | プロジェクト、タスク、テスト設計書、テストケース、ドキュメント、要件定義の閲覧 |

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
GET    /projects                ログイン必須、page/page_size/q/status対応
GET    /projects/{project_id}   project:read
GET    /projects/{project_id}/me ログイン必須
GET    /projects/{project_id}/member-users project:read, q/limit対応
PATCH  /projects/{project_id}   project:update, version必須
DELETE /projects/{project_id}   project:delete
```

`GET /projects` は、`project:read` を持つ system role のユーザーには全プロジェクトを返します。それ以外のログインユーザーには、所属プロジェクトのみ返します。

`project_code` は必須かつ一意のプロジェクト識別子です。一覧レスポンスには `version` を含めません。編集画面では `GET /projects/{project_id}` で詳細を取得してください。

`GET /projects/{project_id}/me` は、現在ログイン中のユーザーが対象プロジェクトで持つproject roleと、system_admin判定を返します。プロジェクト配下画面のメニューやボタン表示制御に使います。API実行可否はバックエンドが各エンドポイントで再判定します。

プロジェクトメンバーの場合:

```json
{
  "project_id": 1,
  "role": {
    "key": "manager",
    "name": "マネージャー"
  },
  "is_system_admin": false
}
```

`system_admin` で、対象プロジェクトのメンバーではない場合:

```json
{
  "project_id": 1,
  "role": null,
  "is_system_admin": true
}
```

未参加かつ `system_admin` でもない場合は `403 Forbidden`、プロジェクトが存在しない場合は `404 Not Found` を返します。

`GET /projects/{project_id}/member-users` は、対象プロジェクトに所属するユーザーを担当者選択用に返します。

クエリ:

```text
q: email, name の部分一致検索
limit: 1-100。default 20
```

レスポンス例:

```json
{
  "items": [
    {
      "id": 1,
      "email": "user@example.com",
      "name": "User Name",
      "avatar_url": "https://example.com/avatar.png",
      "is_active": true
    }
  ]
}
```

`GET /projects` のクエリ:

```text
page: 1以上。default 1
page_size: 1-100。default 20
q: project_code, name, description の部分一致検索
status: active/archived などのステータス絞り込み
```

レスポンス例:

```json
{
  "items": [
    {
      "id": 1,
      "project_code": "SYNC",
      "name": "Syncnesto",
      "description": "Backend project",
      "status": "active",
      "start_date": "2026-05-01",
      "end_date": null,
      "updated_at": "2026-05-19T10:00:00Z"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20
}
```

### Project Members

```text
POST   /projects/{project_id}/members              project:invite_member
GET    /projects/{project_id}/members              project:read
PATCH  /projects/{project_id}/members/{user_id}    project:invite_member, version必須
DELETE /projects/{project_id}/members/{user_id}    project:remove_member
```

メンバー追加・更新では `role_id` ではなく project role の `role_key` を送ります。

```json
{
  "user_id": 10,
  "role_key": "member"
}
```

レスポンスでは role key/name を返します。

```json
{
  "id": 1,
  "project_id": 1,
  "user_id": 10,
  "role": {
    "key": "member",
    "name": "メンバー"
  },
  "version": 1
}
```

`DELETE /projects/{project_id}/members/{user_id}` は物理削除です。同じユーザーを再度メンバー追加できます。

### Requirements

要件定義APIはプロジェクト配下のリソースとして扱います。すべてのエンドポイントは `/projects/{project_id}` 配下です。

要件定義書:

```text
POST   /projects/{project_id}/requirement-documents                 requirement:create
GET    /projects/{project_id}/requirement-documents                 requirement:read, page/page_size/q/status対応
GET    /projects/{project_id}/requirement-documents/{document_id}   requirement:read
PATCH  /projects/{project_id}/requirement-documents/{document_id}   requirement:update, version必須
DELETE /projects/{project_id}/requirement-documents/{document_id}   requirement:delete
```

要件:

```text
POST   /projects/{project_id}/requirements                          requirement:create
GET    /projects/{project_id}/requirements                          requirement:read, page/page_size/document_id/q/status/requirement_type対応
GET    /projects/{project_id}/requirements/{requirement_id}         requirement:read
GET    /projects/{project_id}/requirements/{requirement_id}/summary requirement:read
PATCH  /projects/{project_id}/requirements/{requirement_id}         requirement:update, version必須
DELETE /projects/{project_id}/requirements/{requirement_id}         requirement:delete
GET    /projects/{project_id}/requirements/{requirement_id}/revisions requirement:read
```

要件詳細:

```text
POST   /projects/{project_id}/requirements/{requirement_id}/details             requirement:update
GET    /projects/{project_id}/requirements/{requirement_id}/details             requirement:read
PATCH  /projects/{project_id}/requirements/{requirement_id}/details/{detail_id} requirement:update
DELETE /projects/{project_id}/requirements/{requirement_id}/details/{detail_id} requirement:update
```

要件リンク:

```text
POST   /projects/{project_id}/requirements/{requirement_id}/links           requirement:link
GET    /projects/{project_id}/requirements/{requirement_id}/links           requirement:read
DELETE /projects/{project_id}/requirements/{requirement_id}/links/{link_id} requirement:link
```

要件コメント:

```text
POST   /projects/{project_id}/requirements/{requirement_id}/comments              requirement:comment
GET    /projects/{project_id}/requirements/{requirement_id}/comments              requirement:read
DELETE /projects/{project_id}/requirements/{requirement_id}/comments/{comment_id} requirement:comment
```

要件レビュー:

```text
POST   /projects/{project_id}/requirements/{requirement_id}/reviews             requirement:review
GET    /projects/{project_id}/requirements/{requirement_id}/reviews             requirement:read
PATCH  /projects/{project_id}/requirements/{requirement_id}/reviews/{review_id} requirement:review
DELETE /projects/{project_id}/requirements/{requirement_id}/reviews/{review_id} requirement:review
```

`system_admin` は system permission により全プロジェクトの要件定義APIを操作できます。project roleでは、`project_admin` がすべて、`manager` が作成/更新/コメント/レビュー/リンク、`member` が作成/更新/コメント/リンク、`viewer` が閲覧のみ可能です。

要件定義書作成リクエスト例:

```json
{
  "title": "Syncnesto 要件定義書",
  "document_code": "RD-001",
  "status": "draft",
  "purpose": "業務要件と機能要件を管理する",
  "target_system_name": "Syncnesto",
  "client_name": "QA部門",
  "vendor_name": "Internal"
}
```

要件定義書レスポンスでは、`author_id`, `reviewer_id`, `approver_id` に加えて、担当者表示用の軽量ユーザー情報を返します。未設定または対象ユーザーが存在しない場合は `null` です。

```json
{
  "id": 1,
  "project_id": 1,
  "title": "Syncnesto 要件定義書",
  "document_code": "RD-001",
  "author_id": 1,
  "reviewer_id": 2,
  "approver_id": null,
  "author": {
    "id": 1,
    "email": "author@example.com",
    "name": "Author",
    "avatar_url": "https://example.com/author.png",
    "is_active": true
  },
  "reviewer": {
    "id": 2,
    "email": "reviewer@example.com",
    "name": "Reviewer",
    "avatar_url": null,
    "is_active": true
  },
  "approver": null,
  "version": 1
}
```

要件作成リクエスト例:

```json
{
  "document_id": 1,
  "requirement_code": "REQ-001",
  "requirement_type": "functional",
  "category": "auth",
  "title": "ログインできる",
  "description": "登録済みユーザーがメールアドレスとパスワードでログインできる。",
  "rationale": "認証済みユーザーのみ業務データへアクセスさせるため。",
  "acceptance_criteria": "正しい認証情報でログインするとHttpOnly Cookieが発行される。",
  "priority": "must",
  "status": "draft",
  "source": "業務ヒアリング",
  "owner_id": 1
}
```

要件更新時は `change_summary` と `reason` を送ると、改訂履歴に保存されます。

```json
{
  "title": "ログインできること",
  "version": 1,
  "change_summary": "タイトルを明確化",
  "reason": "レビュー指摘対応"
}
```

要件詳細画面の初期表示には `GET /projects/{project_id}/requirements/{requirement_id}/summary` を使います。要件本体と関連情報をまとめて返します。

```json
{
  "requirement": {
    "id": 1,
    "document_id": 1,
    "requirement_code": "REQ-001",
    "requirement_type": "functional",
    "category": "auth",
    "title": "ログインできる",
    "description": "登録済みユーザーがログインできる。",
    "priority": "must",
    "status": "draft",
    "version": 1,
    "created_at": "2026-05-21T10:00:00Z",
    "updated_at": "2026-05-21T10:00:00Z"
  },
  "details": [],
  "links": [],
  "comments": [],
  "reviews": [],
  "revisions": []
}
```

`summary` の `comments` と `revisions` は直近20件のみ返します。コメント一覧や改訂履歴を全件表示する画面では、個別APIを使ってください。

要件詳細は `detail_json` に種別ごとの差分情報を保存します。

```json
{
  "detail_type": "screen",
  "detail_json": {
    "screen_name": "ログイン画面",
    "url_path": "/login",
    "input_items": ["email", "password"],
    "actions": ["login"]
  }
}
```

要件リンクは成果物とのトレーサビリティに使います。

```json
{
  "linked_type": "api",
  "linked_id": "POST /auth/login"
}
```

レビューの `status` は以下を想定しています。

```text
pending
approved
rejected
commented
```

## 排他制御

更新APIは楽観的排他制御を行います。バックエンドは取得レスポンスに `version` を含め、フロントエンドは更新時にその `version` をリクエストへ含めます。

対象:

```text
PATCH /auth/me
PATCH /users/{user_id}
PATCH /projects/{project_id}
PATCH /projects/{project_id}/members/{user_id}
PATCH /projects/{project_id}/requirement-documents/{document_id}
PATCH /projects/{project_id}/requirements/{requirement_id}
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

## 重複エラー

一意であるべき値が既に存在する場合、バックエンドは `409 Conflict` を返します。排他制御の `409` とは `code` で区別してください。

```http
409 Conflict
```

```json
{
  "message": "Requirement document code already exists",
  "code": "DUPLICATE_RESOURCE"
}
```

主な発生例:

```text
project_code が既に存在する
同一プロジェクト内の document_code が既に存在する
同一要件定義書内の requirement_code が既に存在する
同一プロジェクトへ同じユーザーを追加しようとした
```

論理削除済みの行とDBの一意制約が衝突した場合も、バックエンドは `500` ではなく `409 DUPLICATE_RESOURCE` を返します。

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

ログイン失敗:

```http
401 Unauthorized
```

```json
{
  "message": "Invalid email or password",
  "code": "INVALID_CREDENTIALS"
}
```

未ログイン:

```http
401 Unauthorized
```

```json
{
  "message": "Authentication required",
  "code": "AUTHENTICATION_REQUIRED"
}
```

トークン期限切れ:

```http
401 Unauthorized
```

```json
{
  "message": "Token expired",
  "code": "TOKEN_EXPIRED"
}
```

不正トークン:

```http
401 Unauthorized
```

```json
{
  "message": "Invalid token",
  "code": "INVALID_TOKEN"
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

重複:

```http
409 Conflict
```

```json
{
  "message": "Resource already exists",
  "code": "DUPLICATE_RESOURCE"
}
```

フロントエンドでは、`401 INVALID_CREDENTIALS` はログイン画面の入力エラー、`401 AUTHENTICATION_REQUIRED` は未ログイン、`401 TOKEN_EXPIRED` はセッション期限切れ、`401 INVALID_TOKEN` はCookie破棄後の再ログイン誘導として扱ってください。`403` は権限なし表示として扱ってください。`409 VERSION_CONFLICT` は最新データの再表示、`409 DUPLICATE_RESOURCE` は入力値の重複エラーとして扱ってください。
