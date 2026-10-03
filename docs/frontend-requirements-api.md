# Syncnesto Frontend Requirements API Guide

このドキュメントは、フロントエンド実装で要件定義APIを扱うための仕様です。

## Requirements

要件定義APIはプロジェクト配下のリソースとして扱います。すべてのエンドポイントは `/projects/{project_id}` 配下です。

要件定義書:

```text
POST   /projects/{project_id}/requirement-documents                 requirement:create
GET    /projects/{project_id}/requirement-documents                 requirement:read, page/page_size/q/status対応
GET    /projects/{project_id}/requirement-documents/{document_id}   requirement:read
PATCH  /projects/{project_id}/requirement-documents/{document_id}   requirement:update, version必須
DELETE /projects/{project_id}/requirement-documents/{document_id}   requirement:delete
POST   /projects/{project_id}/requirement-documents/{document_id}/exports requirement:read
```

要件:

```text
POST   /projects/{project_id}/requirements                          requirement:create
GET    /projects/{project_id}/requirements                          requirement:read, page/page_size/document_id/section_id/q/status/requirement_type/priority/owner_id/sort/sort_by/sort_order対応
GET    /projects/{project_id}/requirements/{requirement_id}         requirement:read
GET    /projects/{project_id}/requirements/{requirement_id}/summary requirement:read
PATCH  /projects/{project_id}/requirements/{requirement_id}         requirement:update, version必須
DELETE /projects/{project_id}/requirements/{requirement_id}         requirement:delete
GET    /projects/{project_id}/requirements/{requirement_id}/revisions requirement:read
```

`RequirementRead` は `owner_id` に加え、任意の `owner: UserSummary | null` を返します。作成・一覧・取得・更新・未決事項からの昇格、summary内の要件が対象です。`owner` は既存のユーザー要約（id / name / email / avatar_url / user_type / is_active）形式で、担当者未設定またはユーザーが削除済みの場合は `null` です。一覧ではページ内の担当者を一括取得します。画面表示は `owner.name` を利用し、追加のユーザー検索や内部ID表示は不要です。入力には引き続き `owner_id` を使用し、認可・DB構造・履歴形式は変更しません。

要件定義セクション:

```text
POST   /projects/{project_id}/requirement-documents/{document_id}/sections            requirement:update
GET    /projects/{project_id}/requirement-documents/{document_id}/sections            requirement:read
PATCH  /projects/{project_id}/requirement-documents/{document_id}/sections/sort-order requirement:update
GET    /projects/{project_id}/requirement-sections/{section_id}                       requirement:read
PATCH  /projects/{project_id}/requirement-sections/{section_id}                       requirement:update, version必須
DELETE /projects/{project_id}/requirement-sections/{section_id}                       requirement:update
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
PATCH  /projects/{project_id}/requirements/{requirement_id}/links/{link_id} requirement:link
DELETE /projects/{project_id}/requirements/{requirement_id}/links/{link_id} requirement:link
```

要件関連:

```text
POST   /projects/{project_id}/requirements/{requirement_id}/relations              requirement:link
GET    /projects/{project_id}/requirements/{requirement_id}/relations              requirement:read
DELETE /projects/{project_id}/requirements/{requirement_id}/relations/{relation_id} requirement:link
```

要件関連作成リクエスト例:

```json
{
  "target_type": "requirement_item",
  "target_id": "2",
  "relation_type": "depends_on",
  "description": "REQ-002 に依存する。"
}
```

要件関連レスポンスでは `created_by` に加えて、コメントと同じ軽量ユーザー情報として `created_by_user` を返します。
一覧表示で関連先の名称を表示できるよう、`target_summary` に関連先の軽量表示情報も返します。関連先が削除済み、またはIDを解決できない場合は `null` です。

```json
{
  "id": 1,
  "document_id": 1,
  "source_requirement_id": 1,
  "target_type": "requirement_item",
  "target_id": "2",
  "target_summary": {
    "id": "2",
    "code": "REQ-002",
    "title": "ログインできる"
  },
  "relation_type": "depends_on",
  "description": "REQ-002 に依存する。",
  "created_by": 1,
  "created_by_user": {
    "id": 1,
    "name": "管理者",
    "email": "admin@example.com",
    "avatar_url": null
  },
  "created_at": "2026-07-02T00:00:00Z"
}
```

要件コメント:

```text
POST   /projects/{project_id}/requirements/{requirement_id}/comments              requirement:comment
GET    /projects/{project_id}/requirements/{requirement_id}/comments              requirement:read
DELETE /projects/{project_id}/requirements/{requirement_id}/comments/{comment_id} requirement:comment
```

要件コメントレスポンスでは、`user_id` に加えて表示用の軽量ユーザー情報 `user` を返します。ユーザーが削除済みなどで取得できない場合は `null` です。

```json
{
  "id": 1,
  "requirement_id": 1,
  "user_id": 1,
  "user": {
    "id": 1,
    "name": "山田 太郎",
    "email": "taro@example.com",
    "avatar_url": null
  },
  "comment": "この要件の確認をお願いします。",
  "created_at": "2026-05-21T10:00:00Z"
}
```

汎用コメント:

```text
POST   /projects/{project_id}/comments                         requirement:comment
GET    /projects/{project_id}/comments                         requirement:read, target_type/target_id必須
PATCH  /projects/{project_id}/comments/{comment_id}            requirement:comment, version必須
DELETE /projects/{project_id}/comments/{comment_id}            requirement:comment
POST   /projects/{project_id}/comments/{comment_id}/resolve    requirement:comment, version必須
POST   /projects/{project_id}/comments/{comment_id}/reopen     requirement:comment, version必須
```

汎用コメントの `target_type`:

```text
document
section
requirement_item
open_issue
```

要件詳細画面のコメントでは、`target_type=requirement_item` / `target_id=要件ID`
に対して `target_anchor` で画面内の対象を指定できます。`target_anchor.kind`
を指定した場合、バックエンドは参照先が対象要件に属していることを検証します。

```json
{
  "target_type": "requirement_item",
  "target_id": 1,
  "target_anchor": {
    "kind": "requirement_link",
    "link_id": 10,
    "label": "関連成果物: POST /auth/login"
  },
  "body": "このAPI仕様を確認してください。"
}
```

検証対象の `kind`:

```text
requirement_field    要件本体の項目
requirement_detail   実現内容。detail_id が対象要件に属すること
requirement_link     関連成果物。link_id が対象要件に属すること
requirement_relation 要件関連。relation_id が対象要件に属すること
requirement_task     関連タスク。relation_id または task_id が対象要件に紐づくこと
```

存在しないID、別要件のID、未紐づきのタスクIDを指定した場合は
`404 NOT_FOUND / Requirement comment target not found` を返します。

要件レビュー:

```text
POST   /projects/{project_id}/requirements/{requirement_id}/reviews             requirement:review
GET    /projects/{project_id}/requirements/{requirement_id}/reviews             requirement:read
PATCH  /projects/{project_id}/requirements/{requirement_id}/reviews/{review_id} requirement:review
DELETE /projects/{project_id}/requirements/{requirement_id}/reviews/{review_id} requirement:review
```

未決事項:

```text
POST   /projects/{project_id}/open-issues                              requirement:create
GET    /projects/{project_id}/open-issues                              requirement:read, page/page_size/document_id/q/status/assignee_id/due_date_from/due_date_to/related_requirement_id対応
GET    /projects/{project_id}/open-issues/{issue_id}                   requirement:read
PATCH  /projects/{project_id}/open-issues/{issue_id}                   requirement:update, version必須
DELETE /projects/{project_id}/open-issues/{issue_id}                   requirement:delete
POST   /projects/{project_id}/open-issues/{issue_id}/promote-to-requirement requirement:create, version必須
```

変更履歴:

```text
GET /projects/{project_id}/change-logs requirement:read, page/page_size/document_id/target_type/target_id/action/changed_by/changed_at_from/changed_at_to対応
```

`target_type` はAPIレスポンス用の安定コードで返します。クエリの `target_type` でも同じコードを指定できます。現時点の主な候補は以下です。

```text
requirement_document
requirement_section
requirement
requirement_detail
requirement_link
requirement_relation
requirement_review
requirement_open_issue
requirement_comment
```

`action` もAPIレスポンス用の安定コードで返します。クエリの `action` でも同じコードを指定できます。現時点の主な候補は以下です。

```text
created
updated
deleted
exported
sorted
promoted_to_requirement
comment_created
comment_updated
comment_deleted
comment_resolved
comment_reopened
approval_requested
approval_approved
approval_rejected
```

`field_name` は単一フィールドコードまたは `null` です。現時点の主な候補は以下です。

```text
title
document_code
status
purpose
author_id
reviewer_id
approver_id
sort_order
requirement_code
requirement_type
category
description
rationale
acceptance_criteria
priority
source
owner_id
issue_code
assignee_id
due_date
body
is_resolved
```

`old_value` / `new_value` はタスク変更履歴APIと同じ共通形式です。`status`、`priority`、`requirement_type` は `{ code, label }`、ユーザーID系フィールドは `{ id, label }` 形式で返せます。

要件定義書、セクション、要件、未決事項の更新では、1回の更新操作につき変更履歴は1件だけ作成されます。実際に値が変わった項目のみ `new_value.updated_fields` に入り、`old_value.snapshot` / `new_value.snapshot` にはその項目の変更前後値だけが入ります。

```json
{
  "action": "updated",
  "field_name": null,
  "old_value": {
    "snapshot": {
      "title": "旧タイトル",
      "priority": "should"
    }
  },
  "new_value": {
    "updated_fields": ["priority", "title"],
    "snapshot": {
      "title": "新タイトル",
      "priority": "must"
    }
  }
}
```

リクエストで送信されても実際の値が変わらなかった項目は `updated_fields` に含まれません。コメント、承認、出力、並び替えなどの操作履歴は各操作の専用形式を維持します。

要件定義書出力:

```json
{
  "format": "markdown",
  "include_comments": false,
  "include_change_logs": false
}
```

現時点で対応している `format` は `markdown` と `html` です。PDF出力やS3保存は未対応で、レスポンスとして文字列を返します。

```json
{
  "format": "markdown",
  "content": "# Syncnesto 要件定義書\n\n## 文書情報\n..."
}
```

出力に成功すると、変更履歴に `target_type=document`, `action=exported` が記録されます。

承認:

```text
GET  /projects/{project_id}/approvals                       requirement:read, page/page_size/target_type/target_id/status/approver_id対応
POST /projects/{project_id}/approvals/request               requirement:review
POST /projects/{project_id}/approvals/{approval_id}/approve  requirement:review
POST /projects/{project_id}/approvals/{approval_id}/reject   requirement:review
```

承認申請リクエスト例:

```json
{
  "target_type": "document",
  "target_id": 1,
  "approver_id": 2,
  "comment": "承認をお願いします。"
}
```

承認/差し戻しリクエスト例:

```json
{
  "comment": "承認します。"
}
```

承認の `target_type`:

```text
document
section
requirement_item
open_issue
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
  "section_id": 1,
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

`requirement_code` は作成時に省略可能です。省略した場合、バックエンドが要件定義書単位で `REQ-001` 形式のコードを自動採番します。後方互換のため作成時の明示指定は引き続き受け付けますが、フロントエンドでは送信しない方針です。作成後の `requirement_code` は表示専用で、更新APIでは変更できません。

要件定義セクション作成リクエスト例:

```json
{
  "title": "業務要件",
  "section_type": "business",
  "content": "業務要件の概要を記載する。",
  "sort_order": 10,
  "status": "draft"
}
```

要件定義セクションレスポンス例:

```json
{
  "id": 1,
  "document_id": 1,
  "title": "業務要件",
  "section_type": "business",
  "content": "業務要件の概要を記載する。",
  "sort_order": 10,
  "status": "draft",
  "version": 1,
  "created_by": 1,
  "updated_by": 1,
  "created_at": "2026-05-21T10:00:00Z",
  "updated_at": "2026-05-21T10:00:00Z"
}
```

要件をセクション配下に置く場合は、要件作成・更新リクエストで `section_id` を送ります。`section_id` は同じ要件定義書に属している必要があります。別要件定義書のセクションを指定した場合は `404 NOT_FOUND` です。

セクションの表示順更新リクエスト例:

```json
{
  "items": [
    {
      "section_id": 1,
      "sort_order": 20,
      "version": 1
    },
    {
      "section_id": 2,
      "sort_order": 10,
      "version": 1
    }
  ]
}
```

未決事項作成リクエスト例:

```json
{
  "document_id": 1,
  "title": "SSO対応範囲が未確定",
  "description": "SSOを初期リリースに含めるか確認する。",
  "impact_scope": "認証機能",
  "assignee_id": 1,
  "due_date": "2026-06-30",
  "status": "open"
}
```

`issue_code` は作成時に省略可能です。省略した場合、バックエンドが要件定義書単位で `ISSUE-001` 形式のIDを自動採番します。後方互換のため作成時の明示指定は引き続き受け付けますが、フロントエンドでは送信しない方針です。作成後の `issue_code` は表示専用で、更新APIでは変更できません。

未決事項レスポンス例:

```json
{
  "id": 1,
  "document_id": 1,
  "related_requirement_id": null,
  "issue_code": "ISSUE-001",
  "title": "SSO対応範囲が未確定",
  "description": "SSOを初期リリースに含めるか確認する。",
  "impact_scope": "認証機能",
  "assignee_id": 1,
  "due_date": "2026-06-30",
  "status": "open",
  "resolution": null,
  "version": 1,
  "created_by": 1,
  "updated_by": 1,
  "created_at": "2026-05-21T10:00:00Z",
  "updated_at": "2026-05-21T10:00:00Z"
}
```

未決事項を要件に昇格するリクエスト例:

```json
{
  "version": 1,
  "requirement_type": "functional",
  "section_id": 1,
  "priority": "must",
  "resolution": "初期リリースに含める。",
  "reason": "方針決定"
}
```

昇格時の `requirement_code` もバックエンドが要件定義書単位で自動採番します。昇格に成功すると、要件作成レスポンスと同じ形式を返します。未決事項は `status=resolved` になり、`related_requirement_id` に作成された要件IDが入ります。

変更履歴レスポンス例:

```json
{
  "items": [
    {
      "id": 1,
      "document_id": 1,
      "target_type": "requirement_open_issue",
      "target_id": 1,
      "action": "created",
      "field_name": null,
      "old_value": null,
      "new_value": {
        "issue_code": "ISSUE-001"
      },
      "reason": null,
      "changed_by": 1,
      "changed_by_user": {
        "id": 1,
        "name": "山田 太郎",
        "email": "yamada@example.com",
        "avatar_url": null
      },
      "changed_at": "2026-05-21T10:00:00Z"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20
}
```

汎用コメント作成リクエスト例:

```json
{
  "target_type": "section",
  "target_id": 1,
  "parent_comment_id": null,
  "body": "このセクションの説明を補足してください。"
}
```

汎用コメントレスポンス例:

```json
{
  "id": 1,
  "document_id": 1,
  "target_type": "section",
  "target_id": 1,
  "parent_comment_id": null,
  "body": "このセクションの説明を補足してください。",
  "author_id": 1,
  "author": {
    "id": 1,
    "name": "山田 太郎",
    "email": "taro@example.com",
    "avatar_url": null
  },
  "is_resolved": false,
  "version": 1,
  "created_at": "2026-05-21T10:00:00Z",
  "updated_at": "2026-05-21T10:00:00Z"
}
```

`author` は投稿者の表示用情報です。ユーザーが削除済みなどで取得できない場合は `null` です。

コメント更新リクエスト例:

```json
{
  "version": 1,
  "body": "このセクションの前提条件を補足してください。",
  "reason": "表現調整"
}
```

コメント解決/再オープンリクエスト例:

```json
{
  "version": 1,
  "reason": "対応済み"
}
```

汎用コメントは、対象が指定プロジェクト配下に存在する場合のみ作成・参照できます。別プロジェクトの対象や存在しない対象を指定した場合は `404 NOT_FOUND` です。

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
    "section_id": 1,
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
  "linked_id": "POST /auth/login",
  "linked_url": "https://example.com/api/auth-login",
  "status": "completed"
}
```

`linked_id` は画面名、APIパス、資料名などの参照名・識別子です。`linked_url` は成果物本体を開くための任意URLで、成果物完成後に後から追加できます。

`status` は成果物の状態を表します。未指定時は `unknown` です。

```text
unknown
not_started
in_progress
completed
verified
```

レビューの `status` は以下を想定しています。

```text
pending
approved
rejected
commented
```

## コメントのメンション

要件コメントと要件定義対象コメントのAPIに `mentions` を追加した。本文形式は維持し、UTF-16単位の出現位置とユーザーIDを送受信する。候補は既存メンバー検索APIの `mention_permission=requirement:read` で取得する。詳細は [コメントのメンションAPI](frontend-comment-mentions-api.md) を参照。
