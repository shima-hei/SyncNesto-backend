# Task計画行の更新と親課題の名称参照

## 計画専用更新

`PATCH /tasks/{task_id}/planning` は既存の `task:update` 権限、Cookie/CSRF、
有効なTenant・Project所属、楽観的排他を使う。旧 `PATCH /tasks/{task_id}` と
公開MCP41操作の入力・返却・同意範囲・実績日補完は変更しない。

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
