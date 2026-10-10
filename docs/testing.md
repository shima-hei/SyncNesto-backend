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
デモの組織分離、失効、DB・ファイルの破棄、通常回収との接続先分離・併用は引き続きPostgreSQLを使用する。

```sh
uv run pytest tests/core/test_ingress.py tests/test_demo_deployment.py tests/routers/test_demo.py tests/routers/test_deleted_data_cleanup.py -m no_db -q
uv run pytest -q --durations=10
```

結果と公開反映の状態は[決定記録](decisions/2026-10-08-demo-mode.md)へ追記する。

## MFAの回帰確認（2026-10-11）

既存の業務・RBACケースは`pytest_sessionstart`で`MFA_REQUIRED=false`にし、従来のテスト目的を維持する。
製品の既定値は`true`のまま。`tests/routers/test_mfa.py`は各ケースで明示的に必須化し、専用暗号鍵と固定RP/originを設定する。
追加認証前の通常API/MCP拒否、所有者・Permission、OTP再利用、実WebAuthn署名とorigin/RP/UV、
同時復旧コード消費、期限・失効・運営復旧、通知/監査失敗のrollbackを実PostgreSQLで確認する。
Passkeyの署名テストに実機の生体認証・登録操作は含まれない。

```sh
uv run pytest -q tests/routers/test_mfa.py tests/routers/test_demo.py tests/routers/test_demo_coexistence.py tests/routers/test_documents.py
uv run pytest -q > /tmp/syncnesto-tests.log 2>&1
```

MFAテーブル追加時はデモの所有者定義にも追記する。未定義テーブルを無視せず回収を止める既存の安全策を維持する。
今回の対象実行は63件成功。FrontendはMFAのCSRF・Server Guard・BFF中継4件を含む79件成功。
最終のBackend全体実行は`926 passed, 5 skipped, 3 xfailed`（563.55秒）。ruff・pyrightも通過。
実装状態・ローカル適用・公開未適用の区別は [MFA運用手順](mfa.md) に記録する。
