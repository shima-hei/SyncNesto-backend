# 組織の監査ログ閲覧

`GET /tenants/current/audit-logs` は現在組織のOwner・管理者だけが利用する。
Cookie・サーバーセッション・`X-Tenant-ID` の所属確認を維持する。
運営者のsystem権限だけでは組織の閲覧権限を代替しない。

既存 `audit_logs` を利用するためmigrationは不要。ログの記録・保持期間・削除処理は変更しない。
組織未確定時のログイン等 `tenant_id=null` のIdentity共通ログは対象外。
閲覧APIは重要操作の既存記録を表示するもので、全アクセスを記録する機能ではない。

## 検索条件

- `event_type`: 操作種別の完全一致、1〜100文字。
- `actor_user_id` / `project_id`: 1〜2147483647。
- `created_from`: この日時以降。タイムゾーンを含める。
- `created_before`: この日時より前。開始より後を指定する。
- `page`: 1〜500。`page_size`: 1〜50、既定25。

Frontendは端末のタイムゾーンで開始日の午前0時・終了日の翌日午前0時をISO日時へ変換する。
夏時間の日も固定24時間を加算しない。条件変更時は1ページ目へ戻す。
新しい日時・大きいIDの順。新規記録が加わるとページ位置は移動するため再読み込みできる。

## 応答

`items` / `total` / `page` / `page_size` を返す。
各項目は日時、操作種別、組織内操作者の表示名・ID、案件の名称・ID、対象の種別・整数ID、
実行元、限定した補足情報。名前のjoinも同じ組織に限定し、共通Identityのプロフィールを返さない。
削除済み案件も証跡を残す。名称が取得できない操作者・案件はIDを表示する。

本文・自由記述のmetadata・IP・User-Agent・request_idは返さない。
`details` は既知の更新項目名、既知のロール、非負整数のversion・件数・保持日数、正規UUIDの対象IDだけ。
既存の将来用 `metadata.source=mcp` は実行元表示に利用できるが、MCP自体は未実装。

Frontendのquery keyは組織IDと検索条件を含め、AbortSignalを渡す。条件はlocalStorageへ保存しない。
登録・編集・削除・CSV出力はこの閲覧画面に含めない。
