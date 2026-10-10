# 通常ユーザーのMFA必須化と運営・組織・業務権限の整理

2026-10-10調査、2026-10-11追記。現行コード・ローカルDBの調査と、ユーザーとの認識合わせの記録。
両日の変更はドキュメントのみ。MFA、権限変更、migration、公開環境への適用は行っていない。

## 確定した方針

- システム管理者は、全体のユーザー・組織・システム設定・組織に許可する機能を管理する。
- **運営権限と業務権限は分離する。** システム管理者でも、組織内の要件・タスク・テスト等を扱うには、別途その組織の所属とProjectの権限が必要。
- **通常アカウント全員にMFAを必須にする。** 管理者・構成員・外部協力者や、Projectの閲覧・テスト実行権限による例外は設けない。
- 使い捨ての匿名デモはMFAの対象外。通常アカウントのデータを既存DB/Storageで扱い、デモだけを専用DB/Storageへ隔離する現在の設計を維持する。
- **初版からTOTPとPasskeyの両方を用意する。** 2026-10-11のユーザー回答で確定。TOTPだけで開始する前日の案を置き換える。
- 予備の認証アプリを事前登録できるようにする。認証手段と復旧コードを全て失った場合は、システム運営者が本人確認して復旧を支援する。組織管理者の単独解除・メールだけの解除は行わない。
- 今回は調査・方針・記録まで。以下のAPI・保存形式・復旧操作は実装案であり、まだ利用できない。

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

### 初版の方式と提案する利用手順

- 既存のパスワード認証・HttpOnly Cookie・サーバーセッション・BFF・CSRF・RBACを拡張する。
- 通常ユーザーは **パスワード + TOTPまたはPasskey** で確認する案。サービスとして両方式を提供し、毎回両方を要求しない。初回は少なくとも一方の登録と検証を必須にし、予備の手段と使い捨て復旧コードを用意する。パスワードなしのログインは今回の拡張に含めない。
- SMS送信や有料の認証サービス移行は前提にしない。認証情報の検証は現在のBackendで行う。
- MFA登録は組織所属ではなく共通Identityへ紐付ける。組織を切り替えても再登録しない。
- 初期パスワード設定 → MFA登録と初回コード確認 → 通常利用、の順に進める。既存ユーザーは初回のパスワード確認後に登録を要求する。
- パスワードだけの段階は、サーバー状態とHttpOnly Cookieに結び付けた短時間の限定challengeにする。通常セッションはMFA確認後に発行する。認証状態確認・登録/検証・ログアウト等に限定し、業務API・運営API・MCP同意へ入れない。Cookie認証の更新操作には既存のCSRF対策を維持する。
- Frontendのリダイレクトに加え、Backendの共通認証境界で強制する。直接API呼出しで登録画面を迂回できないようにする。

TOTPにはフィッシング耐性がない。PasskeyはWebAuthnのドメインとの結び付けと本人確認を検証する。
TOTPや復旧経路も利用可能なので、アカウントの全経路がフィッシング耐性を持つとは説明しない。
MFAだけでセッション窃取や権限設定ミスまで防げるとは扱わない。

### 保存・復旧・セッション

- MFA要素、短期challenge、復旧コード、セッションの検証済み要素・時刻・認証設定の版を管理する。DB変更はAlembic migrationで追加する。
- TOTP共有秘密はBackend管理の専用鍵で暗号化し、鍵の版を保持する。検証に秘密が必要なためハッシュだけでは保存しない。復旧コードはハッシュ化し、使用を原子的に一度だけ許可する。
- OTPの試行回数制限はIdentity・接続元で行う。検証済み時間区間の再利用や並行した復旧コード使用を拒否する。QR・共有秘密・OTP・復旧コードをログや監査本文へ出さない。
- 登録を有効化する前にコードを確認する。要素の追加・交換・復旧コード再発行は直近の再認証を要求し、必須MFAが単純な解除操作で無効にならないようにする。
- パスワード再設定・メール変更だけでMFAを解除しない。組織管理者による単独解除、メールだけで完了する解除も採用しない。端末を失った場合の運営者による本人確認・復旧申請の案は後述する。
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
2. 2026-10-11、初版からTOTP/Passkeyの両方を提供し、予備の認証アプリと本人確認を伴う運営者の復旧支援を用意すると回答済み。具体的な利用・復旧・移行手順の案は以下に記録する。
3. MFAのDB・認証API・制限状態・エラーコード・Frontend画面を設計し、[Frontend認証仕様](../frontend-authorization.md) とOrvalを実装に合わせて更新する。
4. 登録・検証・復旧・共通強制・MCP失効を実装する。境界を先に揃え、運営者自身の登録を確認してから公開の必須化を行う。
5. 権限表示・メニュー名、system RoleのPermission整理を進める。システム設定・組織の機能許可は範囲を決めて別途実装する。

検証はMFA未完了のAPI拒否、既存セッション/refreshの迂回、OTP再利用・復旧コードの競合、
復旧時の失効と監査、運営者とOwner/Admin/Memberの分離、複数組織、通常/匿名デモの境界へ絞る。
既存の全テストの棚卸しは今回の作業に含めない。

## 2026-10-11: 認証状態と画面・APIの具体案

前日の方針を確認してから、現在の認証Router・共通Dependency・SessionService・CSRF・MCP認証、
Frontendのlogin hook・Server Guard・AuthProvider・BFFを読み直した。DB・公開設定は変更していない。

| 状態 | 許可する操作 | 画面 |
| --- | --- | --- |
| 未認証 | パスワード入力、既存の再設定申請、匿名デモ開始 | ログイン |
| 初期パスワード設定待ち | 本人の初期設定とログアウトのみ。既存の再ログイン手順を維持 | 初期パスワード設定 |
| MFA未登録・パスワード確認済み | TOTP/Passkeyの初回登録・検証、復旧コード保存の確認、取消のみ | MFA登録 |
| MFA登録済み・パスワード確認済み | 登録済みのTOTP/Passkey、または復旧コードの検証、取消のみ | 追加認証 |
| MFA確認済み | 従来のRBACで許可された操作。機微な認証変更は直近の再認証を要求 | 通常画面・アカウント |
| 運営者の復旧支援後 | 短期の再登録要求とパスワードの検証によるMFA再登録のみ | MFA再登録 |

通常セッションをMFA待ち状態へ流用しない。別の短期CookieとDBのchallengeに状態・目的・本人を固定し、
ブラウザが送る `mfa_verified` 等のフラグを信用しない。初期パスワードの限定状態も業務へ通さない。
復旧コードによるログインは本人の復旧を監査し、予備手段の追加・必要に応じたMFA再登録へ誘導する。

### APIの責務案

以下は新規実装時の候補で、現行のエンドポイントではない。
具体的なschemaとHTTP statusは、Backend/Frontendを同時に実装するときに確定する。

| API群 | 責務 |
| --- | --- |
| 既存 `POST /auth/login` | パスワード検証後の次手順を返す。MFA未完了なら通常のaccess tokenを返さない |
| `GET /auth/flow` | Cookieで検証した限定状態と次手順だけを返す。組織・Role・業務情報は含めない |
| `/auth/mfa/totp/*` | 初回/予備アプリの登録、コード検証、失効 |
| `/auth/mfa/passkeys/*` | 登録/認証のoptionsと完了検証、名前変更、失効 |
| `/auth/mfa/recovery-codes/*` | 初回発行、確認済みの本人による再発行、一度限りの使用 |
| `/auth/mfa/reauth/*` | 認証手段の変更等のための再認証。目的と対象を固定する |
| `/users/{user_id}/mfa-recovery/*` | 専用system Permissionを持つ運営者の本人確認記録と復旧承認。組織Adminは不可 |

`/auth/me` は通常の本人情報APIとして維持し、MFA待ちのフロー取得に使わない。
既存の初期パスワード画面が必要とする限定取得は明示的に扱い、未完了状態に全ての本人情報を公開する変更はしない。
ログアウトは通常・限定Cookieとchallengeを破棄する。復旧・ログインの応答は `Cache-Control: no-store` とする。

### FrontendとBFFの変更点

- login hookは現在、パスワード成功後に `/auth/me` を取得して通常画面へ進む。MFAの次手順で分岐し、通常キャッシュの取得はMFA完了後にする。
- MFA登録・追加認証・再登録の画面は通常のprotected shellの外へ置く。限定状態をServer Guardでも検証する。
- AuthProvider・共通API clientは、MFA待ち・コード不一致・期限切れ・レート制限を通常セッション切れと区別する。リダイレクトの往復やMFA画面での通常認証キャッシュ復活を防ぐ。
- Backendの `should_check_csrf` とFrontendの `shouldValidateCsrfToken` は現在、通常の認証Cookieを判定している。限定Cookieによる全更新にもCSRF検証を適用する。登録optionsを生成する操作も対象にする。
- CookieはBFFの既存中継を利用し、Frontend JSへ認証Cookieを渡さない。ログアウト・Cookie切替・既存Cookie名との衝突を確認する。
- Codex同意画面への安全な戻り先を、パスワード → MFA → 同意まで保持する。任意の外部URLへ戻さない。
- アカウント画面に「セキュリティ」を設け、登録手段の名称・登録日・最終利用・予備手段・復旧コードの再発行をまとめる。秘密や復旧コードは一覧へ再表示しない。
- API契約の確定後にFrontend認証仕様を更新し、OpenAPIからOrvalを再生成する。生成物は手修正しない。

## 2026-10-11: Passkey・予備手段・復旧の具体案

### Passkey

- ブラウザのWebAuthnで登録/認証し、署名・challenge・RP ID・origin・本人とcredentialの対応をBackendで検証する。認証器による本人確認を必須にし、画面の成功通知だけでは認証済みにしない。
- RP IDは利用者が開くFrontendの固定ホストにする。現行の公開URLを使うなら `syncnesto.vercel.app` であり、Backendの `syncnesto-api.vercel.app` や共有ドメインの `vercel.app` ではない。originは設定した値に完全一致させる。
- 本番・ローカル・検証用の設定と資格情報を分離する。任意のPreviewホストやユーザー入力を本番のorigin許可へ追加しない。固定ドメインの変更時は再登録の影響を事前に案内する。
- 共有Identityにランダムな安定したuser handleを持たせる。メール変更でcredentialを作り直さず、メールや内部の連番をhandleに使わない。
- サーバーは公開鍵・credential ID・所有者・利用履歴・必要な認証器メタデータを保存する。Passkeyの秘密鍵は受け取らない。
- 複数端末・セキュリティキーを登録できるようにする。同期型の認証器も考慮し、署名カウンターだけの単純な比較で正常な利用を全て拒否しない。検証は保守されるWebAuthnライブラリを利用する。

### 予備の認証アプリと復旧コード

- 予備アプリは既存のQRを再表示する方式にせず、独立したTOTP秘密と登録名を持つ追加要素として登録し、そのコードを確認する。追加には既存要素での直近の再認証を要求する。
- ログイン時は登録済みのどれかで確認する。TOTPの同一コード再利用は要素ごとに拒否し、試行制限はアカウント全体にも適用する。
- 初回登録後に復旧コードを一度だけ表示し、保存を案内する。再発行は旧コードを全て無効化する。最後の有効な認証手段だけを削除して通常利用へ戻る操作は許可しない。
- 認証手段の変更は本人へ通知し、監査する。別要素への差し替えや復旧では旧セッション/MCPを失効させる。単なる予備手段追加と日常の再認証で全接続を切るかは、実装時に操作別の表で明示する。

### 全て失った場合の運営者による復旧支援

1. 本人からの問い合わせを受け付ける。問い合わせメールや組織Adminの申告だけで解除しない。
2. 運営者が、事前に確認した連絡経路・対面等を使って本人と対象Identityを照合する。公開情報や要件本文を知っていることだけを本人確認に使わない。確認できなければ復旧を承認しない。
3. MFA済みかつ直近の再認証を終えたシステム運営者が、専用Permissionで復旧を承認する。対象・理由・確認方法の分類・承認者・時刻を監査する。身分証の画像や秘密を監査に保存しない。
4. 対象Identityの既存セッション・限定challenge・MCP資格情報と認証手段を失効する。通常利用を復旧した扱いにはせず、短期・一度限りのMFA再登録要求を発行する。
5. 本人はパスワードと再登録要求を確認して新しい認証手段を登録する。必要なパスワード再設定は既存フローで別に行い、それだけでMFAを解除しない。
6. 新要素の検証・復旧コードの再発行後に通常ログインを許可する。本人通知と復旧完了の監査を残す。要求の期限切れ・再利用を拒否する。

特定組織だけの所属を削除する操作と、共通Identity全体の復旧を混同しない。
運営者自身が全手段を失った場合、別の運営者が支援する。
最後の運営者も失った場合は、検証済みバックアップと本番DBの運用権限を使う緊急手順を別途用意する。
通常APIへMFA不要の管理者ログイン・自己解除を設けない。

本人確認に使える事前の連絡経路、復旧要求の通知方法・有効期限、緊急手順は、公開で強制する前に運営者向けrunbookへ具体化する。
ユーザーが選択したのは復旧支援を初版へ含める方針であり、未作成の本人確認手順を運用開始済みとは扱わない。

### DB・原子性・MCPの実装単位

- `users` に認証設定の版とWebAuthn user handle、認証手段の保存先にTOTP/Passkeyごとの必要情報を追加する。challenge・復旧コード・復旧申請も目的と所有者を持つ。通常/デモの共通migrationへの影響を確認する。
- `sessions` とMCP接続に、確認した認証設定の版・MFA要素/確認時刻を保存する。MCPの現在のfingerprintはパスワード・メール等だけなので、MFA復旧/差し替え時の変更を含める。
- 古い通常セッションやMCP接続のMFA情報を、migrationで確認済みへ一括補完しない。通常ユーザーは再認証、匿名デモは明示した別経路で扱う。
- challenge/復旧コードの消費、MFA確認、通常セッション発行と監査を同一transactionで確定する。既存のSession Repositoryは内部commitがあるため、新処理の原子性を壊さないようcommit境界を限定して拡張する。
- 登録・認証・復旧・再認証のchallengeを相互流用させない。短い期限、一度限りの消費、Identity単位と信頼したIP単位の回数制限を共通で適用する。具体的な閾値は認証APIの実装時に固定し、テストする。
- MCPの同意だけでなく、code交換・refresh・Bearer検証に新しい認証状態の共通確認を入れる。通常ユーザーのMFA未登録を匿名デモの例外に読み替えない。

### 実装・移行の順序

1. Backendのモデル/migration・要素検証・限定状態・復旧処理を追加し、認可は現行の3層を維持する。
2. Frontendの登録/追加認証/アカウント設定/運営者の復旧画面と、両側のCSRF・状態遷移を接続する。
3. 通常APIとMCPの共通強制、既存認証の無効化を確認する。実ブラウザでPasskey登録/認証とTOTP・予備手段・復旧の流れを検証する。
4. 公開前に専用暗号鍵・固定RP/origin・バックアップ/復旧手順を準備し、運営者自身が主手段と予備を登録する。暗号鍵・QR・復旧コードをGitへ含めない。
5. 新旧Backend/Frontendの組み合わせが認証を迂回しない配布順を決め、登録案内と再ログインを伴って通常アカウント全員に強制する。コードを追加しただけで公開に適用済みとしない。
6. 権限表示・system Permissionの整理、組織の機能許可は別の変更として進める。MCPの申請資料へ新しい認証手順を反映する。

## 一次資料

- [OWASP MFA Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html): 要素交換・復旧がMFAの迂回にならない設計、TOTP・復旧コード。
- [NIST SP 800-63B-4: Authenticators](https://pages.nist.gov/800-63-4/sp800-63b/authenticators/): OTPの性質とフィッシング耐性、暗号認証器の条件。
- [W3C WebAuthn](https://www.w3.org/TR/webauthn-3/): 登録・認証結果をサーバーで検証する手順と認証器のメタデータ。

上記は実装方針の根拠であり、認証保証レベルや規格準拠の認証を取得したことを意味しない。
