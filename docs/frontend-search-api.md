# 横断検索API

`GET /search?q=認証&category=test&project_id=1&page=1&page_size=20` は現在の組織の参加プロジェクトを横断する。
既存の認証Cookie・サーバーセッション・組織選択・Demo寿命を適用する読み取り専用API。
DBスキーマの変更、外部検索サービス、全文の複製は不要。

## 検索対象と認可

| kind | 対象 | 閲覧権限 | 主な検索フィールド |
| --- | --- | --- | --- |
| requirement_document | 要件定義書 | requirement:read | タイトル、コード、目的、対象システム名 |
| requirement | 個別要件 | requirement:read | タイトル、コード、説明、根拠、受入条件 |
| task | タスク | task:read | タイトル、コード、説明 |
| test_design | テスト設計書 | test_plan:read | 名前、説明 |
| test_item | テスト項目 | test_plan:read | コード、対象機能、観点、内容、前提、データ、手順、期待結果、備考 |
| test_case | テストケース | test_case:read | 保存済み項目の同フィールド、実際の結果、備考 |
| document | ドキュメント | document:read | タイトル、Markdown本文 |

現在の組織、削除されていないProject・ProjectMember、本人のProject roleの
`project:read` と対象の閲覧権限を **UNION前のSQL条件** で確認する。
組織管理者・system_adminでもProject所属や種類別権限を省略しない。
削除済みリソース・削除済み親・テスト項目の区切り行は除外する。
実行履歴として存続するケースは保存済みスナップショットを検索する。
コメント、過去版、添付本文、拡張列、パターンの因子・水準は検索対象外。

## 入出力

- `q`: 前後空白を除いた1〜200文字。大文字小文字を区別しない日本語対応の部分一致。
  `%`・`_`・`\` はワイルドカードでなく文字そのものとして検索する。
- `category`: 未指定は全種類。`requirement` / `task` / `test` / `document`。
- `project_id`: 任意の正整数。権限外・別組織・不存在は結果0件で、名称を返さない。
- `page`: 1〜500、`page_size`: 1〜50、既定20。
- `items`: `kind`, `category`, 文字列の安定 `id`, `project_id`, `project_name`,
  `project_code`, 親の `container_id` またはnull、240文字以内の `title`・`excerpt`、`code`。
  本文の全体、履歴、S3 key・署名URLは返さない。
- `total`: 現在の種類フィルタで閲覧できる一致件数。
- `counts`: 種類フィルタ適用前の4種類の件数。組織・Project・語・閲覧権限条件は結果と同じ。
- `page`・`page_size`: 現在の取得範囲。

名前の完全一致、名前の部分一致、コード、本文の順を優先し、その後はProject ID・kind・IDで安定して並べる。
同じ条件でも権限変更・データ変更があれば次の取得で反映される。
件数と結果はDB内で取得し、案件ごとの認可・本文取得を繰り返さない。

`GET /search/projects?q=案件&page=1&page_size=50&selected_id=1` は同じ所属・Project閲覧条件と
少なくとも1種類の閲覧権限を満たす候補だけを返す。
`items` は `id`, `name`, `project_code`、`total` は候補件数、`selected` は選択中の権限内案件またはnull。
名前・コードで候補を検索でき、500ページまで取得できる。

## UIへの注意

Backend OpenAPIからOrvalを再生成する。検索結果はMarkdown/HTMLではなくReactの文字列として表示する。
要件は親の要件定義書、項目・ケースは親の設計書IDを含めて既存の詳細画面へ接続する。
検索語・条件・ページはURLに保持し、ブラウザの戻る操作で復元する。検索履歴の永続保存は追加しない。

現段階は既存DBの部分一致で、追加インデックスは作成しない。
件数が増えた段階で実測して、権限・組織境界を維持したPostgreSQLインデックスや検索方式を別途検討する。
