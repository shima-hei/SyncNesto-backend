# 通常ユーザーのMFA必須化と運営・組織・業務権限の整理

2026-10-10。現行コード・ローカルDBの調査と、ユーザーとの認識合わせの記録。
今回の変更はドキュメントのみ。MFA、権限変更、migration、公開環境への適用は行っていない。

## 確定した方針

- システム管理者は、全体のユーザー・組織・システム設定・組織に許可する機能を管理する。
- **運営権限と業務権限は分離する。** システム管理者でも、組織内の要件・タスク・テスト等を扱うには、別途その組織の所属とProjectの権限が必要。
- **通常アカウント全員にMFAを必須にする。** 管理者・構成員・外部協力者や、Projectの閲覧・テスト実行権限による例外は設けない。
- 使い捨ての匿名デモはMFAの対象外。通常アカウントのデータを既存DB/Storageで扱い、デモだけを専用DB/Storageへ隔離する現在の設計を維持する。
- 今回は調査・方針・記録まで。認証方式と復旧運用の詳細を確定してから実装する。

MFAは「本人をどう確認するか」、RBACは「確認した本人に何を許可するか」を担当する。
MFA完了を理由に権限を増やさず、管理者権限を理由にMFAを省略しない。

## 現在の権限モデル

ユーザーが排他的に3種類に分類される仕組みではなく、同じIdentityに次の権限を独立して割り当てる。

| 層 | 現行Role key / 所属 | 担当する範囲 |
| --- | --- | --- |
| システム運営 | `system_admin` / `user_roles` | 全体のIdentity・組織の発行と管理。組織所属やProject業務権限は自動取得しない |
| 組織 | `tenant_owner`・`tenant_admin`・`tenant_member` / `tenant_members` | Owner/Adminは現在組織の所属・プロフィール・設定、Project作成・設定・所属を管理。Owner委譲はOwnerのみ。Memberに組織管理権限はない |
| Project業務 | `project_admin`・`manager`・`member`・`viewer` / `project_members` | 所属Projectの要件・タスク・テスト・コメント等を各Permissionで判定 |

`user_type=internal|guest` は利用者の区分で、これだけでは編集・閲覧権限を決めない。
同じ人が運営者と組織Ownerを兼任したり、複数組織で別のRoleを持ったりできる。
組織Owner/Adminも業務内容の閲覧にはProject所属とPermissionが必要。
ただしProjectの設定・所属を管理できるため、自分へのProject所属付与を通じて業務権限を得る操作はあり得る。
「役職だけで自動的に業務を閲覧できること」と「監査される所属付与」は区別する。

### Backendで確認した境界

- [`require_system_permission`](../../app/core/auth.py) → [`AuthorizationService.has_system_permission`](../../app/services/authorization.py) → [`RbacRepository`](../../app/repositories/rbac.py) は、`scope=system` の本人のRoleだけを見る。組織Owner/Adminをシステム管理者へ読み替えない。
- [`get_current_tenant` / `require_tenant_admin`](../../app/core/tenant.py) は有効な組織と所属を要求する。システム管理者の所属なしの迂回はない。
- 同ファイルの `require_project_management` は、組織Owner/AdminにProjectの設定・所属管理を許可する。要件等の業務内容用の判定ではない。
- `AuthorizationService.has_project_permission` は有効な組織・所属・ProjectとProject所属のPermissionを要求する。システム管理者・組織管理者の自動通過はない。
- [`tenants` Router](../../app/routers/tenants.py) の運営操作はsystem `tenant:manage`、現在組織の管理はOwner/Admin。全体のIdentity管理は [`users` Router](../../app/routers/users.py) のsystem `user:*`。
- [`ProjectService`](../../app/services/project.py) は、Owner/Adminに組織内Projectの管理用メタデータ一覧を見せる。参加Projectの業務一覧・概要・要件等の認可とは別。

現行境界を維持する。[組織境界の仕様](../multi-tenancy.md) とも一致する。
既存の `tests/routers/test_tenants.py::test_new_identity_exact_add_and_operator_separation` は、
運営者が組織Ownerを兼任しても、Project所属を外すとタスク・概要は403、組織運営は200になることを検証している。
今回はテストを再実行した結果ではなく、既存テストの内容を読んだ確認。

### 初期データとローカルDB

- [`seed_initial_admin`](../../scripts/seed_rbac.py) はsystem `system_admin` だけを付与し、組織Ownerを作らない。
- [`c15a7e92d401` migration](../../alembic/versions/c15a7e92d401_add_tenant_boundaries.py) は既存ユーザーをDefault Tenantの `tenant_member` にする。システム管理者からOwnerへの自動昇格はない。
- [`bootstrap_tenant_owner`](../../scripts/bootstrap_tenant_owner.py) は、明示した既存ユーザーをOwnerにする別の運用処理。system Roleは変更しない。

2026-10-10、接続先がloopbackであることを確認し、読み取り専用transactionでローカルDBを集計した。
ユーザーのメール・パスワード等は出力せず、データは変更していない。

| 確認対象 | ローカル実測 |
| --- | --- |
| Alembic revision | `91a7d2b8c406` |
| `system_admin` を持つユーザー | 2人 |
| 有効な組織所属 | Owner 1件 / Admin 0件 / Member 5件 |
| システム管理者の組織内Role | 1人がOwnerを兼任、1人がMember |

したがって、ローカルで同じログインに両方の管理画面が見える原因は兼任データで説明できる。
初期seedがRoleを同一視しているわけではない。現在のデータだけから、誰がいつ兼任を設定したかまでは断定しない。
公開DBのユーザー別Role割当は今回確認していない。

### 整理が必要な点

1. `seed_rbac.py` は `system_admin` に業務用を含む全Permissionを列挙している。現行のProject認可では自動通過しないが、意図を誤解しやすい。運営用Permissionだけへの整理は、使用箇所・既存Role割当とmigration方針を確認してから別途実施する。
2. Frontendの組織管理は `TenantProvider.canManageTenant`、運営画面は `requireSystemAdmin` / `system_roles` で分離されている。一方でサイドバーの「管理」「組織の運営管理」「ユーザー管理」は範囲が分かりにくい。「システム運営」「全体のユーザー管理」と、現在組織の管理を明確に表示する案とする。
3. 本人の権限表示も、運営Role・現在組織のRole・ProjectのRoleを別々に示す案とする。兼任を単一の「管理者」表示へまとめない。Owner委譲と最後のOwner保護は維持する。
4. 組織管理者はパスワード再設定・メール変更の申請を開始できる。現行の [`AccountActionService`](../../app/services/account_action.py) は現在メールへの確認等と、完了時の管理権限再検証を行う。変更されるIdentityは組織間で共通なので、MFA復旧を組織管理者の単独操作で迂回できる設計にしない。
5. システム設定と組織ごとの機能許可は未実装。現在のTenantモデルに機能契約・許可一覧はない。画面名の整理だけで実装済みと扱わない。

Frontendの確認箇所は同じワークスペースの `syncnesto-frontend` 内の
`features/auth/utils/authorization.ts`、`features/tenants/providers/tenant-provider.tsx`、
`lib/auth/server.ts`、`features/app-shell/components/layout/app-sidebar.tsx`、
`features/app-shell/constants/navigation.ts`。Frontendのファイルは今回変更していない。

## MFAの現状と実装案

### 現状

Syncnesto自身の通常ユーザー向けMFAは未実装。
[`auth` Router](../../app/routers/auth.py) はパスワード認証後に通常セッションを発行する。
[`get_current_user`](../../app/core/auth.py) の追加制約は初期パスワード変更で、MFA完了判定はない。
[`sessions` モデル](../../app/models/session.py) に認証要素・MFA確認時刻等はない。
[`McpAuthService`](../../app/services/mcp_auth.py) もブラウザ同意・接続検証でMFAを確認しない。
Vercel・GitHub・Neon・Supabaseの管理アカウントに設定したMFAとは別の機能。

### 提案する初版

- 既存のパスワード認証・HttpOnly Cookie・サーバーセッション・BFF・CSRF・RBACを拡張する。
- 通常ユーザーは **パスワード + 認証アプリのTOTP** を基本とし、使い捨ての復旧コードを用意する。SMS送信や有料の認証サービス移行を前提にしない。方式はまだ合意前。
- MFA登録は組織所属ではなく共通Identityへ紐付ける。組織を切り替えても再登録しない。
- 初期パスワード設定 → MFA登録と初回コード確認 → 通常利用、の順に進める。既存ユーザーは初回のパスワード確認後に登録を要求する。
- パスワードだけの段階は、サーバー状態とHttpOnly Cookieに結び付けた短時間の限定challengeにする。通常セッションはMFA確認後に発行する。認証状態確認・登録/検証・ログアウト等に限定し、業務API・運営API・MCP同意へ入れない。Cookie認証の更新操作には既存のCSRF対策を維持する。
- Frontendのリダイレクトに加え、Backendの共通認証境界で強制する。直接API呼出しで登録画面を迂回できないようにする。

TOTPにはフィッシング耐性がない。ドメインに結び付いたWebAuthn/Passkeyも選択肢だが、
初版から採用するかは端末対応・復旧手順・実装範囲を合わせて決める。
MFAだけでセッション窃取や権限設定ミスまで防げるとは扱わない。

### 保存・復旧・セッション

- MFA要素、短期challenge、復旧コード、セッションの検証済み要素・時刻・認証設定の版を管理する。DB変更はAlembic migrationで追加する。
- TOTP共有秘密はBackend管理の専用鍵で暗号化し、鍵の版を保持する。検証に秘密が必要なためハッシュだけでは保存しない。復旧コードはハッシュ化し、使用を原子的に一度だけ許可する。
- OTPの試行回数制限はIdentity・接続元で行う。検証済み時間区間の再利用や並行した復旧コード使用を拒否する。QR・共有秘密・OTP・復旧コードをログや監査本文へ出さない。
- 登録を有効化する前にコードを確認する。要素の追加・交換・復旧コード再発行は直近の再認証を要求し、必須MFAが単純な解除操作で無効にならないようにする。
- パスワード再設定・メール変更だけでMFAを解除しない。組織管理者による単独解除、メールだけで完了する解除も採用しない。端末を失った場合の運営者による本人確認・復旧申請の手順は実装前に確定する。
- MFA強制開始時は、既存の通常セッションとMCP接続にも適用する。既存認証を失効するか、認証設定の版を共通検証し、旧セッション・refresh tokenだけで使い続けられないようにする。
- MFA復旧等で認証の安全性が変わった場合は、対象Identityのセッション・MCP資格情報を失効させ、本人へ通知して監査する。秘密は記録しない。

### MCPへの適用

- 同意画面はMFA済みの通常ユーザーだけが使う。Bearer検証・認可コード交換・refreshでも現在のMFA状態と認証設定の版を確認する。
- MFAはブラウザ側で完了させ、OTPや復旧コードをCodexのツール引数へ渡さない。ツールを呼ぶたびにOTP入力を要求する設計にはしない。
- 本人の現在の組織/Project権限、同意Project、client・scope・期限・失効確認を維持する。編集権限のある外部協力者も利用可、閲覧専用・テスト実行専用・匿名デモは引き続き接続不可。
- 審査用アカウント・接続手順・規約草案・正常/異常系シナリオへMFAを反映してからOpenAIへの申請を再開する。

## 組織に許可する機能の案

運営者が組織単位で許可する機能と、ユーザーのRole/Permissionを分離する。
例えば、組織にテスト設計を許可しても、その組織の全員に編集権限を与えない。
利用可否は「組織に機能を許可」かつ「本人に必要なProject権限」かつ「有効な所属・認証」で判定する。
機能が無効ならBackendのAPIとMCPの両方で制限し、ボタンの非表示だけに頼らない。

初版で管理する機能の一覧と、無効化後の既存データの閲覧・export・再有効化の扱いは未確定。
運営画面・機能許可の保存モデル・migrationは、この範囲を決めてから設計する。

## 次に進める順序と確認事項

1. 本記録でRoleの兼任と管理範囲を認識合わせする。運営/業務の分離、通常全員のMFA・匿名デモ除外は回答済み。
2. 初版の認証方式（TOTP案またはPasskeyを含めるか）、端末紛失時の本人確認・復旧、移行時の登録案内を決める。
3. MFAのDB・認証API・制限状態・エラーコード・Frontend画面を設計し、[Frontend認証仕様](../frontend-authorization.md) とOrvalを実装に合わせて更新する。
4. 登録・検証・復旧・共通強制・MCP失効を実装する。境界を先に揃え、運営者自身の登録を確認してから公開の必須化を行う。
5. 権限表示・メニュー名、system RoleのPermission整理を進める。システム設定・組織の機能許可は範囲を決めて別途実装する。

検証はMFA未完了のAPI拒否、既存セッション/refreshの迂回、OTP再利用・復旧コードの競合、
復旧時の失効と監査、運営者とOwner/Admin/Memberの分離、複数組織、通常/匿名デモの境界へ絞る。
既存の全テストの棚卸しは今回の作業に含めない。

## 一次資料

- [OWASP MFA Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html): 要素交換・復旧がMFAの迂回にならない設計、TOTP・復旧コード。
- [NIST SP 800-63B-4: Authenticators](https://pages.nist.gov/800-63-4/sp800-63b/authenticators/): OTPの性質とフィッシング耐性、暗号認証器の条件。

上記は実装方針の根拠であり、認証保証レベルや規格準拠の認証を取得したことを意味しない。
