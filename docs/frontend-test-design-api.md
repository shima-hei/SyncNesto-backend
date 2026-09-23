# テスト設計・テストケースAPI

## 方針と既存機能との整合

テスト設計書はプロジェクト配下のリソースとして扱う。既存のCookie認証、サーバーセッション、CSRF、`require_project_permission`、業務例外、監査ログを利用する。既存の要件・タスクAPI契約は変更しない。

Routerは認可と入出力、Serviceは所属・参照整合性・排他制御とトランザクション、RepositoryはSQLAlchemyでの読み書きを担当する。ケースのスナップショット生成・影響判定は`services/test_design_cases.py`に分離している。

## データモデル

| エンティティ | テーブル | 責務・関連 |
| --- | --- | --- |
| TestDesign | test_designs | プロジェクト、名前、説明、version、作成者・更新者・日時 |
| PatternTable | test_pattern_tables | 用途ごとの独立した表。名称・表示順 |
| TestItem | test_items | 項目ID表示コード、観点、内容、前提、データ、手順、期待結果、備考、任意列値 |
| Factor | test_factors | 設計書内の因子 |
| FactorLevel | test_factor_levels | 1つの因子に属する水準 |
| TestPattern | test_patterns | 再利用可能な組み合わせ、表示コード、説明、備考、有効・無効 |
| TestPatternValue | test_pattern_values | パターン×因子ごとの水準。nullは対象外 |
| ExpectedValue | test_expected_values | 因子とは独立した期待値候補 |
| TestPatternExpectedValue | test_pattern_expected_values | パターンごとに選択した期待値。パターン単位で一意 |
| TestItemPattern | test_item_patterns | 項目×パターンの多対多関連。組み合わせは一意 |
| TestCase | test_cases | 紐付けから生成した独立ケース、生成時内容、個別結果、version |
| TestDesignColumn | test_design_columns | 任意列のキー・名称・表示順 |
| TestDesignLayout | test_design_layouts | セル書式・列幅・行高。業務データとは別保存 |

子エンティティはクライアント生成のUUIDを安定IDとして持ち、`position`で並べる。行・列の移動や追加で、内容と書式の対応が変わらない。項目・因子・水準・パターン・紐付けの参照は、設計書IDを含む複合外部キーと入力グラフ検証で保護する。

任意列キーは`custom_`で始まる。値は`TestItem.custom_values`に保持し、未定義のキーを拒否する。固定項目の列は削除しない。パターンと因子の組み合わせは一意で、指定水準はその因子に属する必要がある。

## APIと権限

共通プレフィックス: `/projects/{project_id}/test-designs`

| Method | Path | 権限 | 内容 |
| --- | --- | --- | --- |
| GET | 共通プレフィックス | test_plan:read | 設計書一覧 |
| POST | 共通プレフィックス | test_plan:create | 名前・説明から空の設計書を作成 |
| GET | /{design_id} | test_plan:read | 構造化設計全体を取得 |
| PUT | /{design_id} | test_plan:update | version付きで設計全体を原子的に保存 |
| DELETE | /{design_id}?version=N | test_plan:delete | 設計書と全ケースを削除 |
| GET | /{design_id}/cases | test_case:read | ケース一覧・変更影響 |
| POST | /{design_id}/cases/generate | test_case:create | 有効な紐付けから未生成ケースを追加 |
| PATCH | /{design_id}/cases/{case_id} | test_case:execute | 実行結果・備考を更新 |
| POST | /{design_id}/cases/{case_id}/refresh | test_case:update | 最新設計内容を明示的に取り込む |

既存RBACの通り、memberは設計編集とケース実行が可能だが、ケース生成・設計内容の取り込みはmanager以上。viewerは閲覧のみ。設計書削除はproject_adminまたはsystem_admin。

## 保存契約

用途別管理では`pattern_tables: [{id, name, position}]`を追加した。項目は任意の`pattern_table_id`、因子・組み合わせ（TestPattern）・期待値は`table_id`を保持する。同一設計書内のみ参照でき、別表の因子や期待値を組み合わせに選ぶと422。同じ因子名・組み合わせコードは別表で再利用可能。表を使う新形式では`links`を空配列にする。

ケース生成は、表なし項目を1件、表あり項目を有効な組み合わせ数だけ展開する。空の表は0件。設計全体の展開上限は20,000件。ケースのsource_keyは項目UUID、または項目UUID:組み合わせUUIDで、再生成時の重複を防止する。source.patternは単独ケースではnull。表を利用する場合はsource.pattern_tableに表のスナップショットを追加する。表の解除や組み合わせの削除でも過去のケース・実行結果を保持する。

`8d9e0f1a2b3c_pattern_tables.py`は旧データを設計書ごとの「既存パターン表」に移し、旧紐付けがある項目に設定する。旧ケースID・結果を保持し、既存組み合わせとの対応をsource_keyへ移行する。旧形式のAPIデータは表を持たない場合のみ互換処理する。

マトリクス対応で`expected_values: [{id, name, position}]`と`expected_selections: [{id, pattern_id, expected_value_id, position}]`を追加。省略時は空配列として扱うため、全体保存時は取得した配列も必ず送信する。各配列は10,000件まで。同一設計書の参照のみ許可する。期待値の名称変更・選択変更はケースの変更検知対象となる。ケースの`source.expected_value`に生成時の期待値を保存し、既存の実行結果は上書きしない。

追加migrationは`7c8d9e0f1a2b_add_pattern_expected_values.py`。従来の項目・パターン・ケースは保持する。

作成リクエスト例:

```json
{ "name": "ログイン設計", "description": "ユーザー種別と入力値を確認" }
```

PUTは`name`、`description`、`version`、`items`、`factors`、`levels`、`patterns`、`values`、`links`、`columns`、`layout`を受け取る。配列から除いたエンティティは削除対象となるため、部分配列を送ってはいけない。GETレスポンスの`id`、`project_id`、`updated_at`は更新本文から除く。

`layout`の構造例:

```json
{
  "cells": {
    "items:項目UUID:content": {
      "bold": true, "align": "left", "background": "#ffff00", "color": "#000000"
    }
  },
  "widths": { "items:content": 280 },
  "heights": { "items:項目UUID": 72 }
}
```

保存は設計書行をロックした後にversionを検証し、全体を一つのトランザクションで処理する。成功時はversionを1増やす。競合は`409 VERSION_CONFLICT`で最新の設計全体を`current`に返す。別設計のUUID再利用などDB制約違反はロールバックし`409 DUPLICATE_RESOURCE`、グラフ不整合は422を返す。

既存の紐付けUUIDの参照先を変更することはできない。紐付け先変更は旧関連を除いて新しいUUIDの関連を追加する。生成済みケースの意味を後からすり替えないためのルールである。

## ケースと変更影響

`TestItem`には`target_feature`（対象画面・機能）と`is_spacer`（明示的な区切り行）を追加した。区切り行は独立IDとpositionを持ち、保存・再表示で位置を維持する。内部codeは保持するが項目数・ケース数に含めず、ケースを生成しない。migrationは`0f1a2b3c4d5e`。

設計書PUTのトランザクション内でケースを同期する。新しい組み合わせ・単独項目にはケースを追加し、既存ケースのsourceは最新の設計に更新する。`source_hash`と`acknowledged_source`は実行者が変更を確認するまで以前の基準を保持し、`stale`で影響を示す。`acknowledged_source`は最後に確認済みとなった設計内容で、APIは変更前後の比較に利用できる。状態・実行結果・備考は変えない。消えた生成元のケースは削除せず`active=false`とする。旧`/cases/generate`は互換APIとして残るが画面からは呼ばない。`refresh`は実行者が設計変更を確認した際に影響表示の基準と`acknowledged_source`を更新する。設計の更新でsourceが変わるとケースversionも増える。

`acknowledged_source`追加前にすでに影響ありとなっていたケースは旧値を復元できず、nullを返す。影響がなかった既存ケースは、次の設計変更直前に旧内容を記録する。表示用項目番号だけが変わった場合は、影響を発生させず確認済み内容の番号も更新する。

設計一覧の`item_count`は項目数、`expanded_case_count`は有効な組み合わせを展開した生成予定ケース数。過去のケース履歴は件数に含めない。表なしは1件、表ありは有効な組み合わせ数（空の表は0件）として集計する。

`expected_selections`は同一組み合わせに複数の期待値を設定可能。同一(pattern_id, expected_value_id)の重複は禁止。ケースsourceは単一期待値では従来の`expected_value`、複数では`expected_values`配列を返す。表示側は両方に対応する。選択順のみの変更でstaleにしない。

ケース状態は`not_run` / `passed` / `failed` / `blocked` / `not_applicable`（対象外）。更新APIは実行結果・備考も受け取るため、一覧で状態だけを変更するときも既存の`actual_result`と`notes`を送信する。ケース更新時に認証ユーザーを`executed_by`、更新時刻を`executed_at`へ自動記録する。既存ケースは両方nullで、対象外も同じ記録方式を使う。将来のEvidenceはケースUUIDへの独立した関連として追加し、source内の設計スナップショットに格納しない。

互換用の生成リクエストは`{"version": 設計書version}`。同じ紐付けから何度生成してもケースは重複しない。無効パターンは対象外。項目内容・選択水準の名前・説明・任意列情報を`source`へ保存する。通常の画面操作では設計保存時に自動同期する。

ケース一覧は次を返す。

- `active`: 生成元の関連が存在し、パターンが有効か。
- `stale`: 生成元が削除・無効、または現在の設計内容がスナップショットと異なるか。
- `source`: 最新の設計保存時点の内容。生成元を削除した場合は最後の内容。
- `acknowledged_source`: 影響ありで生成元が有効なケースに限り、最後に確認済みとなった設計内容を返す。影響のないケースと、旧値を復元できない既存ケースではnull。通常のケース一覧でスナップショットを重複転送しない。
- `status`: `not_run` / `passed` / `failed` / `blocked` / `not_applicable`。
- `actual_result`、`notes`: ケースごとの実行記録。
- `executed_by`、`executed_by_name`、`executed_at`: 最後に結果を保存した認証ユーザーと日時。設計変更確認の`refresh`では更新しない。

書式・表示順だけの変更ではstaleにならない。項目・関連・パターンを削除しても、ケースのスナップショットと実行結果は残る。関連削除時の`item_pattern_id`はnullとなる。設計書そのものの削除のみ、全ケースも削除する。

実行結果の更新と設計変更確認にはケース自身のversionを指定する。古いversionは409。`refresh`は実行結果を保持して影響表示の基準を更新する。削除・無効な生成元は確認済みにできない。

## 制限と将来拡張

設計書あたり項目10,000件、因子100件、水準10,000件、パターン10,000件、選択水準100,000件、紐付け20,000件、任意列100列。セル本文は20,000文字。幅60〜1,200px、行高28〜300px。

全組み合わせ追加はフロントエンドで事前に件数を制限し、既存組み合わせ（無効も含む）を除く。個別追加・除外・無効化は構造化データのまま保存する。将来のpairwise生成や制約式はパターン生成サービスを追加して拡張できる。

現在は設計書全体を取得・保存する。さらに大きい設計書や複数人の同時編集を優先する場合は、行単位の差分APIとversionスコープの細分化が次の拡張点になる。

## 適用・検証

Alembic revision: `2b3c4d5e6f70`（親: `1a2b3c4d5e6f`）。`uv run alembic upgrade head`で適用する。既存のpermissionは変更しない。

`uv run pytest tests/routers/test_test_designs.py`で実DBへのmigration、認可、別設計参照、競合、ケース保持を検証する。OpenAPI変更後はfrontendの`OPENAPI_TARGET=... npm run api:generate`で再生成する。
