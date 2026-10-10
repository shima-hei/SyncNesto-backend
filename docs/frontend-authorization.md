# Syncnesto Frontend Authorization Guide

MCPは通常Cookie認証と別のOAuth委任を使用します。接続本人の現在のProject権限と許可Projectの積集合で毎回認可します。
`mcp:connect` に加えて業務の編集権限が必要で、閲覧専用・テスト実行専用は対象外、編集権限のあるguestは利用可能です。
Cookie/BFF/CSRFは同意・接続管理に維持し、通常APIのAuthorization headerを有効にしません。詳細は [MCP仕様・接続手順](mcp.md) を参照してください。
MCP本体は既存Backendの `/mcp` で提供します。公開resourceはissuerと同じoriginのHTTPS URLです。
アカウントの「外部サービス連携 → Codex」が連携入口です。紹介URL未設定時は公開準備中にします。
`GET /integrations/mcp/availability` は通常Cookie/BFFで `{ "can_connect": true|false }` を返し、
同意候補と同じ現在のProject権限を検証します。デモは拒否します。
同意取得の `McpConsentRead.redirect_uri` は要求時に検証したcallbackです。
Frontendは承認・拒否レスポンスのredirect先がこのURLと一致することを確認します。
既存loopbackのほか、plugin専用clientにはBackendで完全一致登録したOpenAI HTTPS callbackを許可します。
これらのブラウザAPIのCookie・BFF・CSRF境界は維持します。追加migrationは不要です。
Bearer専用の直接経路は `/mcp` とresource metadataの完全一致に限定し、同意・取消のFrontend API契約は維持します。
Backend/Frontend両方のサーバー環境変数 `MCP_ENABLED=true` と再デプロイが有効化に必要です。
追加のFrontend実装・Orval再生成は不要です。リモート化では既存JSON APIの入出力やOpenAPIを変更していません。

通常ログインは既存DB・Storageを継続し、デモ開始と検証済みデモJWTだけを専用DB・Storageへ向けます。
JWTの署名鍵も分離します。`DEMO_MODE`はデモ機能の可否であり、通常ユーザーの接続先・メール・30日保持を切り替えません。
最新の合意と検証は [決定記録](decisions/2026-10-08-demo-mode.md) を参照してください。

ブラウザは既知のログイン状態を`X-Syncnesto-Data-Realm: normal | demo`で送ります。
Backendは署名検証したCookieと一致しない操作を403で拒否し、通常/デモを別タブで切り替えた後の旧画面からの更新・logoutを防ぎます。
このヘッダーを接続先選択や認可に使いません。`GET /auth/me`はヘッダーを付けず、新しいCookieの本人状態を再確認します。
既存APIクライアントのためヘッダーなしも許可し、通常の認証・所有範囲検証を適用します。

監査ログの `GET /tenants/current/audit-logs` は現在組織のOwner・管理者に限定します。
system権限だけでは閲覧できません。詳細は `frontend-audit-logs-api.md` を参照してください。

このドキュメントは、フロントエンド実装で認証状態・認可状態を扱うための仕様です。

組織境界と3階層Roleは [multi-tenancy.md](multi-tenancy.md)、メールによる承認とパスワード復旧は [email-approval.md](email-approval.md) を参照してください。system_adminは運営権限で、Projectの業務権限を自動取得しません。

## 公開環境の接続境界

`APP_ENV=production` では、FastAPIの業務APIは `X-Syncnesto-BFF-Key` を要求します。公開デモも `APP_ENV=production` + `DEMO_MODE=true` とし、同じ保護を維持します。Next.jsのBFFとServer Guardが、サーバー専用の `BFF_SHARED_SECRET` をこのヘッダーに設定します。ブラウザから送られた同名ヘッダーは上書きします。キーを `NEXT_PUBLIC_` 変数、レスポンス、ログへ含めないでください。このキーは既存のユーザー認証・permission・CSRF検証を置き換えません。

健康確認の `GET /`・`HEAD /` は共有キーを要求しません。内部運用の `GET /internal/demo/cleanup`・`GET /internal/trash/cleanup` は専用 `CRON_SECRET` のBearer認証を要求し、Cookie・BFF共有キーでは実行できません。通常データの定期回収は既定で無効で、対象組織の指定と対象確認を経て運用者が有効化します。詳細は [deleted-data-cleanup.md](deleted-data-cleanup.md) を参照してください。

環境名は `development` / `test` / `production` のみ許可します。
Vercel上ではPreviewも含め `production` が必須です。公開デモも同じ環境で
Secure Cookie、Cookie-only認証、明示したHost、DBの `verify-full` TLS、
回数制限、OpenAPI非公開など本番と同じ設定検証を適用します。
デモデータの作成・分離・破棄機能は `DEMO_MODE=true` で有効にし、公開保護の判定には使用しません。

キー未設定・不一致はDBへ到達する前に `403 FORBIDDEN` になります。Vercelでキーが未設定・32文字未満ならBFFは `503 SERVICE_UNAVAILABLE` を返します。ローカルでは共有キーを省略できます。

本番はIP単位にログイン10回/分、その他240回/分を制限し、超過は `429 RATE_LIMITED` と `Retry-After` を返します。BFFがVercelの `x-vercel-forwarded-for` から検証したIPだけを `X-Syncnesto-Client-IP` で転送します。このヘッダーもブラウザの入力を上書きし、バックエンドは共有キーの確認後に使用します。Server Guardの呼び出しは接続元IPを使います。既存のアカウント単位の失敗回数・ロックも維持します。

カウンターはPostgreSQLの `request_limits` に保存し、複数worker・Vercel instance・再起動をまたいで共有します。DBの時刻で1分の時間枠を決め、原子的なupsertで同時要求の超過を防ぎます。IPは `SECRET_KEY` によるHMACにして保存します。全体6000回/分の上限でIPを増やす要求による行数の増加も抑え、古い時間枠の行は次の要求で削除します。業務処理に入る前に独立した短いDBトランザクションが発生し、カウンターDBが使えない場合は `503 SERVICE_UNAVAILABLE` で停止します。同一プロセス内でも回数を先に確認します。分散攻撃や無料枠の消費を完全に防ぐものではありません。

本番起動には32文字以上の `BFF_SHARED_SECRET`・強い `SECRET_KEY`、Secureな認証/CSRF Cookie、Cookie-only認証、ワイルドカードを含まない `ALLOWED_HOSTS`、`sslmode=verify-full` のDB接続、`SQL_ECHO=false` が必須です。PostgreSQLのCAはOSの信頼ストアを優先し、存在しない環境ではcertifiを使います。必要なら `PGSSLROOTCERT` でCAファイルを指定します。Vercelではプラットフォームが設定した `VERCEL_URL`・`VERCEL_PROJECT_PRODUCTION_URL` も許可Hostに追加します。本番ではSwagger・ReDoc・OpenAPIルートと開発用CORSを無効化します。APIクライアント生成は開発環境のOpenAPIを使います。

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
Set-Cookie: access_token=<JWT>; HttpOnly; Path=/; SameSite=Lax
```

本番環境で `AUTH_COOKIE_SECURE=true` の場合は `Secure` も付与されます。

```http
Set-Cookie: access_token=<JWT>; HttpOnly; Secure; Path=/; SameSite=Lax
```

本番相当の `ALLOW_BEARER_TOKEN_RESPONSE=false` では、ログインレスポンスbodyに成功メッセージと初回設定の必要性を返します。JWTは返しません。

```json
{
  "message": "Login successful",
  "password_change_required": false
}
```

Cookie名:

```text
access_token
```

Cookieに入るJWTには、ユーザー識別子 `sub` とDBセッションID `sid` が含まれます。バックエンドは `sid` を使って `sessions` テーブルの状態を確認します。初回ログインのJWTは署名付きの `password_setup_only` を保持し、本人の初回設定状態が変わっても業務セッションへ昇格しません。

## 承認制の発行と初回パスワード設定

公開の新規登録・組織作成は提供しません。運営者が申し込みを確認した後に `/tenants/issuance` で組織と初期Ownerを発行します。新規Identityの初回パスワードはランダム生成・7日間有効で、登録メールへ案内します。既存IdentityをOwnerに指定した場合は共有パスワード・氏名・System Roleを変更しません。

ログイン結果の `password_change_required=true` は `/initial-password` へ誘導します。`GET /auth/me` にも `password_change_required` と `initial_password_expires_at` を追加します。既存ユーザーのmigration値はfalse/nullで、初回設定を要求しません。運営・組織管理者による新規登録にも初回設定を適用します。

初回設定前に利用できる認証済みAPIは本人状態の参照・初回設定・ログアウトです。業務・組織・運営・プロフィール変更APIは `403 PASSWORD_CHANGE_REQUIRED`。FrontendのServer GuardとAPI clientの両方で設定画面へ誘導し、Backendの共通認証Dependencyが最終的に拒否します。

`POST /auth/initial-password` は `{current_password,password}` を受け取り、ログインCookieとCSRFを要求します。新しいパスワードは12〜128文字・初回パスワードと異なることが必要です。Userロック下で初回パスワードを再検証し、本人の設定、全セッション・確認リンクの失効を原子的に保存します。成功時はCookieも削除し、新しいパスワードで再ログインします。

期限切れの初回パスワードによるログイン・設定は `403 INITIAL_PASSWORD_EXPIRED`。本人は既存の `/forgot-password` からメール確認で再設定できます。この場合も初回設定状態を解除し、全セッションと確認リンクを失効します。

## CSRF対策

ログイン成功時、バックエンドは認証Cookieとは別にCSRF token用のCookieをセットします。

```http
Set-Cookie: csrf_token=<CSRF_TOKEN>; Path=/; SameSite=Lax
```

Cookie名:

```text
csrf_token
```

Header名:

```text
X-CSRF-Token
```

`csrf_token` はフロントエンド/BFFが読み取れるように `HttpOnly` を付けません。`access_token` は引き続き `HttpOnly` のため、フロントエンドから読まないでください。

更新系APIでは、ブラウザから受け取った `csrf_token` Cookie の値を `X-CSRF-Token` ヘッダーにも入れてバックエンドへ送ります。

```http
Cookie: access_token=<JWT>; csrf_token=<CSRF_TOKEN>
X-CSRF-Token: <CSRF_TOKEN>
```

CSRF検証対象:

```text
POST
PUT
PATCH
DELETE
```

CSRF検証対象外:

```text
GET
HEAD
OPTIONS
TRACE
POST /auth/login
```

認証CookieがないリクエストではCSRF検証ではなく、通常の認証エラーとして扱います。認証Cookieがある更新系APIで `X-CSRF-Token` が未指定、またはCookie値と一致しない場合は `403 CSRF_TOKEN_INVALID` を返します。

Next.js BFFでFastAPIを呼び出す場合は、BFF側でリクエストCookieから `csrf_token` を読み取り、FastAPIへのリクエストヘッダーに `X-CSRF-Token` として付与してください。

セッションは以下の方針で管理します。

```text
idle timeout:
一定時間操作がない場合に期限切れにする

sliding expiration:
期限切れが近い状態で認証済みAPIを呼び出した場合、DBセッションとCookieを延長する

absolute timeout:
操作が続いていても、ログインから一定時間を超えたら必ず期限切れにする
```

期限切れになったセッションは物理削除せず、`sessions.revoked_at` と `sessions.revoked_reason` を更新します。`401 TOKEN_EXPIRED` または `401 INVALID_TOKEN` では、バックエンドが削除用の `Set-Cookie` も返します。

```http
Set-Cookie: access_token=; Max-Age=0; HttpOnly; Path=/; SameSite=Lax
Set-Cookie: csrf_token=; Max-Age=0; Path=/; SameSite=Lax
```

システム権限変更時は対象ユーザーの既存セッションを失効します。バックエンドでは `sessions.revoked_reason` に `permission_changed` を保存します。運営者が共通Identityのメール・パスワード・有効状態を変更した場合も、更新と同じトランザクションで全セッションと未使用の本人確認リンクを失効し、理由は `credentials_changed` を保存します。両方を変更した場合のセッション理由は `permission_changed` です。組織・Project所属の変更は次回のAPI認可で即時反映し、他組織のログインを終了しません。

対象操作:

```text
PATCH  /users/{user_id}
  system_role_keys を変更した場合

```

権限変更されたユーザーは、次回API呼び出し時に `401 INVALID_TOKEN` になります。BFFは削除用 `Set-Cookie` をブラウザへ中継し、ログイン画面へ誘導してください。

フロントエンドは Next.js BFF でバックエンドの `Set-Cookie` をブラウザへ中継し、Cookie破棄後にログイン画面へ誘導してください。Cookieが存在しない `401 AUTHENTICATION_REQUIRED` では、削除用Cookieは必須ではありません。

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
  "user_type": "internal",
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

## User Type

`user_type` はシステム全体のユーザー区分です。role や permission ではありません。

主な user type:

```text
internal
guest
```

表示文言:

| user_type | 表示名 |
|---|---|
| `internal` | 社内 |
| `guest` | ゲスト |

`guest` は外部協力者や客先確認者などを表す横断属性です。`guest` であること自体ではプロジェクト権限を付与しません。プロジェクトの閲覧や操作可否は、従来どおり `project_members` に紐づく project role と permission で判定します。

```text
guest + project member viewer:
対象プロジェクトを閲覧可能

guest + project member なし:
対象プロジェクトは閲覧不可
```

`guest` には `system_admin` を付与できません。`POST /users` または `PATCH /users/{user_id}` で `user_type: "guest"` と `system_role_keys: ["system_admin"]` の組み合わせ、または既に `system_admin` を持つユーザーを `guest` に変更する操作は `400 BAD_REQUEST` を返します。

## System Role

`/auth/me` が返す `system_roles` は、システム全体に対するロールです。

現時点でフロントエンドが見る主な system role:

```text
system_admin
```

`system_admin` はシステム運営者です。共通Identityと組織メタデータを管理します。組織内の設定・所属管理はTenant Owner/Admin、業務内容の操作は本人のProject RoleとPermissionに基づきます。

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
| `project_admin` | プロジェクト管理者 | プロジェクト閲覧/更新/削除、メンバー招待/削除、タスクCRUD/コメント、テスト設計書CRUD、テストケースCRUD/実行、ドキュメントCRUD、要件定義CRUD/コメント/レビュー/承認/リンク |
| `manager` | マネージャー | プロジェクト閲覧、タスクCRUD/コメント、テスト設計書閲覧/作成/更新、テストケース閲覧/作成/更新/実行、ドキュメント閲覧/作成/更新、要件定義閲覧/作成/更新/コメント/レビュー/リンク |
| `member` | メンバー | プロジェクト閲覧、タスク閲覧/作成/更新/コメント、テスト設計書閲覧/作成/更新、テストケース閲覧/実行、ドキュメント閲覧/作成/更新、要件定義閲覧/作成/更新/コメント/リンク |
| `viewer` | 閲覧者 | プロジェクト、タスク、テスト設計書、テストケース、ドキュメント、要件定義の閲覧 |

## API別の認可

### テスト設計・テストケース

`/projects/{project_id}/test-designs`配下の設計書には既存の`test_plan:read/create/update/delete`を利用する。設計コメントの投稿・更新・削除・解決には`test_plan:comment`を要求し、閲覧と履歴取得には`test_plan:read`を要求する。`test_plan:comment`はproject_admin、manager、memberへ付与し、viewerには付与しない。コメント本文の編集・削除は投稿者本人またはProject管理権限のあるユーザーに限る。要件と項目の関連追加・解除には`requirement:link`、関連閲覧にはそれぞれ`requirement:read`または`test_plan:read`を要求する。ケースの閲覧・生成・結果更新・設計取り込みにはそれぞれ`test_case:read/create/execute/update`を要求する。実行証跡の閲覧・ダウンロードは`test_case:read`、登録・削除は`test_case:execute`を要求する。すべてプロジェクトスコープを検証し、既存のCookie・CSRF・セッション管理を適用する。設計書、ケース、コメントはそれぞれ独立したversionで競合検知する。詳細は[テスト設計API仕様](frontend-test-design-api.md)を参照。

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
user_type
is_active
version
system_role_keys
```

`system_role_keys` は `role.id` ではなく role key を指定します。未指定または空配列の場合、システムロールは付与されません。`PATCH /users/{user_id}` では `system_role_keys` を送った場合だけロールを差し替えます。空配列を送るとシステムロールをすべて外します。

`user_type` は未指定の場合 `internal` です。指定可能な値は `internal` / `guest` です。

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
      "user_type": "internal",
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
GET    /projects/{project_id}/member-candidates project:invite_member, q/limit対応
PATCH  /projects/{project_id}   project:update, version必須
DELETE /projects/{project_id}   project:delete
```

`GET /projects` は現在組織の本人参加Projectを返します。Owner/Adminは管理メタデータの一覧を取得できます。`member_only=true` は権限にかかわらず本人参加Projectだけを返します。業務内容の閲覧にはProject所属を要求します。

`project_code` は必須で、同じ組織内で一意のプロジェクト識別子です。一覧レスポンスには `version` を含めません。編集画面では `GET /projects/{project_id}` で詳細を取得してください。

`GET /projects/{project_id}/me` は本人のProject Roleを返します。互換フィールド `is_system_admin` はfalseです。プロジェクト配下画面のメニューやボタン表示制御に使います。API実行可否はバックエンドが各エンドポイントで再判定します。

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

`GET /projects/{project_id}/member-candidates` は、対象プロジェクトへ追加可能な有効ユーザーを返します。既に対象プロジェクトへ所属しているユーザーは含みません。`project_admin` がメンバー追加画面で候補検索するために使います。

クエリ:

```text
q: email, name の部分一致検索
limit: 1-100。default 20
```

レスポンス形式は `GET /projects/{project_id}/member-users` と同じです。

未参加の場合はsystem_adminでも `403 Forbidden` です。存在しないProject・削除済みProjectから業務内容へアクセスすることもできません。

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

最後の `project_admin` を降格または削除しようとした場合、バックエンドは `409 LAST_PROJECT_ADMIN_REQUIRED` を返します。

### Requirements

要件定義APIの詳細は `docs/frontend-requirements-api.md` を参照してください。

### Tasks

タスク、カンバンボード、ガントチャートAPIの詳細は `docs/frontend-tasks-api.md` を参照してください。

## 排他制御

更新APIは楽観的排他制御を行います。バックエンドは取得レスポンスに `version` を含め、フロントエンドは更新時にその `version` をリクエストへ含めます。

対象:

```text
PATCH /auth/me
PATCH /users/{user_id}
PATCH /projects/{project_id}
PATCH /projects/{project_id}/members/{user_id}
PATCH /projects/{project_id}/requirement-documents/{document_id}
PATCH /projects/{project_id}/requirement-documents/{document_id}/sections/sort-order
PATCH /projects/{project_id}/requirement-sections/{section_id}
PATCH /projects/{project_id}/requirements/{requirement_id}
PATCH /projects/{project_id}/open-issues/{issue_id}
POST  /projects/{project_id}/open-issues/{issue_id}/promote-to-requirement
PATCH /projects/{project_id}/comments/{comment_id}
POST  /projects/{project_id}/comments/{comment_id}/resolve
POST  /projects/{project_id}/comments/{comment_id}/reopen
PATCH /tasks/{task_id}
POST  /boards/{board_id}/tasks/{task_id}/move
PATCH /task-dependencies/{dependency_id}
PATCH /boards/{board_id}
PATCH /board-columns/{column_id}
PATCH /milestones/{milestone_id}
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
version
```

`email`, `password`, `department`, `position`, `is_active` は本人プロフィール更新では変更できません。パスワードはメール再設定、メールアドレスは旧メール承認と新メール確認のAPIを使います。契約外の項目は422です。

リクエスト例:

```http
PATCH /auth/me
```

```json
{
  "name": "Updated Name",
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

## 監査ログ

ログイン、ログアウト、ユーザー管理、プロジェクト管理、プロジェクトメンバー管理などの重要操作はバックエンド側で監査ログとして記録します。

現時点では監査ログはバックエンド内部の証跡であり、フロントエンドから追加で送信すべき項目はありません。`X-Request-ID` を送信した場合は監査ログにも保存されるため、BFFやフロントエンド側のログと突合しやすくなります。

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
const canManageProject = roleKey === "project_admin";
const canEditProject = roleKey === "project_admin";
const canViewProject = [
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
Set-Cookie: access_token=; Max-Age=0; HttpOnly; Path=/; SameSite=Lax
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
Set-Cookie: access_token=; Max-Age=0; HttpOnly; Path=/; SameSite=Lax
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

CSRF token不正:

```http
403 Forbidden
```

```json
{
  "message": "Invalid CSRF token",
  "code": "CSRF_TOKEN_INVALID"
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

フロントエンドでは、`401 INVALID_CREDENTIALS` はログイン画面の入力エラー、`401 AUTHENTICATION_REQUIRED` は未ログイン、`401 TOKEN_EXPIRED` はセッション期限切れ、`401 INVALID_TOKEN` はCookie破棄後の再ログイン誘導として扱ってください。`TOKEN_EXPIRED` / `INVALID_TOKEN` ではバックエンドの `Set-Cookie` をBFFからブラウザへ中継してください。`403 FORBIDDEN` は権限なし表示、`403 CSRF_TOKEN_INVALID` はCSRF tokenの再取得または再ログイン誘導として扱ってください。`409 VERSION_CONFLICT` は最新データの再表示、`409 DUPLICATE_RESOURCE` は入力値の重複エラーとして扱ってください。
# マルチテナント対応

現行の組織境界・3階層Role・変更されたsystem_adminの扱いは [multi-tenancy.md](multi-tenancy.md) を参照してください。業務APIには検証対象の `X-Tenant-ID` を送ります。運営者のsystem権限はProjectの業務権限を迂回しません。`/projects/{id}/me` の互換フィールド `is_system_admin` は常にfalseで、Project Roleを表示制御に使います。
