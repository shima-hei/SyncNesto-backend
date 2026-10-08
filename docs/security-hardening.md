# ポートフォリオ公開環境の保護

この変更は未コミットのAI支援から分離したworktreeで行う。
DB schemaとAPIレスポンスの変更、現在のVercel設定・公開環境へのデプロイは含まない。

## 環境設定による迂回を防ぐ

`Settings.is_public_environment` に公開判定を集約し、`APP_ENV=production` に
BFF共有キー、Secure Cookie、Cookie-only認証、明示的なHost、DB TLS検証、
SQLログ無効、メールorigin/SMTP検証、レート制限、OpenAPI非公開を適用する。

`development` / `test` はローカル互換を維持する。未対応環境名は拒否し、Vercel上では
`production` 以外を拒否する。Previewでも公開用の安全な設定を用意する。
Terraformの現在の `APP_ENV=production` は変更不要。
デモは `APP_ENV=production` + `DEMO_MODE=true` とし、公開保護と機能設定を分離する。
2026-10-08の修正理由と検証は[決定記録](decisions/2026-10-08-demo-mode.md)に残す。

`tests/core/test_ingress.py` でproductionの通常・デモ両モードの共有キー・Host・CSRF・回数制限・
共有カウンター障害・起動設定・メール設定・API docs非公開を確認する。
他の認証・認可・tenancyテストも実行し、変更の影響を検証する。

## 依存関係

BackendのPR CIにロック済み本番依存の `pip-audit` を追加する。
uvで固定バージョンの依存一覧をexportし、監査で依存解決やアプリの実行を行わない。
既知の脆弱性・収集エラーでは失敗する。
監査ツール自体は `pip-audit==2.10.1` に固定し、アプリの本番依存には追加しない。

`.github/dependabot.yml` でuvとGitHub Actionsの更新PRを用意する。
`.github/workflows/dependency-audit.yml` は週次と手動で本番依存を監査し、デプロイしない。
workflowが実行されるのはGitHubへ反映した後で、ローカルでファイルを作るだけでは有効にならない。

Frontendでは修正版へロックを更新し、Production依存の監査、開発依存の週次監査、
install lifecycle scriptの自動実行抑止、nonce付きCSPとProduction HTMLの検証を行う。開発依存に残る未修正のbracesは
Frontendの `docs/deployment.md` に記録する。既知脆弱性の監査はコード監査を代替しない。

## 次の段階

[デモ分離・破棄](portfolio-demo-design.md) を先に実装し、文書・添付も同じ境界へ追加する。
運営・Vercel・GitHub・DB管理アカウントのMFA/Passkey、最小権限とsecret rotation、
backupからの復元確認はクラウドアカウント設定・運用の確認として別に扱う。
コードだけの変更で、これらを設定済みとは扱わない。

AI支援とEveへの変更検討は後続課題で、今回の変更・監査対象に含めない。

## ローカル検証結果（2026-10-07）

| 対象 | 結果 |
| --- | --- |
| Backend ruff / pyright | 成功 |
| Backend pytest | 666 passed、5 skipped、3 xfailed |
| Backend本番依存のpip-audit | 既知脆弱性0件 |
| Frontend format / typecheck / lint / build | 成功 |
| Frontend Nodeテスト | 29 passed（KaTeX安全性の回帰テストを含む） |
| Frontend本番依存のnpm audit | 既知脆弱性0件 |
| Production HTMLのCSP | SSR/テーマnonceの一致・偽装拒否・キャッシュ制限を確認 |
| ブラウザ | Mermaidの図と数式、危険なリンク拒否、HTML内の未許可scriptの実行拒否を確認 |
| Actions workflow | actionlintで成功 |

ブラウザ確認はローカルのProduction buildで行い、Backend接続を伴うログインの
E2E確認と公開環境の確認は含まない。公開環境・GitHub CIへの反映はまだ行っていない。

## 一次資料

- [pip-auditの利用方法と安全性モデル](https://pypi.org/project/pip-audit/)
- [GitHub Dependabotの対応ecosystem](https://docs.github.com/en/code-security/reference/supply-chain-security/supported-ecosystems-and-repositories)
- [Next.jsのCSP](https://nextjs.org/docs/app/guides/content-security-policy)
