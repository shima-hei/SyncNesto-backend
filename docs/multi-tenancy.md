# 組織境界と移行手順

認証Identityはグローバルな `users` に保持し、メールアドレスの既存UNIQUE制約とCookie・サーバーセッションを維持する。所属は `tenant_members` の N:M とし、表示名・部署・役職・Role・利用状態を組織単位で管理する。Projectは必ず1つのTenantに所属する。ProjectのRole・Permissionと保存形式は維持する。

## 所有関係

直接所属するデータは `projects`、`notifications`、`drafts`、業務操作の `audit_logs`。Project配下の要件・タスク・テスト・コメント・実行・証跡・各中間テーブルは既存FKの所有経路で組織を判定する。冗長なtenant_idを各子テーブルへ追加しない。所有経路の正は `app/db/tenant_scope.py`。Identity、認証セッション、ログイン試行、リクエスト制限、RBACマスタは組織非依存。認証・運営操作の監査ログはtenant_idがNULLの場合もある。

Projectコードの一意性は `(tenant_id, project_code)`、所属は `(tenant_id, user_id)`、下書きは `(owner_user_id, tenant_id, scope_key)`。通知は組織・宛先・日時の複合インデックスを追加する。削除済みProjectの通知もtenant_idを保持する。

現在のアプリケーションテーブル57個の分類（Alembic管理テーブルを除く）:

| 分類 | テーブル |
| --- | --- |
| 組織の本体・N:M所属 | `tenants`、`tenant_members` |
| 直接組織を保持 | `projects`、`notifications`、`drafts`、`audit_logs`（システム操作はNULL可） |
| Projectを親とする | `project_members`、`requirement_documents`、`tasks`、`boards`、`milestones`、`task_change_logs`、`test_designs` |
| 要件Document経由 | `requirement_sections`、`requirements`、`requirement_open_issues`、`requirement_approvals`、`requirement_change_logs`、`requirement_target_comments`、`requirement_relations` |
| 要件経由 | `requirement_revisions`、`requirement_details`、`requirement_links`、`requirement_reviews`、`requirement_comments` |
| テスト設計経由 | `test_pattern_tables`、`test_items`、`test_factors`、`test_factor_levels`、`test_patterns`、`test_pattern_values`、`test_expected_values`、`test_pattern_expected_values`、`test_item_patterns`、`test_design_columns`、`test_design_layouts`、`test_cases`、`test_design_comments` |
| 上記の子・関連先経由 | `board_columns`、`task_comments`、`task_dependencies`、`requirement_task_relations`、`requirement_test_items`、`test_case_issues`、`test_executions`、`test_evidence`、`test_design_comment_changes`、`comment_mentions` |
| 共通Identity・認証・RBAC・運用 | `users`、`sessions`、`account_actions`、`login_attempts`、`roles`、`permissions`、`role_permissions`、`user_roles`、`request_limits` |

## 認可

`system_admin` はサービス運営者。Identityと組織メタデータの運営管理を行い、Projectの業務権限を自動取得しない。組織のOwner/Adminは組織内メンバー、Project作成、Project設定・所属の管理ができる。Owner委譲はOwnerだけに許可し、最後の有効なOwnerの降格・停止・所属削除は拒否する。所属変更はTenant行のロックで直列化し、versionで競合を検出する。

業務内容は認証・有効なTenant所属・有効なProject所属・Project Permissionの全てを要求する。組織の管理者でも、HOME・概要集計・履歴・要件・タスク・テスト・証跡はProject所属が必要。Projectを作成した本人にはProject管理者の所属を同じトランザクションで付与する。

組織の所属削除は論理状態 `removed` とし、その組織のProject所属を論理削除する。他組織のIdentity・プロフィール・所属・セッションには影響しない。再追加時に古いProject権限を自動復活させない。停止中は有効な組織Contextを取得できず、再開時は既存Project所属が利用可能になる。

組織作成・設定・所属追加/編集/削除・新規登録は既存の監査ヘルパーでactor、tenant、対象、日時を記録する。所属編集・削除にはRoleと利用状態の変更前後を含め、パスワード・トークンを含めない。

## 現在の組織

全業務Routerへ共通の `get_current_tenant` を登録する。`X-Tenant-ID` は選択候補であり、本人の有効な所属・Tenantのactive状態・tenant scopeのRoleで検証してからSessionのContextに固定する。未指定は有効な所属が1つの場合だけ互換的に選択する。複数所属は400、非所属・停止・無効な選択は403。bodyのtenant_idで所有者を変更しない。

ORM取得・更新・削除へ共通の組織条件を適用する。業務子リソースには本人のProject所属条件も適用する。flush時には元の行と全FK参照先の組織をまとめて検査し、別組織からの移動・参照を拒否する。認証・migration・seed・保持期間の清掃など、HTTP業務Contextを持たない運用処理は組織フィルタの対象外。新規業務APIは共通Router登録と既存Project認可を必須にする。raw SQLは共通ORM条件が適用されないため、既存の採番・タグ検索は認可済みProject/Document IDを前提として個別検証する。

通知の一覧・件数・個別既読・一括既読は現在組織かつ本人の宛先に限定する。閲覧権限を失った対象のスナップショット・本文抜粋・参照IDは伏せる。

業務データの新規・変更したUser FK（担当者・報告者・レビュー担当・操作ユーザー等）も有効な現在組織の所属を要求する。IdentityのIDだけで別組織のユーザーを指定しない。変更していない既存のUser FKは所属取消し後も履歴として保持できる。

## APIと画面

公開登録を行わない承認制の共有SaaSとして運用する。運営画面では申し込み確認後、`POST /tenants/issuance` によって組織と初期Ownerを発行し、案内メールを送る。既存の `POST /tenants` は登録済みOwnerとの組織作成APIとして維持する。追加の発行・再送・初回パスワード設定は `docs/email-approval.md` と `docs/frontend-authorization.md` を参照する。

| 操作 | API | 権限 |
| --- | --- | --- |
| 本人の組織一覧 | GET /tenants | ログイン本人 |
| 運営者の組織一覧・作成 | GET /tenants/management、POST /tenants | system `tenant:manage` |
| 現在組織の情報・設定 | GET /tenants/current、PATCH /tenants/current | 閲覧は所属、変更はOwner/Admin |
| 組織内メンバーの一覧・追加 | GET/POST /tenants/current/members | Owner/Admin |
| 組織内の編集・削除 | PATCH/DELETE /tenants/current/members/{user_id} | Owner/Admin、Owner保護あり |
| 新規Identityと所属の登録 | POST /tenants/current/users | Owner/Admin |

追加は既存ユーザーのメール完全一致。全Identityの部分検索は公開しない。新規ユーザー登録はIdentityと所属を原子的に作り、ランダムな初期パスワードを登録レスポンスだけに含める。監査ログや一覧には含めない。登録済みメールは共通Identityのため、複製せず所属追加を使う。

フロントはHeaderの組織選択、`/organization` のメンバー・組織設定、`/projects/management` の組織内Project管理、`/system/tenants` の運営管理を分離する。本人・組織ごとのQueryClientを使い、切替は新しいDocumentへ移動して画面State・未完了の古い画面処理・キャッシュを持ち越さない。選択はuser_idで名前空間を分けたsessionStorageに保存し、別タブへ干渉しない。切替URLのIDも所属一覧と照合する。ログアウト・セッション失効時も新しいDocumentへ遷移する。

認証Identity・個人プロフィール・アバターのキャッシュは共通のAuth QueryClientに置き、個人設定の保存結果をHeaderや編集画面へ即時反映する。業務画面で本人を参照する場合は共通Auth Contextを利用する。

設計書のIndexedDB下書きはユーザー・組織・Project・設計書で分離する。旧キーはBackendから現在組織の設計書を取得できた後だけ、IDとProjectを照合して引き継ぐ。localStorageの下書きキーにも組織を含める。

## Migrationと初期Owner

`c15a7e92d401` は既存User、Project、Project所属、業務リソースのIDを保持する。Tenantテーブル作成、nullable列追加、Default Tenant作成、既存データbackfill、FK、NOT NULL、INDEX/UNIQUEの順でAlembicの同じトランザクション内で適用する。既存Userは有効状態に応じたDefault Tenant Memberとなり、system_adminからOwnerへ一律昇格しない。

初期Ownerは別の明示的な運用処理で1人だけ設定する。既存の別Ownerがいる場合は初期化を拒否する。既存Identityが存在し、有効であることが前提。Owner追加のためにIdentityやProject所属を複製しない。

ローカルの例:

```sh
uv run alembic upgrade head
uv run python -c 'from scripts.seed_rbac import seed_roles_and_permissions; from app.repositories.rbac import RbacRepository; seed_roles_and_permissions(RbacRepository())'
uv run python -m scripts.bootstrap_tenant_owner --email admin@example.com
```

パスワードはmigrationやseedへ埋め込まない。初期設定で明示的に変更する場合だけ `--password-file` を指定する。設定時は対象Identityのセッションを失効する。平文ファイルは0600で管理し、リポジトリ・ログへ含めない。

本番では既存の検証済みdirect Neon migration Roleを利用する。バックアップ・リハーサル後、業務更新を止めてmigrationを適用し、infraの `scripts.deploy tenant-owner --email <指定Owner> [--password-file <0600ファイル>]` でRBACとOwnerを設定してから対応したBackend/Frontendを公開する。旧フロントは組織Headerを送らないため、複数所属のユーザーについては新フロントの公開まで業務APIが400になる。本番のOwnerメール・パスワードはコードに固定しない。

新しい組織にデータが作成された後の自動downgradeはデータ消失・コード重複につながるため禁止する。失敗時はAlembicのtransaction rollback、公開後の復旧はアプリ停止と検証済みバックアップからの復旧を使う。バックアップ後に発生した更新の再適用も含めて判断する。

## RLSの判断

今回はRLSを有効化していない。ローカル・既存テストはsuperuser/BYPASSRLSで、productionのruntime Roleは非superuser/非BYPASSRLS、migration Roleは別接続でBYPASSRLS。Repositoryが処理途中で複数回commitするため、transaction-localの設定を各transactionに再適用する基盤と、RLSが実際に働く非特権Roleのテストが必要になる。Context設定だけで防御できたと判断せず、先にHTTP共通認可・ORM条件・書込検証・Isolation Testで境界を確立する。RLS追加時はpool再利用・rollback・transaction再開・運用処理を含めて別途検証する。

## 後続検討

- メール本人確認・忘れたパスワードの復旧を追加した。詳細は [email-approval.md](email-approval.md)。現在と新しいメールの両方を使えない場合は、システム運営者による本人確認と緊急復旧が必要で、組織管理者には無条件のIdentity変更を許可しない。
- SMTP・ResendアダプターとローカルMailpitを追加した。独自ドメインを購入しない当面の送信元は `syncnesto@gmail.com` のGmail SMTPで、2段階認証と専用アプリパスワードを設定して実配信を確認する。ResendはドメインとAPIキーの準備後に利用できる。将来のSESへの切替、バウンス/Webhook、耐久キューによる自動再送、招待・承諾フローは後続で検討する。
- 本番用DB Roleを使ったRLS防御、組織状態の運営操作、組織規模に応じた管理一覧のページングを後続で検討する。
- ORMの一括更新には組織WHERE条件を適用するが、flush時のFK検証は適用されない。現在のHTTP経路は既読・論理削除などの安全な値だけを一括更新する。新しい一括更新でtenant_id・所有FK・User FKを外部入力から変更しない。専用ガードとRLSは追加防御として検討する。
- 組織内プロフィールは現在の所属管理画面へ反映する。Project担当者・コメント等の既存表示名は共有Identityのnameを利用しており、組織プロフィールの全業務画面への投影は後続のUI整合対応とする。

## 検証結果（2026-10-04）

- Backend全体: `uv run pytest -q` は621 passed、5 skipped、3 xfailed。既存のStarlette/httpx非推奨警告が1件。ruffとpyrightも成功。追加の見直しで、削除済みProjectの内容アクセス、最後の有効なProject管理者の並行降格、他組織まで巻き込むProject所属変更時のセッション失効を修正した。
- 全業務HTTP operationの組織Context必須化、存在する非所属/停止組織の選択、両組織に所属する本人の越境CRUD・一覧・検索・グラフ保存・export・アップロード許可・通知・HOME、親子IDの組み替え、ORM取得/一括更新/削除/参照先、User FK、最後のOwner保護を検証。
- Migrationは隔離したPostgreSQLで、既存ID・パスワード・Project Roleの保持と失敗時transaction rollbackを検証。実際のローカルDBバックアップを別DBに復元した移行リハーサルでも件数保持を確認してからローカルへ適用。
- ローカルはDefault Tenantのみ、既存User6人・Project2件・Project所属6件を保持。指定された `admin@example.com` だけをOwnerに設定。UI検証で作成した空の組織2個は、その検証用所属・監査行とともに削除済み。
- FrontendはOrval再生成後、format:check、typecheck、lint、build成功。ブラウザーでは組織作成後の選択肢更新、組織切替によるHOME/メンバーの切替、ライト/ダーク・390px幅、新規登録の保存中dismiss防止、プロフィール更新レスポンスによる共通認証キャッシュの即時反映、ログアウトを確認。プロフィールのキャッシュ確認と保存中の失敗確認にはAPIレスポンスのmockを使用し、既存プロフィールは変更していない。
- メール承認はローカルMailpitを使い、管理者の申請・旧メール承認・新メール確認・新メールへの忘れたパスワード申請・新パスワードでのログイン・使用済みリンク拒否まで実際のUI/APIで確認した。同一ページの別fragmentリンクで確認ステップが更新されない不具合も修正し、390px幅、fragment消去、no-referrer、ボタン操作前に変更が確定しないことを確認した。確認専用Identityとメールを削除し、既存User6人・Project2件・Project所属6件、初期Ownerの資格情報を保持した。
- 本番migration・Owner設定・パスワード設定・デプロイは未実行。RLSと外部メール配信は上記の判断と後続検討に従う。
