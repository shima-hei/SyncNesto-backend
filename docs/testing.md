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
