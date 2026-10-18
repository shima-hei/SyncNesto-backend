# 公開準備版 API（Phase 2A-3a）

追加2表 `artifact_publications` / `artifact_publication_nodes`、migration `dae4b7c2e658`（親 `c9e3f6a1d547`）。内部限定の未公開表現を保存する。ゲスト公開・閲覧許可・顧客の判断・新しいDL経路は含まない。

## 認証・認可

通常Cookie JWT＋DB session、MFA/初期パスワード、CSRF、active tenant/Project所属を維持。全操作で既存の固定版read権限と新 `artifact_publication:read`、同梱全種類の現在readを確認する。生成/previewはさらに `artifact_publication:manage` と `artifact_release:create`。添付構成ありは `document:read`。画面/API設計も `document:read`、テスト設計は `test_plan:read`。

新2permissionを既存project_admin/managerへ各付与（4割当）。system/tenant管理者権限だけの迂回なし。guest属性・demo・MCPも内部専用transactionで拒否。現在readを失うと単体/ページを拒否し、一覧COUNTからも隠す。全応答に `Cache-Control: private, no-store` と `Referrer-Policy: no-referrer`。

## 4 URL / 5操作

接頭辞 `/projects/{project_id}/artifact-publications`。

| 操作 | 入力/結果 |
| --- | --- |
| POST `/preview` | `release_id`、nullable `preservation_id`、`projection_version=public-preview-v1`、UUID `request_id`。確認用のtitle/件数/容量/添付公開manifest/除外対象と `public_digest` / `source_manifest_digest`。本文は返さず、DB書込みなし |
| POST `` | 同じ指定＋ `expected_public_digest` / `expected_source_manifest_digest`。201新規/200同じ操作の再送。異payloadで同じrequest_idは409。本文と添付構成は生成後変更不可 |
| GET `` | `release_id`任意、`page>=1`、`page_size<=100`（既定20）。現在の全readで許可される準備版/totalだけ |
| GET `/{publication_id}` | `state=prepared`、`content`（公開用metadataとfiles）、`internal_origin`（内部版/保全と両digest）を分離。created_byや内部source IDをcontentへ混ぜない |
| GET `/{publication_id}/nodes` | `page_size<=100`（既定50）/cursor。通番付き公開本文、total/next_cursor。最大1MiB。cursorは本人/Project/公開版/表現版/digestへ束縛し毎ページ再認可 |

`content`と公開nodesは相手向け表現だが、この工程のAPI全体は**内部向け**である。`internal_origin`を顧客DTOへ流用しない。preparedは承認/公開/納品状態を表さない。

## 公開本文と同一性

内部固定版の本文全体とready保全構成を使用する。任意本文/JSON/ファイルの秘密を自動判別したとは扱わない。内部コメント/担当・作成者/内部関連/実行履歴は付加しない。

テストgraphは内部JSONを検証し、design/project数値ID/updated_atを除去して `public-test-design-v1` へ再分割する。業務本文型は既存 `TestDesignUpdate`（id/project_id/updated_atを含まない）と同じ構成で、サーバーは余分なキーを拒否する。項目/因子水準/○●/無効pattern/UUID/任意列/書式を保持。partは256KiB以下、全体32MiB/50,000 nodes以下。未知schemaや破損/容量超過は黙って省略しない。

各nodeのcontent_digestはcontent_digest以外の公開node値のcanonical JSON SHA-256。public_digestはschema/projection/title/全nodes/filesを正規化する。nodesの表示UUID・親UUID・個別content_digestを除き、親を `parent_ordinal` に写すため、同じ固定内容を別操作で準備してもpublic_digestは同じ。内部source対応/id_mapと保全digestは別の不変source_manifestに固定し、公開本文digestと区別する。

画面は準備保存後に全nodeを読み、全体取得完了後に本文を表示する。テストgraphのhash/件数/版を検証。既存の読み取り専用グリッドだけを使いEditor hookや下書き保存を起動しない。Markdownは新画面だけinert表示、画像/リンク/HTML/Mermaidの能動的な読込・実行をしない。元の内部固定版へのリンクは社内ナビとして別に示す。

## エラーと停止

認証/MFA/CSRFエラーコードは既存と同じ。別Project/不存在404、権限不足403、previewと生成のdigest不一致409 VERSION_CONFLICT（currentにはdigestだけ）、未ready添付409、容量413、未知構成400。入力422。cursor改変・別対象再利用も拒否する。

停止用 `PUBLICATION_WRITES_ENABLED=False` はpreview/createを拒否し、内部reader/DBガード/固定本文を保持する。データがあるdowngradeは拒否。元資源のpurgeに公開証跡を加えない。通常Storageの実体・設定・既存DLは変更しない。
