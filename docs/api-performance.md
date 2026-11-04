# APIのCPU・SQL再取得抑制

## 共通処理と寿命

- `AuthorizationService.has_project_permission` は既存の組織所属・有効な組織・未削除の案件/案件所属・project Role/Permissionを1回のSQLで確認する。system Roleによる案件アクセスは追加しない。審査Identityの制約は従来通り先に確認する。
- `app/db/read_cache.py` は明示した読み取り処理の中だけで同じ権限集合を再利用する。キーには利用者/案件/組織、Contextにはnormal/demo・組織・本人・トランザクションを含める。処理終了、flush、commit、rollback、savepoint終了で破棄し、未flushの変更がある間は再利用しない。入れ子の処理で書いた場合は外側も無効化する。
- 適用先はMCPのツール一覧/読み取り操作、通知一覧、要件追跡の一覧/詳細、ごみ箱一覧。更新API・MCPの更新前/ロック後の再認可には読み取りキャッシュを設定しない。
- `app/core/schema_cache.py` は固定PydanticモデルのJSON Schemaだけをプロセス内で最大128モデル分再利用する。共有値は文字列で保持し、返却時に独立した辞書へ戻す。権限・業務データ・資格情報・利用者別のツール一覧はここに保存しない。
- SQLの組織条件をSession内で再利用する既存の `tenant_scope_options` は別の寿命/責務として保持する。HTTPレスポンスキャッシュ・要求間の認可結果キャッシュは追加しない。

## タスク共通表示

一覧/ガント/ボード/詳細の `TaskPresentationDataService` は、対象のTask IDだけを渡してブロック状態を一括取得する。未削除の先行Taskが`done`以外で、依存種別が`finish_to_start`の場合だけブロックとする。既存のORM組織・案件所属スコープも適用する。

## 2026-11-04の比較

基点 `8c69d813c80bfdcccb2b236c0ec413378e98ae9a`。同じ使い捨てPostgreSQL・データ・有効なサーバー側セッションで、前→後→前→後の順に各12標本を測定した。100Task・約3分の1に未完了の先行Taskがある条件。ウォームアップ各群2回は除外した。CPUは他要求を重ねないローカルプロセスCPUであり、Vercelの月間削減率ではない。

| 対象 | CPU中央値 前→後 (ms) | SQL 前→後 |
| --- | --- | --- |
| タスク一覧20件 | 97.877 → 33.020 | 42 → 15 |
| タスク一覧100件 | 379.163 → 36.965 | 149 → 15 |
| タスク詳細 | 30.554 → 25.751 | 16 → 14 |
| 案件メンバー候補 | 18.807 → 15.716 | 10 → 9 |
| 通知未読数 | 13.270 → 12.965 | 7 → 7 |
| 要件一覧（空） | 16.301 → 13.770 | 9 → 8 |
| 要件追跡（空） | 49.094 → 23.902 | 23 → 11 |
| ごみ箱（空） | 57.492 → 18.061 | 24 → 10 |
| MCP一覧Service（41ツール） | 364.439 → 3.494 | 132 → 1 |

全9対象で応答内容のハッシュが一致。アバターの署名URLは署名生成そのものを実行し、署名時刻だけ固定した。MCP一覧はService計測で、HTTP/OAuth交換時間は含まない。認証はテスト用セッション発行ヘルパーを使い、ログイン/MFAのCPUは含まない。通知の小差は改善率として評価しない。

再計測:

```bash
uv run python scripts/verify.py --profile full tests/performance/benchmark_api.py
```

基点はその時点のHEADとorigin/mainのmerge-base。テストランナーが専用DBを作成/破棄し、比較結果を `.verify/api-efficiency/measurement.json` へ保存する。通常のpytest収集にはこの手動計測を含めない。失敗・未完了の記録は`complete: true`と区別する。

## 横断確認の範囲と残る計測

認証Dependency、DB接続/組織スコープ、RBAC、MCP認証/dispatch、タスク表示、通知、ホーム、要件追跡/検証、ごみ箱、固定スキーマ生成、フロントエンドの候補検索/通知/更新後失効を確認した。ホームは専用の集計SQLを使い、システム権限と案件権限を混ぜない。固定出力・履歴・承認・書き込みのロック後確認には広いキャッシュを適用しない。

本番のルート別CPU・要求数による重み付け、コールドスタート頻度、PDF/HTML/大きな検証結果の実データでのCPUは未計測。Vercelのルート別CPU取得はObservability Plusの制限で取得できていない。起動時の複数S3クライアント生成は追加候補だが、この変更では扱わない。新依存/有料サービス/DB migration/API契約変更はない。

## 受入条件と戻し方

応答内容、テナント/本人/normal-demoの分離、削除/完了/依存種別の判定、次要求での権限剥奪、flush/rollback後の再認可を維持し、全体テストとCIを別に成功確認する。戻す場合はこのPRのコードをrevertする。データ変換やmigrationの巻き戻しは不要。

## 一次情報

- [SQLAlchemy Session.info](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session.info)
- [SQLAlchemyのプロファイリング](https://docs.sqlalchemy.org/en/20/faq/performance.html)
- [Python lru_cache](https://docs.python.org/3.14/library/functools.html#functools.lru_cache)
- [Vercel Active CPU](https://vercel.com/docs/functions/usage-and-pricing)
