# テストの実行と診断

依存は既存pytest/uvとDockerのみ。アプリ・通常DB・Storageの設定を変更しない。

```bash
uv run python scripts/verify.py --profile changed --base <実際のPR基点> --plan
uv run python scripts/verify.py --profile changed --base <実際のPR基点>
uv run python scripts/verify.py --profile fast
uv run python scripts/verify.py --profile full
uv run python scripts/verify.py --profile full tests/routers/test_notifications.py
```

既存のpytest引数やデバッガが必要なら、使い捨てDBの設定を確認して直接pytestを使える。通常DB/クラウド/標準5432番はfixtureが拒否する。通常はランナーがdotenv・実SMTP/クラウド資格を継承せず、専用Compose名・ランダムloopbackポート・tmpfs DBを作成/破棄する。DB不要モードではDBを起動しない。

結果は `.verify/<run>/result.json`、全ケース/phase/失敗は `report.json`、JUnitは `junit.xml`、起動・収集エラーを含む完全ログは `full.log`。成功時は約1行、失敗時は先頭3件の短いスタックと完全ログの場所を返す。全失敗情報は切り捨てず保存する。timeoutは既定1800秒、終了コード非0・中断・結果欠落・DB回収失敗も失敗とする。プロセスを強制終了してもCIのartifact不足/ジョブ失敗は品質ゲートで成功にならない。

`changed` はmerge-baseからcommit/未commit/未追跡/削除/renameを調べる。明示対応ドメインのみ関連テストと全no_dbを選ぶ。shared、model、schema、認証/認可、migration、fixture、依存、未知の変更、基点不明、空選択は全体へ戻す。静的依存グラフではないため、横断的な契約変更は明示的にfullを使う。PR CIは選択を使わず常に全体を実行する。

`--reuse` は明示指定のローカルだけで1時間以内の成功を再利用する。ファイル内容・未commit/削除・lock・runtime・インストール済みPython依存・scope・固定テスト環境の指紋が一致することが必要。失敗・壊れた記録・CIは再利用しない。Docker engine/外部要因を疑う場合は再利用しない。

DBは毎ケース全表を一括TRUNCATE+RESTART IDENTITYする。最初に実際のseed関数で作ったRBAC初期行をプロセス内に保管し、次のケースでは全行をbulk投入、採番も復元する。commit/複数Session/並行処理の既存テストを保持し、transaction rollbackや同一DBでの並列化へは変更しない。seed自体の既存テストも保持する。

CIは静的検査/依存監査→独立VM・独立DBの2分割→`checks`で全成功とケース集合の完全一致を照合する。ファイル単位の決定的分割なのでクラス/ケース順序を分断しない。分割失敗・欠落・重複・異なる収集集合を拒否する。全ログ/JUnit/JSONは成功・失敗とも7日保存し、PRだけ旧実行を中断する。mainのdeploy条件と必須`checks`名は維持する。

依存監査は独立した必須jobとし、監査失敗でも静的検査に通ったテストの診断を収集できる。最終`checks`は監査を含む全jobの成功を要求し、監査を迂回してmerge/deployできる構成にはしない。

## 維持する検証観点

`no_db` はDBを使わないと確認できたケースだけに付ける。`db` fixtureとの併用はエラーにする。実DBのレート制限を使う公開APIテストはDBありのまま維持する。認証、CSRF、組織分離、権限、競合、セッション失効、削除復元のケースは削減しない。2026-10-08の完全一致本文の棚卸しでは重複がなかったが、意味上の全重複がないことを証明した結果ではない。

デモ設定の回帰では、`APP_ENV=production` + `DEMO_MODE`の設定・公開境界・Cron生成をDBなしで確認する。OpenAPI非公開ケースは共有カウンターをstubにしてルーティングを確認し、実カウンターの拒否・障害は専用ケースで確認する。組織分離、失効、DB/ファイルの破棄、通常回収との接続先分離/併用は実PostgreSQLを使う。結果と公開反映は[デモ決定記録](decisions/2026-10-08-demo-mode.md)に分けて残す。

MFAの既存業務/RBACケースは `pytest_sessionstart` の `MFA_REQUIRED=false` を維持し、製品の既定値trueは変えない。`tests/routers/test_mfa.py` は明示的に必須化し、専用暗号鍵と固定RP/originを使う。追加認証前のAPI/MCP拒否、所有者/Permission、OTP再利用、実WebAuthn署名/origin/RP/UV、同時復旧コード消費、期限/失効/運営復旧、通知/監査失敗のrollbackを実DBで検証する。署名テストと実機の生体認証/登録操作は区別する。

```bash
uv run python scripts/verify.py --profile fast tests/core/test_ingress.py tests/test_demo_deployment.py tests/routers/test_demo.py tests/routers/test_deleted_data_cleanup.py
uv run python scripts/verify.py --profile full tests/routers/test_mfa.py tests/routers/test_demo.py tests/routers/test_demo_coexistence.py tests/routers/test_documents.py
```

MFAテーブル追加時はデモの所有者定義へ追記し、未定義テーブルで回収を止める安全策を維持する。実装・LOCAL適用・公開適用の区別は[MFA運用手順](mfa.md)を参照する。
