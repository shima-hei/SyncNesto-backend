# 工程・工数API

採用仕様の正本はProject5文書13 §9。工程は要件分類・親子・状態から独立し、Taskは単一工程、共通作業、未分類のいずれかを持つ。

## APIと認可

| API | 権限 |
| --- | --- |
| GET /projects/{project_id}/phases | task:read |
| POST /projects/{project_id}/phases | project:update |
| PATCH /projects/{project_id}/phases/{phase_id} | project:update |
| GET /projects/{project_id}/phases/{phase_id}/events | task:read |
| GET /projects/{project_id}/tasks/effort-summary | task:read |

既存の有効Tenant所属・Project所属を必須とし、system roleやmanagerへ業務権限を追加しない。工程一覧はpage/page_size（最大100）とinclude_archived（既定false）を持ち、items/total/page/page_sizeを返す。履歴は20件単位。工程名は前後空白を除き1〜100字、同一Projectのアーカイブを含め大文字小文字を区別せず一意。

POSTはname/sort_order（非負整数、既定0）。PATCHはversionを必須に、name/sort_order/archivedを部分更新する。省略は保持し、nullは422。競合は409 VERSION_CONFLICTと最新current、同名は409 DUPLICATE_RESOURCE。工程の物理削除APIは提供しない。アーカイブでもTask所属・工数・日程・versionは変えない。

PATCHと応答に工程の `color` と `description` を追加。色は `blue` / `violet` / `emerald` / `amber` / `rose` / `slate`、説明は最大2000字。これら2項目は省略で保持、`null` で解除。既存工程は両方未設定から始まり、変更値は工程履歴にも記録する。

## 工程一覧からのタスク移動

- `GET /projects/{project_id}/tasks` は任意の `phase_scope` と `phase_id` に対応。`phase_id` は `phase` 所属の同一案件の工程に限る。共通・未分類は `phase_scope=shared` / `unclassified` で取得する。通常のページングと権限を使う。
- `POST /projects/{project_id}/phases/transfer-tasks` は `task:update` で、`tasks` に1〜100件の `{id, version}` と `destination` に工程所属を受ける。競合時は全件をロールバックする。
- `GET /projects/{project_id}/phases/{phase_id}/archive-preview` で対象件数と確認用の `snapshot` を取得し、`POST .../{phase_id}/archive-with-transfer` へ `snapshot` と `destination` を送る。両方に `project:update` と `task:update` が必要。対象や版の変化は409。確認済みの所属変更・アーカイブ・履歴を同じトランザクションで保存する。従来の所属を保持するアーカイブも引き続き利用できる。

## 既存Task APIへの追加

作成/更新で任意のphase_assignmentを受ける。省略は既存所属を保持（新規は未分類）、nullは未分類へ解除。objectはkind=unclassified/shared/phaseで、phaseだけphase_id必須、他はnull。別Project/不明工程は404、新しいアーカイブ工程への割当は400。同じ工程行のロックでアーカイブとの競合を直列化し、Task更新は従来のversion/409 current/変更履歴/監査を維持する。

estimated_basis/actual_basisはそれぞれown/rollup/unreviewed。新規はown、省略更新は保持、nullは422。既存の見積/実績分数は変更しない。既存の親で値がある場合だけ、削除済みの子も含めてunreviewedへ移行する。工数根拠は通常のTask更新権限内で変更する。

TaskReadとガント/ボード/409 currentにはphase_scope/phase_id/phase_name/phase_archivedと両basisを追加する。effort_totalsは自分と子孫の有効Taskの算出値で、単一Task読取で計算し、複数Taskの一覧ではnull（未算出）とする。一覧の各行をrootに重複して再帰集計せず、全体・工程別にはeffort-summaryを使う。算出値を入力欄へ書き戻さない。削除済みの中間親の下にある有効な子孫も保持する。工程変更は子へ自動伝播しない。

## 集計

effort-summaryは一覧と同じq/status/task_type/priority/assignee_id/requirement_id/parent_task_id/root_only/start_date_from/due_date_to/overdue/tagを受ける。ページ・折り畳みと独立した一意Task集合を先に確定し、ownの分数を1回だけ加算する。同額の別Taskは両方計上。rollupの保存値とunreviewedは加算しない。nullと明示0を区別する。

各metricはminutes/missing_count/unreviewed_count/complete。未入力・未確認があれば確認済み小計として扱う。groupsは工程・共通・未分類へ分け、アーカイブ分も含む和とtotalsを一致させる。filtersとunit=minutesを返す。進捗率・親達成率は更新しない。

status_groupsは同じ一意集合の状態別内訳。完了・中止を既定で除外せず、各内訳の和はtotalsと一致する。工程別と状態別を同じSQL結果から作り、同時更新で両者の対象をずらさない。

工程一覧の `completed_task_count` は未削除の `done` を数える。分母の `task_count` は中止を含む全所属タスク。ガントの期間やフィルターとは独立する。

## 工数見積

- `GET /tasks/{task_id}/estimate?page=1` は `task:read`。現在値、Taskの `version`、改訂履歴のページを返す。従来の見積値は初回の改訂まで単純見積として表示する。
- `GET /projects/{project_id}/task-estimate-methods?task_ids=1&task_ids=2` は `task:read`。同じ案件の未削除タスクに絞った手法を取得する。
- `PATCH /tasks/{task_id}/estimate` は `task:update`。Task版、`simple` / `pert` / `triangular` と手法に応じた分単位の非負整数を受ける。3点は楽観≦最頻≦悲観。計算結果を最寄りの分に丸め、Taskの `estimated_minutes`、見積の入力・前提・変更理由・更新者、既存監査を同一トランザクションで保存する。
- 詳細見積があるタスクの `estimated_minutes` を従来のタスク編集や計画APIから変更すると、新しい値を単純見積として履歴に追加する。明示した `null` は解除履歴として保持する。見積の変更では日程・実績・工数の集計根拠を補完しない。

### 基準見積

- `GET /tasks/{task_id}/estimate/baselines?page=1` は `task:read`。最新の確定版と確定履歴を20件単位で取得する。確定版は見積の保存内容、確定者、確定日時、任意の理由を返す。
- `POST /tasks/{task_id}/estimate/baselines` は `task:update` と `project:update` の両方が必要。確認した `estimate_version`、`expected_baseline_id`（初回は `null`）、任意の `reason` を送る。同じ見積版の再送では確定履歴が増えない。確認後に対象の版が進んだ場合は `409 VERSION_CONFLICT` を返す。
- 確定は既存タスクの見積改訂を参照し、確定履歴を追加する。以前の確定版は引き続き参照できる。数値だけが保存されている旧タスクでは、確定時に単純見積として保存内容を記録する。見積未入力の場合は保存から始める。タスク更新と確定を同じ行のロックで直列化する。

公開MCPはmcp_task_contract.pyで従来のTask入力を固定し、業務APIの新項目を公開しない。通常/デモの新表はtenant_scopeの所有関係に登録し、既存デモの上限・失効・回収を適用する。migration ab26d41066c1は利用済みの所属・根拠・履歴を削除するdowngradeを拒否する。UIの切戻しは追加DBを保持して行う。
