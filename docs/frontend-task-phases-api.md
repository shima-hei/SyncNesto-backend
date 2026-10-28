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

## 既存Task APIへの追加

作成/更新で任意のphase_assignmentを受ける。省略は既存所属を保持（新規は未分類）、nullは未分類へ解除。objectはkind=unclassified/shared/phaseで、phaseだけphase_id必須、他はnull。別Project/不明工程は404、新しいアーカイブ工程への割当は400。同じ工程行のロックでアーカイブとの競合を直列化し、Task更新は従来のversion/409 current/変更履歴/監査を維持する。

estimated_basis/actual_basisはそれぞれown/rollup/unreviewed。新規はown、省略更新は保持、nullは422。既存の見積/実績分数は変更しない。既存の親で値がある場合だけ、削除済みの子も含めてunreviewedへ移行する。工数根拠は通常のTask更新権限内で変更する。

TaskReadとガント/ボード/409 currentにはphase_scope/phase_id/phase_name/phase_archivedと両basisを追加する。effort_totalsは自分と子孫の有効Taskの算出値で、単一Task読取で計算し、複数Taskの一覧ではnull（未算出）とする。一覧の各行をrootに重複して再帰集計せず、全体・工程別にはeffort-summaryを使う。算出値を入力欄へ書き戻さない。削除済みの中間親の下にある有効な子孫も保持する。工程変更は子へ自動伝播しない。

## 集計

effort-summaryは一覧と同じq/status/task_type/priority/assignee_id/requirement_id/parent_task_id/root_only/start_date_from/due_date_to/overdue/tagを受ける。ページ・折り畳みと独立した一意Task集合を先に確定し、ownの分数を1回だけ加算する。同額の別Taskは両方計上。rollupの保存値とunreviewedは加算しない。nullと明示0を区別する。

各metricはminutes/missing_count/unreviewed_count/complete。未入力・未確認があれば確認済み小計として扱う。groupsは工程・共通・未分類へ分け、アーカイブ分も含む和とtotalsを一致させる。filtersとunit=minutesを返す。進捗率・親達成率は更新しない。

公開MCPはmcp_task_contract.pyで従来のTask入力を固定し、業務APIの新項目を公開しない。通常/デモの新表はtenant_scopeの所有関係に登録し、既存デモの上限・失効・回収を適用する。migration ab26d41066c1は利用済みの所属・根拠・履歴を削除するdowngradeを拒否する。UIの切戻しは追加DBを保持して行う。
