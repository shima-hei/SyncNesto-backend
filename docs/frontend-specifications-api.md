# API設計書・画面設計書API

2026-10-14。専用の構造化設計書をProject内で管理する。既存の要件詳細・自由記述リンク・Markdown文書は保持する。MCPの既存ツール・審査用データは変更しない。

## 権限とデータ境界

- 共通prefix: `/projects/{project_id}/specifications`。
- Cookie認証、サーバーセッション、CSRF、組織・Project所属判定は既存Dependencyを使う。
- 読取 `document:read`、作成 `document:create`、編集・指摘・関連変更 `document:update`、削除 `document:delete`。
- 承認・修正依頼は上記の編集権限に加えて `requirement:review`。運営権限だけでは業務データを操作できない。
- 関連先は同一Projectの未削除資源だけ。要件は `requirement:read`、タスクは `task:read`、テスト項目は `test_plan:read`、設計書・参考文書は `document:read` も必要。読めない関連先は名前も返さない。
- 匿名デモでも既存の組織境界と件数制限が適用される。4表をOWNERSへ登録し、既存のデモ終了・期限切れ回収、組織回収、ごみ箱の回収計画に含める。

## エンドポイント

| Method | suffix | 内容 |
| --- | --- | --- |
| GET / POST | 空文字 | 一覧 / 下書き作成 |
| GET / PATCH / DELETE | `/{id}` | 現在版 / 本文更新 / ごみ箱へ移動 |
| PATCH | `/{id}/status` | 確認状況を変更 |
| GET | `/{id}/revisions` | 最新100版のメタデータのみ |
| GET | `/{id}/revisions/{number}` | 選択した1版のsnapshot |
| GET / POST | `/{id}/comments` | 指摘一覧 / 1件投稿 |
| PATCH | `/{id}/comments/{comment_id}` | 解決・再オープン |
| GET / POST | `/{id}/links` | 関連一覧 / 関連追加 |
| DELETE | `/{id}/links/{link_id}` | 関連だけ解除 |
| GET | `/link-candidates` | 閲覧可能な関連候補（最大50件） |
| GET | `/related` | 指定資源から設計書を逆引き（最大100件） |

一覧query: `kind=api|screen`（省略で両方）、`q`（名前・目的、200文字以内）、`page`（1以上）、`page_size`（1〜100、既定20）。応答は `items,total,page,page_size`。一覧・逆引きでは本文JSONBを取得しない。
候補は `target_type` と `q`、逆引きは `target_type,target_id` を使う。型は `requirement|task|test_item|specification|document`。test_itemのIDはUUID、他は正の32bit整数の文字列表現。親文書・親テスト設計が削除済みの子やスペーサーは候補に含めない。

## 本文と更新

```json
{
  "title": "ログインAPI",
  "summary": "登録済みの利用者がログインできる",
  "content": {
    "type": "api",
    "method": "POST",
    "path": "/auth/login",
    "authentication": "未ログインでも呼び出し可能",
    "authorization": "有効な通常アカウント",
    "request_fields": [],
    "response_fields": [],
    "responses": [],
    "notes": ""
  }
}
```

初回はtitleのみ必須。`content.type` は `api|screen`、後からの種類変更は拒否する。screenは `route,audience,reference_url,fields,actions,states,notes` を持つ。参考URLはHTTP/HTTPSのみ。

項目は `id`（UUID）, `name,data_type,required,description,example,validation,location`。locationは `path|query|header|body|display`。応答・操作・状態の行は `id,name,description,destination`。行IDは並べ替えでも保持し、本文内の重複IDは422。未知の本文・項目キーも422。

各項目配列は200行、ルール配列は100行まで。本文合計はUTF-8で500KB以内、notesは2万文字、summaryは1万文字まで。OpenAPIが項目ごとの上限の正。本文更新は作成payloadに `version` を追加する。成功時は版を増やしてsnapshotを保持。競合は既存の `409 VERSION_CONFLICT` と最新 `current` を返す。Frontendは安定UUIDによる3方向比較で独立した変更を結合し、同じ項目の変更・削除対編集は利用者が選ぶ。

状態は `draft|in_review|changes_requested|approved`。確認依頼・承認には目的とパス（API）または画面URL（screen）が必要。未解決指摘がある承認は409。承認済み本文を変更するとdraftへ戻す。状態変更もversion必須・版履歴へ記録する。自己承認の禁止や多段階の承認者割当は初版に含めない。

状態不備のcodeは `SPECIFICATION_UNRESOLVED_COMMENTS`（409、未解決指摘）と `SPECIFICATION_REVIEW_FIELDS_REQUIRED`（400、目的/パス/画面URL不足）。Frontendは各codeで次の操作を案内する。取得後の背景再取得で編集の基準versionを変更しない。

## 指摘の位置と変更検知

投稿は `specification_version,field_path,body`。pathは `title` / `summary` / `content/path` / `content/request_fields/{UUID}/validation` など、実在するスカラー項目を指定する。配列全体・存在しない行・キーは400。確認版が古ければ409。

本文5000文字、1設計書につき指摘500件まで。投稿時の値を800文字までsnapshotし、値全体のSHA256を保存する。読取応答の `anchor_changed` は全文digestで判定するため、800文字以降の変更・削除も検知し、並べ替えだけでは変更扱いにしない。`author_name`、投稿日時、投稿時の版も返す。

一覧は `page,page_size`（最大100・既定100）による段階読込。解決・再オープンはコメント自身の `version` が必要。コメントで親本文の版は増やさない。監査ログは操作種別・設計書ID・版だけを記録し、本文や指摘内容は入れない。

## 削除・検索・リリース

DELETEにはquery `version` が必要。30日保持の既存ごみ箱で復元できる。横断検索は既存のdocumentカテゴリ内の `kind=specification` として名前・目的・本文を検索する。旧Frontendとの互換のため、Backendを先行配布する期間には新しい設計書をまだ作成しない。

Migration `e5a9c2f7b103`（親 `d4f8b1e6a902`）は4表と索引・FKを追加するだけで、既存データ・所属・Role割当を変更しない。適用順は通常/デモDBの事前backup・revision確認 → 両DBmigrationとruntime roleの新表/sequence権限確認 → Backend → Frontend。既存Terraformのdefault privilegesを利用し、追加サービス・課金設定は不要。downgradeは新設計書を削除するため、公開後のロールバックはコードを戻して表を保持する。今回は本番適用していない。

## 設計ワークスペース（2026-11-28）

案件ナビゲーションの「設計」から、設計概要、画面設計、API設計、DB設計を選択する。既存の詳細・作成・編集URLを保持する。設計対象の種類に `database` を追加し、従来の番号は `SCR` と `API`、DB設計は `DB` で表示する。

| 方法 | `/projects/{project_id}/design-workspace` 配下 | 用途 |
| --- | --- | --- |
| GET | `/overview` | 種類・状態、グループ、最近の更新 |
| GET / POST / PATCH | `/collections` / `/collections/{id}` | 機能グループ・API仕様セット・DBモデル |
| PUT | `/memberships/{specification_id}` | 設計の版を指定してグループ所属を置換 |
| GET | `/diagram` | 本文を除いた図・一覧の共通データ。画面遷移、外部キー、論理関連を投影 |
| PUT | `/placements` | 全体・グループごとの配置。配置版0は新規、`hidden` は図からの配置解除 |
| POST | `/mockup-preview` | HTML・CSS原本から許可リストを通した iframe 専用文書を生成 |
| GET / POST | `/wireframe-parts` | 案件共通部品の最新メタデータ一覧 / 初版を登録 |
| GET / PATCH | `/wireframe-parts/{part_id}` | 最新または `version` 指定の部品を取得 / 最新版を指定して新版を追加 |
| POST | `/openapi/import` | 差分確認と、版を指定した選択操作の下書き取込 |
| GET | `/openapi`, `/ddl` | 仕様セット・DBモデルの出力、または設計IDと版を指定した出力 |
| GET / PUT | `/impact/{specification_id}` | 過去版との比較と、確認結果の保存 |

既存設計は従来の下書きと版履歴を維持する。新しい画面遷移・カラム・制約・要素はUUIDを持つ。図の座標は本文とは別に保存し、設計書とグループに案件ID込みの外部キーを持つ。保存は明示操作、テキストは下書き復元時にも元の版を保持する。固定版作成時にグループ、配置、API仕様セット設定をスナップショットする。共有コメントは従来の固定公開基盤を使用する。

OpenAPI GUIと出力は操作オブジェクトを共有する。旧項目は原文を残し、旧データから定義を起こす操作を提供する。3.1系を対象とし、不明なバージョン・外部参照・未展開のPath Item共通定義は取込時に明示する。JSONとYAMLは同じバックエンド検証に通す。DDLはPostgreSQL 17向けにスキーマ、テーブル、制約、インデックスを生成する。外部キーはテーブル作成後に生成し、実DBへは接続しない。

プレビューのHTMLはサーバーの許可リストを通し、空のsandbox属性を持つiframeで表示する。CSSの外部参照は除外し、CSPで読み込みを制限する。PNG・JPEG・WebPの小さな埋め込み画像だけを表示する。原本のダウンロードはプレーンテキスト拡張子を使う。

MCPの `list_design_artifacts` と `get_design_artifact` は一覧、必要な構成、履歴、出力を取得する。原本は `section=mockups` の明示指定で取得する。下書きの作成・更新は `create_design_artifact`、`update_design_artifact` を使い、既存の認可・再送防止・版競合検出を適用する。`list_design_collections` と `get_design_diagram` でコレクションと図を取得する。追加ツールは `MCP_DESIGN_TOOLS_ENABLED` のローカルOAuthクライアントに限定し、公開プラグインの既存カタログを維持する。

共通部品は案件ごとに版を追加する。画面には部品・版の識別子、配置元と画面内のレイヤーID対応、実体のレイアウトを保存する。画面項目やイベントは各画面で関連付ける。新版を登録しても配置済みの別画面と過去版はそのまま読み出せる。配置に新版を適用すると、残るレイヤーは画面内のIDと関連を引き継ぐ。

移行は `d84b9a6e2f10 → f31a8d7c620e → 7c19d8ab502e → 4ce61bd208af`。既存の設計行・版・リンク・指摘は書き換えない。配備時に事前バックアップと権限・件数の照合を行う。部品を含む画面が保存された後は、切り戻し時も部品の版情報を読めるバックエンドと追加表を保全する。

参照資料 [OpenAPI 3.1.1](https://spec.openapis.org/oas/v3.1.1.html) ・ [PostgreSQL 17の制約](https://www.postgresql.org/docs/17/ddl-constraints.html) ・ [HTMLのsandbox属性](https://html.spec.whatwg.org/multipage/iframe-embed-object.html#attr-iframe-sandbox)
