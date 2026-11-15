# 成果物固定版API（Phase 2A-1a）

内部の固定版管理用API。顧客公開・正式承認・guest招待とは別の機能。旧要件・設計・承認APIの契約と業務データは変更しない。

## 対象と認可

対応kindは`requirement`、`requirement_document`、`specification`。文書には有効な章・子要件・型別詳細を含める。画面/API設計の本文と項目UUIDも固定する。添付・テスト設計graphは後続単位で追加し、この版で渡すと422。

| 操作 | 必須permission |
| --- | --- |
| preview・作成 | `artifact_release:create` ＋ 含む全kindの既存read/update |
| 一覧・metadata・本文 | `artifact_release:read` ＋ 含む全kindの既存read |

要件/文書は`requirement:*`、画面/API設計は`document:*`を使う。新permissionの初期付与はProjectの`project_admin`・`manager`だけ。role key自体を新APIの特権判定には使わず、現在permissionを判定する。新permissionがあっても元種類の権限がなければ取得不可。

既存のCookie・MFA・CSRF・tenant選択を継承する。内部ユーザーだけを対象とし、guest・demo・MCP経由を拒否する。運営/組織のroleだけではProject内容へアクセスできない。顧客向け公開grantやDTOはこのAPIで代用しない。

## 5つのAPI

全pathの先頭は`/projects/{project_id}/artifact-releases`。

| Method / suffix | 入力・応答 |
| --- | --- |
| POST `/preview` | 元対象・元版・明示的な追加対象を受け取り、対象構成・hash・容量を返す。業務データ保存なし |
| POST 空suffix | previewで確認したhashと操作UUIDを加えて保存。初回201、同じ再送200 |
| GET 空suffix | `source_kind`/`source_id`/`page`/`page_size`でmetadata一覧。本文なし |
| GET `/{release_id}` | 固定版metadata・含めた元対象の一覧。現在版の情報は別属性 |
| GET `/{release_id}/nodes` | 固定本文を署名cursorと件数・byte上限で分割取得 |

## Previewと保存

```json
{
  "source": {"kind": "requirement_document", "id": 12, "version": 3},
  "included_sources": [{"kind": "specification", "id": 7, "version": 4}],
  "projection_version": "internal-preview-v1"
}
```

入力のID/版は架空。`included_sources`は省略時空、最大100件。どの元対象も現在のProjectに所属し、有効である必要がある。重複した入力は422、文書とその子要件を二重に指定する等の展開後の重複は400。内部の関連リンクから他のデータを自動公開しない。

preview応答は`source`、`included_sources`、`projection_version`、`title`、`scope_digest`、`node_count`、`node_counts`、`byte_size`、`captured_at`。hashは元版だけでなく、子構成・順序・本文・型別詳細を含む。

保存POSTには上記入力へ次を加える。

```json
{
  "request_id": "30b6ce12-d2f6-4d34-96a7-a8525b3eb2be",
  "expected_scope_digest": "<previewで受け取った64文字のSHA-256>"
}
```

`request_id`は新しい明示保存操作ごとに作り、通信再送では同じ入力・同じIDを使用する。同じProject・本人・IDに異なる入力を送ると409 DUPLICATE_RESOURCE。hash不一致では409 VERSION_CONFLICTとなり、previewを取り直す。本文・作成者・承認状態はクライアントから受け取らない。

作成時に固定版と本文・操作記録を一つのtransactionで保存する。並行保存の同じ操作は1版に収束する。元対象が後で編集・削除されても、同じ保存操作の再送は現在の権限を再確認して元の固定版を返す。

## Metadata

summaryの主要項目:

- `id`（UUID）、`project_id`、`source_kind`、`source_id`、`source_version`
- `schema_version`、`projection_version`、`title`、`content_digest`
- `node_count`、`byte_size`、`captured_at`、`created_at`、`created_by`

detailには`sources`（元対象と採用版）、`source_state`、`current_source_version`、`scope_comparison`を追加する。`current_source_version`は**根の元対象の現在版だけ**で、文書内のすべての子の一致を意味しない。`scope_comparison`はこの版では常に`not_checked`。元対象が削除・移動等で元Projectから取得できない場合は`source_state=deleted`、現在版nullとする。固定本文は残る。

`captured_at`は固定版transactionの基準時点。保存commit時の絶対最新であるとは表示しない。`created_at`は保存の日時。承認済み・公開済みという状態をこれらの日時やhashから推測しない。

一覧の既定page_sizeは20、最大100。`items`・`total`・`page`・`page_size`を返す。含む種類をすべて読める固定版だけをSQLで絞り、件数も同じ条件から計算する。権限外の対象名や件数を残さない。

## 固定本文nodes

```text
GET /projects/1/artifact-releases/{release_id}/nodes?page_size=50
GET /projects/1/artifact-releases/{release_id}/nodes?cursor={next_cursor}&page_size=50
```

page_sizeの既定50、最大100。応答は`release_id`、`items`、`total`、`next_cursor`。末尾ではcursorがnullになる。

nodeは`id`、`release_id`、`parent_id`、`ordinal`、`position`、`node_kind`、`source_id`、`source_version`、`payload`、`content_digest`を持つ。親は同じ固定版に限定し、元IDは履歴値で、現在の元データへFK接続しない。DBで封印済みのheader変更・削除とnode追加・変更・削除も拒否する。

順序は固定版内で不変の`ordinal`、親内の並びは`position`。cursorはProjectと固定版に署名で結び付ける。別の固定版への流用・改変は400。cursorは権限の代わりではなく、毎回現在権限を確認する。

応答bodyを1MiB以下に分割する。現在の固定版上限は50,000 nodes・正規化したnode合計32MiB、単一nodeは1MiBから応答用の余白4KiBを引いた範囲。超過は413 ARTIFACT_RELEASE_TOO_LARGEで、黙って本文を省略しない。previewのbyte_sizeは正規化nodeの合計で、HTTP応答全体のサイズとは異なる。

## エラーと既存仕様

| 状況 | 応答 |
| --- | --- |
| 未認証/MFA/CSRF/tenant選択 | 既存のエラー分類を継承 |
| Project・新permission・元種類の権限不足、guest/demo/MCP | 403 FORBIDDEN |
| 元対象/固定版がない、指定Project内にない | 404 NOT_FOUND |
| 不正kind・任意本文/actor等の余分な入力 | 422 |
| 展開後の重複、cursor不正 | 400 BAD_REQUEST |
| 元版・構成hash不一致 | 409 VERSION_CONFLICT。currentは版かhashだけで内部本文を含めない |
| 同じ操作IDで別入力 | 409 DUPLICATE_RESOURCE |
| 並行保存の再試行回数を超過 | 409 CONFLICT。同じ操作IDで再送可能 |
| node/固定版の上限超過 | 413 ARTIFACT_RELEASE_TOO_LARGE |

エラーの`message`/`code`形式、既存APIのVERSION_CONFLICT.current、旧承認・レビュー・statusの意味は変更しない。保存した本文は内部向けで、自由記述・型別JSONが顧客に公開可能と自動判定されたものではない。

OpenAPIがクライアント生成の正本。Orval生成ファイルは手修正しない。Phase 2A-1aでは通常Frontend/localhost:3000へ新UIや新clientを反映せず、独立した検証コピーで生成・型確認を行う。
