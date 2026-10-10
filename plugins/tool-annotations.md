# MCPツールのannotation根拠

2026-10-10。公開申請の入力用資料。実際の配布先のtool scan結果と一致することを提出前に確認する。
定義は `app/services/mcp_catalog.py`、公開値は `McpOperationsService.catalog()` にある。
利用者の現在の権限と接続で許可したProjectにより、表示されるツールはこの28件の一部になる。

## 参照ツール（14件）

`list_projects`、`get_project_context`、`list_requirement_documents`、`list_requirements`、
`get_requirement_target`、`list_requirement_sections`、`list_requirement_comments`、
`list_test_designs`、`get_test_design`、`list_test_design_comments`、`list_tasks`、`get_task`、
`list_task_dependencies`、`list_milestones`。

| annotation | 値 | 根拠 |
| --- | --- | --- |
| `readOnlyHint` | `true` | 許可されたProject内の現在値を参照し、業務データや操作receiptを保存しない。 |
| `destructiveHint` | `false` | 業務データの作成・変更・削除を行わない。 |
| `idempotentHint` | `true` | 同じ参照を繰り返しても業務状態に追加の変更を加えない。他者の更新により返却値は変わり得る。 |
| `openWorldHint` | `false` | Syncnestoの接続・Project権限内の資源だけを対象とし、任意の外部URL・宛先を操作しない。 |

## 業務データを書き込むツール（13件）

`create_requirement_document`、`create_requirement_section`、`create_requirement`、
`comment_requirement`、`create_test_design`、`append_test_design`、`comment_test_design`、
`link_requirement_test_item`、`create_task`、`update_task`、`create_task_dependency`、
`create_milestone`、`apply_task_schedule`。

| annotation | 値 | 根拠 |
| --- | --- | --- |
| `readOnlyHint` | `false` | 要件・設計・コメント・タスク・依存・マイルストーン・日程のいずれかを永続化する。 |
| `destructiveHint` | `true` | 現行実装では正式な業務書込みを保守的に一律分類する。作成系も含み、全件が削除操作という意味ではない。公開審査のtool scanでこの根拠も提示する。 |
| `idempotentHint` | `true` | 接続IDと必須の `idempotency_key` で直列化する。同じキー・同じ入力は保存した結果を再送し、入力を変えたキーの使い回しは拒否する。新しいキーは別操作として扱う。 |
| `openWorldHint` | `false` | 書込みはSyncnesto内の許可Projectに限定し、任意の外部送信先を受け付けない。 |

## 日程プレビュー（1件）

`preview_task_schedule`。

| annotation | 値 | 根拠 |
| --- | --- | --- |
| `readOnlyHint` | `false` | 日程の前後・前提・確認ハッシュを後続の適用に使うreceiptへ保存し、監査も記録する。 |
| `destructiveHint` | `false` | プレビュー作成だけでは既存タスクの開始日・期限を変更しない。日程変更には別の `apply_task_schedule` が必要。 |
| `idempotentHint` | `true` | 業務書込みと同じ再送キーの検証・receiptの再送を使い、同じ案を重複保存しない。 |
| `openWorldHint` | `false` | 許可ProjectのタスクとSyncnesto内の保存済み案だけを対象とする。 |

以前はpreviewを読み取り扱いしていたが、receipt保存という副作用に合わせて `readOnlyHint=false` へ修正した。
日程案の適用期限は15分で、対象のversionが変われば適用を拒否する。
annotationはホスト向けのヒントであり、実行時の権限・version・確認ハッシュの検証を代替しない。

根拠: [OpenAIのMCP審査要件](https://developers.openai.com/plugins/deploy/app-review)、
`app/services/mcp_operations.py`、`app/repositories/mcp.py`、`tests/routers/test_mcp.py`。
