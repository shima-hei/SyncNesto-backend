# 内部用固定版：テスト設計graph（Phase 2A-1c）

既存 `/projects/{project_id}/artifact-releases` のpreview/create/list/metadata/nodesに
`test_design`種別を追加する。新しいURL・permission・role割当はない。
顧客公開や正式承認のAPIではない。添付は1bのattachment-preservationsを使う。

## 元対象と認可

`source`と`included_sources`は `{kind: "test_design", id: 整数, version: 整数}` を受け付ける。
preview/createには`artifact_release:create`と全対象種類のread/updateが必要。
テスト設計は`test_plan:read/update`。一覧/metadata/nodesは`artifact_release:read`と全対象種類のread。
現在のtenant/Project所属・internal user・有効session/MFA・CSRFを既存基盤で検証する。
添付の書込には本文全種類のreadと`document:read/update`を要求し、test_plan:updateは追加要求しない。
read失効は一覧COUNT・本文・添付へ反映する。guest/demo/MCP/運営だけの権限では利用できない。

## 本文

- `test_design`根node：source_idは設計の整数ID、source_versionは保存版。
  payloadはtitle / graph_schema=`test-design-graph-v1` / graph_digest（SHA-256）/
  part_count / counts（既存10集合の件数）。positionは指定した元対象の順番。
- `test_design_part`子node：parent_idは対応する根node。
  source_id/versionは根と同じ。positionとpayload.indexは0から連続する。
  payload.graph_schemaは同じ形式名、payload.textはcanonical UTF-8 JSON文字列の断片。
- 子UUID・position・null・無効pattern・区切り行・任意列・layoutを変更しない。
  10集合はpattern_tables/items/factors/levels/patterns/values/links/columns/
  expected_values/expected_selections。linksは項目―個別パターンの対応であり、要件リンクではない。
- schemaは既存TestDesignReadで検証。元graphの破損は本文を返さず400。
  実行結果・履歴・Evidence・Issue・コメント・要件関連履歴を自動包含しない。

各partは保存nodeのJSON escape後256KiB以内。全体32MiB・50,000nodes、応答1MiBを維持する。
総量超過は413、版/preview hash競合は既存409。混合版を避け同一RR snapshotで読み、原子的に保存・封印する。
従来3種類のcanonical化・hash・schema/projection version・冪等再送は変えない。

## Frontend

Orvalで種別enumを再生成する。全part取得後に根・所属・順序・総数・hash・graph ID/Project/版・集合件数を検証し、
textを結合して一度だけJSON parseする。未完了・不整合を完成した表として表示しない。
node_countは保存node数であり、テスト項目数ではない。previewの業務件数は同じ版の元データから表示する。
固定ViewerではuseDesignEditorや下書き・コメント・ケース取得hookをmountしない。
既存DesignGrid/PatternMatrixへreadOnly/viewOnlyを渡す。viewOnlyは追加オプションで既定false。
元editorへの追加入口では既存の未保存離脱確認を維持し、保存済みサーバーデータだけを固定する。

## 移行・切り戻し

`b8d2f5a0c436`は1b revision `a7c1e4f9b325`のCHECKだけを拡張する。
新表/列/権限追加・旧行更新・backfillはない。lock_timeoutは5秒。
新kind受付を止める場合もreaderとmigration履歴・保存データを残す。
test_design根、included_sources、新graph nodeのいずれかが残る場合downgradeを拒否し、データを消して戻さない。
