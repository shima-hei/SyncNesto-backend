# テストの実行方針

開発中は変更したAPIと関連する認証・組織境界を選んで実行し、仕上げとCIでは全体を実行する。
ログはファイルに保存し、成功時は件数・時間、失敗時は該当箇所を確認する。

```sh
uv run pytest -m no_db -q
uv run pytest tests/routers/test_audit_logs.py tests/routers/test_tenants.py -q
uv run pytest -q --durations=10
```

`no_db` はDBを使わないと確認できたケースだけに付ける。DB起動・migration・各ケースのDB初期化とRBAC投入を省略する。
`db` fixtureと併用するとエラーになる。実DBのレート制限を使う公開APIテストはDBありのまま維持する。
DBありのテストが初めて必要になった時にPostgreSQLを起動し、全体終了時に破棄する。
環境変数はテスト収集前に読み込む。DBありの初期化とケース間の分離は維持する。

2026-10-08の簡易棚卸しで完全一致するテスト本文の重複はなかった。意味上の重複を網羅的に調査した結果ではない。
認証、CSRF、組織分離、権限、競合、セッション失効、削除復元のケースは削減していない。

## デモ設定の回帰確認（2026-10-08）

`APP_ENV=production` + `DEMO_MODE`への修正では、まず設定・公開境界・Cron生成をDBなしで確認する。
OpenAPI非公開のケースは共有カウンターをstubにしてルーティングを確認し、実カウンターの拒否・障害は既存の専用ケースで検証する。
デモの組織分離、失効、DB・ファイルの破棄、通常回収との排他は引き続きPostgreSQLを使用する。

```sh
uv run pytest tests/core/test_ingress.py tests/test_demo_deployment.py tests/routers/test_demo.py tests/routers/test_deleted_data_cleanup.py -m no_db -q
uv run pytest -q --durations=10
```

結果と公開反映の状態は[決定記録](decisions/2026-10-08-demo-mode.md)へ追記する。
