# GitHub Actionsからの本番デプロイ

`.github/workflows/ci-deploy.yml` はPRでruff・pyright・全pytestを確認する。テストはDocker Composeの隔離されたPostgreSQLを使用し、本番DBへ接続しない。

`main` へのpush、または `main` を指定した手動実行で、検証成功後に次の順で進む。

1. Vercelの設定取得と本番build。
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

公開URL: https://syncnesto-portfolio-api.vercel.app

[VercelのGitHub Actions手順](https://vercel.com/kb/guide/how-can-i-use-github-actions-with-vercel)
