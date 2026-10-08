# GitHub Actionsからの本番デプロイ

`.github/workflows/ci-deploy.yml` はPRでruff・pyright・本番依存の監査・全pytestを確認する。テストはDocker Composeの隔離されたPostgreSQLを使用し、本番DBへ接続しない。

依存監査の定期実行・公開設定の検証は [公開環境の保護](security-hardening.md) を参照する。

`main` へのpush、または `main` を指定した手動実行で、検証成功後に次の順で進む。

1. Vercelの設定取得と本番build。
   `configure_demo_deployment.py`で取得済み`APP_ENV=production`を検証し、`DEMO_MODE`と通常回収設定からCronを生成する。
   デモの日次回収と通常のごみ箱回収は併用しない。両方無効ならCronは登録しない。
2. `scripts/migrate_production.py` によるAlembic migration。
3. build済みの成果物をVercelへ本番デプロイ。
4. 健康確認200と共有キーなしのAPIアクセス403を確認。

GitHubの `production` Environmentには以下のSecretsを設定済み。デプロイ可能なブランチは `main` に限定する。

- `VERCEL_TOKEN`
- `VERCEL_ORG_ID`
- `VERCEL_PROJECT_ID`
- `MIGRATION_DATABASE_URL`: `syncnesto_owner` のNeon direct URI。`sslmode=verify-full` 必須。

管理URIはmigrationのステップだけへ渡し、Vercelへ登録しない。APIは引き続き制限付き `syncnesto_app` のpooled URIを使う。migrationの生ログは公開しない。失敗するとデプロイを停止する。

migrationは稼働中の旧APIと互換性が必要。列削除・意味変更など互換性のない変更は、追加・切り替え・削除の複数回に分ける。APIのロールバックだけではDB schemaを戻せないため、失敗時に自動downgradeしない。

Vercel CLI62.2.0、Node.js22、Python3.14、uv0.10.11を使用。Git連携・Preview自動デプロイは無効のまま維持する。PRでは本番Secretsを使用しない。

公開URL: https://syncnesto-api.vercel.app

[VercelのGitHub Actions手順](https://vercel.com/kb/guide/how-can-i-use-github-actions-with-vercel)

デモへの切り替え条件・回収仕様は[ポートフォリオ用デモ](portfolio-demo.md)を参照する。
環境変数の正は[2026-10-08の決定記録](decisions/2026-10-08-demo-mode.md)。公開環境の`APP_ENV`は常に`production`とし、デモは`DEMO_MODE=true`で切り替える。
