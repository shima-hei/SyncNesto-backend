# 開発用MCPの検索・要件コンテキスト・概要

業務上の目的・承認範囲は [Project5 文書11](https://syncnesto.vercel.app/projects/joined/5/documents/11)。このファイルは実際の入出力・検証・運用を記録する。新しい業務データを推論して作る機能ではない。

## 有効化と認可

`MCP_ENABLED`、`MCP_DEVELOPMENT_TOOLS_ENABLED` に加え、`MCP_READ_CONTEXT_ENABLED=true` で通常の開発用OAuth clientへ3操作を追加する。公開clientは `MCP_PLUGIN_EXTENDED_TOOLS_ENABLED` の独立した明示ONも必要で、初期OFFなら元の28操作のまま。旧28操作の入力・説明・注釈のdigestは既存テストで検証する。

毎回、既存の接続資格（`mcp:connect`＋編集資格）、同意済みProject、現在の業務権限、internal/通常realmを確認する。読み取り専用ユーザー・ゲストの接続資格は拡張しない。新permission/role割当はない。関連ごとに `document:read` / `task:read` / `test_plan:read` を再確認する。既存UIの `ProjectObjectiveService.access` のMCP拒否ガードはそのまま保持し、利用者承認済みの新MCP経路だけから同じread権限で取得する。

既存の `PROJECT_OBJECTIVES_ENABLED` / `PROJECT_TRACEABILITY_ENABLED` がOFFなら `disabled` とし、取得経路から有効化しない。DB構造・HTTP URL・既存REST response model・依存の追加はない。Frontend / Orvalの変更は不要。

## 操作

正確なinput/output schemaは `app/schemas/mcp_read.py` と認可済み `tools/list` の `inputSchema` / `outputSchema` を参照する。

| 名前 | 主な入力 | 取得範囲 |
|---|---|---|
| `search_project_items` | `project_id`, `query`, `entity_types?`, `status?`, `page=1`, `page_size=20` | 既存8種の権限内検索。本文は240字の抜粋。最大50件 |
| `get_requirement_context` | `project_id`, `requirement_id`, `include?`, `detail_level=standard`, `relation_page=1`, `relation_page_size=10` | 要件・所属文書/章と直接の正式関連。関連は各最大20件 |
| `get_project_brief` | `project_id`, `include?`, `limit=5`, `as_of_date?` | 案件情報・選択された目標の正本・タスク集計・節目。最大20節目 |

検索対象は `requirement_document`, `requirement`, `task`, `test_design`, `test_item`, `test_case`, `document`, `specification`。queryは1〜200字。statusは保存値への完全一致で、状態列のない種別は除外する。コメント・課題・履歴は検索対象に含まれない。順位は既存と同じ「題名完全一致→題名部分一致→code一致→本文一致」、同順位はtype/id順。カーソルや意味検索は追加していない。

contextのincludeは `designs`, `tasks`, `tests`, `documents`。本文はsummaryで未取得、standardで各2000字、fullでも各8000字を上限とする。上限超過は `truncated_fields` に列挙する。要件のdescription/acceptance_criteria/rationale、所属文書、章を同じ案件の有効データから取得する。要件本文にTASK-IDなどが書かれているだけでは関連にしない。

関連は既存のTraceRepositoryとDocumentLinkが表す直接のDB参照の範囲。同じ要件とタスクに複数種類の関連があっても、関連先の一覧・件数は重複させない。子要件・推移的な影響範囲の全展開ではない。正式承認、要件の最終合否、実行結果、過去の履歴をstatus文字列から生成しない。コメントは既存 `list_requirement_comments` への参照を返す。specification/test_case等、詳細MCPのない種別に架空のツール名を返さない。

briefのincludeは `objectives`, `tasks`, `milestones`。ゴール等は選択文書の `project_goal` / `project_scope` / `project_completion_criteria` をそれぞれ2000字まで返す。未選択を案件説明から補完しない。現在フェーズは保存された専用値がないため `unsupported` とする。タスク集計は削除されていない親/子すべての保存状態の行数で、達成率や親子の重複を除いた作業量ではない。期限超過は `due_date < as_of_date` かつ保存statusがdone/cancelled以外。省略時の比較日はUTC当日。基準日と条件をresponseへ明記する。

## レスポンスの意味

* `loaded` とページtotal=0：取得した範囲で0件。
* `not_requested`：取得していない。0件を意味しない。
* `unavailable`：取得権限等のため返さない。件数/ID/存在も返さない。
* `disabled`：対応する既存flagがOFF。
* `unsupported`：今回の集約では扱わない。

関連未取得時はitemsを空にし、paginationはnullとする。モデルでも未取得状態へのitems/件数の混入を拒否する。has_more/next_page/totalは取得した関連だけへ返す。pageは最大10000で、複数回取得の間に更新があれば重複/欠落があり得るlive読取。transaction snapshotとしては提供しない。

IDは数値/UUIDとも出力では文字列に正規化し、既存ツールへの参照引数では元の型を使う。テスト項目自身に版・更新日時の列がない場合はnull。親の設計書の版は `container_version` へ分離する。旧ツールのID型は変更していない。

新3操作のエラーは `isError=true` と、`error.code/status/next_action` を返す。INVALID_ARGUMENT / AUTHENTICATION_REQUIRED / ACCESS_DENIED / NOT_FOUND_OR_UNAVAILABLE / VERSION_CONFLICT / RATE_LIMITED / TRANSPORT_ERROR / INTERNAL_ERRORを区別し、内部メッセージ、stack、引数の秘密情報は返さない。旧操作のエラー表示は保持する。

成功時は `structuredContent` と互換用JSONテキストの `content` を両方保持する。片方を削除して後方互換を壊さない。新3操作にだけoutputSchemaを追加する。[MCP公式仕様](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) と、利用中SDK 2.3.0の `Tool.output_schema` / `CallToolResult.structured_content` で確認した。クライアントに両方が実際にどの程度投入されるかは別測定とする。

## 検証と比較

専用の一時PostgreSQLだけで `tests/routers/test_mcp_read.py` を実行する。認可済みAPI経由で出力schema、別tenant/Project、不正ID、権限失効、機能OFF、未取得、ページング、長文、検索filter、読取による業務版/receipt不変を検証する。共通検索・trace・MCP層を変更するため既存テストも全体実行する。

`.verify/mcp-read/benchmark.json` は、同じ文書の特定と短い文脈の取得を、既存のq検索＋全文getと新検索で比較する。既存のq検索自体はすでに1回で可能。全文getを省くケースだけの比較であり、全利用への削減率ではない。sizeはSDK CallToolResultモデルをJSON化したbytesで、実ネットワーク/OAuth通信量ではない。実際のCodexトークン・モデル呼出回数は未測定。時間は隔離DB/in-process APIの一回測定で、一般的な性能保証ではない。

## 反映と復旧

新flag OFF/ONでの全権限catalog JSONは36操作48,036 bytes→39操作64,020 bytes（+15,984 bytes）。新3操作の入出力schemaを含む定義量の増加であり、モデルへ投入される実トークン量ではない。取得結果の削減と併せて評価する。

同じcanonical Backendで編集・検証する。提出基点の違う既存checkoutはGit差分の切り出しだけに利用し、別のアプリ試作・移植・画面検証は増やさない。MCP限定PRをCI成功後にmergeし、新flagをProductionへ反映する。新ツール未公開のクライアントはMCP再読込後に実機確認する。既存UX/V2/運営PRのmergeは不要。

復旧は新flagをOFFにして3操作だけを隠すか、今回の差分をrevertする。DB migration/downgradeやデータの巻戻しは不要。P1の本人キュー/変更履歴、閲覧専用接続、より広い関連/詳細は後続で設計する。
