# システム管理画面向けAPI

システム運営者が全組織と共通Identityを確認する読み取りAPI。組織・Projectへの所属や業務Permissionは追加しない。
既存の `/tenants/management`、`/tenants/current/*`、ユーザーCRUDとMCPの契約は維持する。

## 認可と組織選択

すべての新規APIにsystem scopeの `tenant:manage` と `user:read` の両方を要求する。
本人の組織所属と `X-Tenant-ID` に依存せず、pathで明示した組織だけの管理情報を取得する。
組織Owner/Adminだけでは403。存在しない組織、削除済み・存在しないIdentityは404。
停止中の組織・所属・アカウントも運営用の参照対象とする。削除済みIdentityとremoved所属は一覧から除く。
既存のMFA・セッション・通常/デモDB分離・Project認可を維持する。

| GET | レスポンス |
| --- | --- |
| `/system/summary` | 組織数、ユーザー数、有効/停止内訳、有効な組織所属がないユーザー数 |
| `/system/tenants` | `items/total/page/page_size`。組織メタデータ、Owner、所属人数 |
| `/system/tenants/{tenant_id}` | 指定組織のメタデータ、Owner、所属人数 |
| `/system/tenants/{tenant_id}/members` | `items/total/page/page_size`。指定組織の所属プロフィールとアカウント状態 |
| `/system/users/{user_id}/memberships` | 複数組織への所属一覧。組織の状態と所属状態を別々に返す |

一覧のpageは1以上、page_sizeは1〜100（既定20）、qは255文字以内。
組織一覧のqは組織名/slug、所属一覧のqは組織内の氏名/共通氏名/メール/組織内の部署/役職。
statusは未指定、active、suspended。総数は同じ検索条件に対する件数でありページ内件数とは区別する。

## 件数と状態

- ユーザー数は共通Identityを1人として数え、複数所属による重複を避ける。
- `users_without_active_membership` は、active組織にactive所属が1件もない、削除されていないIdentityの人数。アカウント全体の有効/停止を問わない。
- `member_count` はremovedでない所属かつ削除されていないIdentityの人数。
- `active_member_count` は上記のうちactive所属かつ有効なIdentityの人数。組織自体の状態は `status` で別に返す。
- Owner一覧はactive所属かつ有効・未削除のIdentityに限る。停止中の組織でもOwnerの所属状況は参照できる。
- 所属一覧の `status` は組織内の状態。`account_is_active` は全組織に影響するアカウント状態。
- `last_login_at` は共通Identityの最終ログインで、組織別の利用時刻ではない。

`GET /users` に後方互換の任意query `without_active_membership=true` を追加。
管理ホームと同じ定義で有効な組織所属なしのIdentityを絞り込む。既定falseで従来の一覧を維持する。

## Frontendと配布

OpenAPIからOrvalを生成する。APIを先に配置した後でFrontendを配置する。今回の追加にDB migrationやPermission追加は不要。
システム設定・組織の機能許可・運営による組織所属の変更APIは今回の対象外。
