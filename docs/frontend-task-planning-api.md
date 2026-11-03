# Task計画行の更新と親課題の名称参照

## 計画専用更新

`PATCH /tasks/{task_id}/planning` は既存の `task:update` 権限、Cookie/CSRF、
有効なTenant・Project所属、楽観的排他を使う。旧 `PATCH /tasks/{task_id}` と
公開MCP41操作の入力・返却・同意範囲を維持する。

通常PATCHでは、状態・進捗・実績日・実績工数を明示編集したときに既存の実績日補完を行う。
名称や優先度、担当・予定などだけの更新では実績日・進捗を補完しない。
一覧の優先度編集も既存PATCHを使い、状態を変更しない限り状態フィールドを送らない。

```json
{
  "version": 3,
  "assignee_id": 2,
  "start_date": "2026-11-01",
  "due_date": "2026-11-05",
  "estimated_minutes": 0,
  "change_reason": "計画の調整"
}
```

- `version` は1以上の整数で必須。担当、開始予定日、終了予定日、見積（分）の
  うち1項目以上を指定する。`change_reason` は任意。対象外フィールドや空更新は `422`。
- 省略は保持、明示 `null` は解除、見積の `0` は明示した0。見積はDB Integerの
  格納範囲 `0..2147483647` の整数で、
  文字列・小数・真偽値を拒否する。片側日付や両日付未設定は許可する。
- 日付を変更するときだけ、変更後の両日付がある場合の開始予定日≦終了予定日を確認する。
  逆転は `400 BAD_REQUEST` で行全体を拒否する。別項目の保存で既存日付を補正しない。
- 新しい担当は有効な本人・同じ組織の有効な所属・有効なProject所属が必要。
  候補外は `400 BAD_REQUEST`。担当の省略と、変更のない既存担当は保持する。
- 状態、進捗率、実績日・実績工数、工程、工数根拠、親子・依存、要件関連を変更しない。
  見積入力が変わっても `estimated_basis` を `own` へ変えず、子や依存先へ予定を伝播しない。
- 1行を既存の変更履歴・監査・担当通知と同じtransactionで保存する。
  失敗時は部分保存しない。成功は既存 `TaskRead`、競合は既存の
  `409 VERSION_CONFLICT` / `current` 形式。全Task一括保存ではなく他行の成功を取り消さない。

FrontendはBackend OpenAPIからOrvalを生成する。生成コードは手編集しない。
成功Taskを取得版・詳細cacheへ反映し、一覧・ガント・工程工数の既存無効化で再取得する。
通信切断など結果不明時は自動再送せず、現在値を再取得して照合する。

## 親課題の名称

通常APIの `TaskRead.parent_summary` を追加する。

```json
{
  "parent_task_id": 12,
  "parent_summary": {
    "id": 12,
    "title": "親課題の名称",
    "task_code": "TASK-012"
  }
}
```

同一Projectの未削除かつ現在のTenant・Project認可から参照できる親だけをまとめて取得する。
親が一覧の別ページや絞り込み外でも名称を参照できる。root・削除済み・参照不可の親は `null`。
親の本文や担当等を含めず、Task行ごとの追加問い合わせを増やさない。
公開MCPは既存の明示したTask投影項目を使い、`parent_summary` を公開契約へ追加しない。

DB列、migration、role/permission、OAuth/flag、新しい依存は追加しない。
## 受入改善：差し戻し・案件別の種別（2026-11-04）

- `returned` は独立した「差し戻し」状態。既存の `task:update` を使い、既存Taskを別の状態から差し戻す更新には空白以外の `change_reason` が必要。担当・期限・進捗・実績を自動変更しない。理由を履歴へ保存し、現在の閲覧権限を持つ担当者へアプリ内通知 `task_returned` を一度だけ送る。閲覧者や別Tenantには権限を追加しない。
- ボード移動APIにも省略可能な `change_reason` を追加し、差し戻し時は通常Task更新と同じ検証・履歴・通知を使用する。新規Taskを差し戻しで直接作成する操作は拒否する。差し戻し中は完了扱いにせず、要件の実装進捗では作業中として集計する。固定版の承認・要件合否とは別の概念。
- `GET /projects/{id}/task-types` は既存 `task:read` で標準/独自/旧自由文字列の一覧を返す。`source=standard|custom|legacy`、`key`、`name`、`is_active`、独自種別の `version` を持つ。
- `POST /projects/{id}/task-types`（`name`）と `PATCH /projects/{id}/task-types/{key}`（`version,name,is_active`）は既存 `project:update` で管理する。キーはサーバーが `custom_` + UUID32桁で採番し、改名・非表示でも変えない。名前は前後空白を除き1〜80文字、同一案件内の大文字小文字を区別しない重複と標準名との重複は409。版不一致は従来通り409 `VERSION_CONFLICT/current`。使用中の種別を削除するAPIは設けない。
- 標準11種の内部キーと既存Task行は移行しない。`other` の表示だけ「タスク（一般）」とする。既に保存された自由文字列は再利用と表示を保持する。未登録の新しいキーをTask作成・変更から持ち込むことはできず、種別管理APIで登録する。管理した非表示種別は新規割当不可、既に割り当てたTaskの編集では同じキーを保持できる。別案件の独自キーは新規割当不可。
- TaskReadへ省略可能な `task_type_name` を追加。Taskの一覧/詳細/ガントでは表示名を一括取得し、改名・非表示後も同じキーを解決する。検索条件やMCPの更新入力には従来通り `task_type` の安定キーを使う。FrontendはOrvalを再生成する。
- migration `c71a8d9320ef` は空の `project_task_types` を追加する。既存Task・担当・日程・工数・履歴を更新せず、利用済みの種別を失うdowngradeは拒否する。配布はBackend migration/APIからFrontendの順とし、リリース用フラグ・新しい環境変数・依存は追加しない。
