# タスクの複数ステータス・階層フィルタ

`GET /projects/{project_id}/tasks` に以下を加算する。既存の `task:read`、Tenant/Project境界、ページサイズ上限100、ソートと検索を維持する。DB/migrationの変更はない。

- `statuses=todo&statuses=in_progress`: 指定した状態のOR。省略は制限なし。同じ値の重複は件数に影響しない。
- `exclude_statuses=done&exclude_statuses=cancelled`: 指定した状態を除外する。将来・旧データの他の状態は除外しない。
- `hierarchy`: `parent`（子あり）、`root_parent`（親なし・子あり）、`middle_parent`（親あり・子あり）、`leaf`（親あり・子なし）、`standalone`（親なし・子なし）。省略は制限なし。不正値は422。

配列はOpenAPIのform/explode（同名キーの繰り返し）、各上限50。旧 `status=todo` は引き続き完全一致。複数の状態引数と他条件はすべてANDで結合する。フィルタ後にtotalを算出してからページングする。既存の `parent_task_id` と `root_only=true` の同時指定は従来どおり400。

`TaskRead.has_children` を加算する。子は同Projectかつ未削除の実関係で判定し、検索語・状態・ページングに依存しない。一覧・詳細・更新・ガントの通常TaskReadでは一括取得した値を返す。409 currentにも同じ値を返す。旧クライアントは追加の読み取り値を無視できる。

公開MCP41操作の入力schema・明示projection・同意範囲には加算しない。TaskCreate/TaskUpdate・業務権限・親子関係の保存形式・ガントの検索条件は変更しない。
