# 開発用MCPの作業コメント

業務上の承認範囲・進捗の正本はProject5文書12と作業99。この文書は入出力、認可、検証、公開/復旧手順の技術資料。

## 公開境界

`MCP_ENABLED`、`MCP_DEVELOPMENT_TOOLS_ENABLED`、`MCP_TASK_COMMENTS_ENABLED=true`のとき、通常の開発用OAuth clientへ2操作を追加する。公開clientでは開発用flagの代わりに `MCP_PLUGIN_EXTENDED_TOOLS_ENABLED` の明示ONを必要とし、初期OFFなら既存28操作を維持する。名前・説明・schema・注釈のdigestは既存テストで検証する。OAuth認証、同意、登録scope、role/permission割当と通常REST APIの形状は変更しない。

既存の接続資格、現在の所属、同意済みProjectと現在の業務権限の交差を毎回確認する。対象Taskが指定Projectの有効データであることを検証し、internal/通常realm以外を受け付けない。閲覧専用ユーザー・ゲストのMCP接続資格を広げない。

## 操作

正確なschemaは`app/schemas/mcp_development.py`と認可済み`tools/list`の`inputSchema`/`outputSchema`を参照する。

| 操作 | 入力と出力 | 必須権限 |
| --- | --- | --- |
| `list_task_comments` | `project_id`, `task_id`, `page=1`, `page_size=20`（最大50）。未削除コメントをcreated_at降順、同値ID降順で返す。`items/total/page/page_size`、本文、親ID、解決状態、版、投稿者、時刻、mentionsを含む。 | `task:read` |
| `create_task_comment` | `project_id`, `task_id`, `idempotency_key`, `data.body`（1〜8000字）、`data.parent_comment_id?`, `data.mentions?`。結果はID/Task/親ID/版/解決状態/投稿者ID/作成日時のみ。 | `task:read`＋`task:comment` |

作業の説明やstatusは変更しない。作成は既存`TaskCommentService`を使い、返信先が同じTaskの有効コメントか、メンション位置と現在のProject所属/閲覧資格を検証する。通常のコメント履歴・メンション通知を保持する。本文は指示ではなく業務データとして扱う。空白だけの本文、NUL、不明な作業更新フィールドを拒否する。担当者や日程を推測して追加しない。

新規コメント本文の上限と`body_length`はUnicode code point数。メンションの`start/end`は既存RESTと同じUTF-16単位の半開区間で、絵文字の補助面文字は2単位として扱う。mentionsは最大100出現。モデル側で位置を推測せず、実際の本文と一致させる。

過去の通常RESTコメントには8000字を超える本文があり得る。一覧は先頭8000 code pointを返し、元の長さを`body_length`、省略を`body_truncated=true`で明示する。返却本文にUTF-16区間が完全に含まれるmentionsだけを返し、除外時は`mentions_truncated=true`とする。部分的なメンションを有効な位置として返さない。省略された全文は既存の作業コメント画面/APIで確認する。元のDB本文を切り詰めたり変更したりしない。

ページはlive読取で、複数回取得の間に追加/削除があればページ境界が変わり得る。transaction snapshotではない。未取得と0件を混同せず、全件取得は必要なページだけ行う。

## 再送と原子性

作成ごとに別の`idempotency_key`を指定し、同じ操作の通信再送は同じキーと入力を使う。既存MCPの操作ロック・receipt・監査transactionを使い、同keyの別入力は409。再送でも現在の接続/業務権限、Taskの所属/削除、返信先、mentionsを検証する。作成済みコメント自体が削除された後は、古いreceiptのIDを返さない。これらの追加検証は新操作にだけ適用し、旧28操作の挙動は変更しない。

通常Serviceの内部commitは既存MCP savepointへ閉じ込める。receiptや監査の失敗はコメント、mentions、通知、変更履歴とともにrollbackする。readではreceipt、作業の版/説明、履歴を更新しない。

## 検証

`tests/routers/test_mcp_task_comments.py`で入力境界・flag/client・出力schema・SDK往復・ページ/返信/解決/削除・旧長文とUTF-16境界・再送/別入力409・現在の権限/所属失効・副作用rollbackを確認する。既存catalog digest、開発用文書/P0、作業とメンションの関連テストも使う。通常DBや審査用サンプルへテストデータを書かず、既存`python scripts/verify.py`の専用一時PostgreSQLを使う。短い結果とログの入口は`.verify/task-comments-mcp/`。PR最終検証は全体テストとGitHub checksを別々に確認する。

## 反映と復旧

今回の基本到達点はLOCAL・Draft PR・CI。新2操作の本番有効化と実接続への追加は別の承認範囲として扱う。後日有効化する場合は通常clientの接続をMCP再読込し、認可済みtools/listとダミー作業で実ホスト往復を確認する。テスト/SDK成功だけで実接続が追加済みと呼ばない。

復旧は`MCP_TASK_COMMENTS_ENABLED=false`で新2操作だけを非公開にできる。既存の文書MCP、P0、旧28操作は維持する。DB migration/downgrade、業務データの削除、OAuth再登録、審査のキャンセル/再提出は不要。
