# SyncnestoのCodexプラグイン

公開MCPは既存BackendのHTTPS `/mcp` を利用する。ローカルMCPの常時起動は不要。
`syncnesto/` がportableパッケージのソースで、資格情報を含まない。
2026-10-10時点でOpenAI未登録・未申請。サポート窓口、プライバシーポリシー、利用規約の実URLと発行者の確定が必要。
このディレクトリをそのまま「申請完了」「公開済み」と扱わない。

## 検証用パッケージ

```bash
uv run python scripts/build_codex_plugin.py /tmp/syncnesto-codex-validation.zip
```

ZIPはCodex形式の `.codex-plugin/plugin.json`、`.mcp.json`、ロゴ、業務用skillだけを含む。
出力先の既存ファイルを上書きしない。展開してCodexのローカルmarketplaceへ登録し、Plugins Directoryから追加する。
例として、展開先を `<検証用marketplaceのroot>/plugins/syncnesto` とし、同rootに
`.agents/plugins/marketplace.json` を置く。

```json
{
  "name": "syncnesto-validation",
  "interface": { "displayName": "Syncnesto検証用" },
  "plugins": [{
    "name": "syncnesto",
    "source": { "source": "local", "path": "./plugins/syncnesto" },
    "policy": { "installation": "AVAILABLE", "authentication": "ON_INSTALL" },
    "category": "Productivity"
  }]
}
```

アプリを再起動し、検証用marketplaceを選択してプラグインを追加・認証する。
既存のpersonal marketplace設定に上書きしない。
ローカルに置くのは接続設定と手順だけで、MCPサーバーはVercel上へ接続する。
このclientを追加したBackendの配布が先に必要。検証では他利用者と分離した通常Projectを選び、
秘密・実業務データを審査資料へ入れない。

## 公開申請の準備

1. `listing.example.json` を別ファイルへコピーし、空欄を実際の発行者・公開済みURLにする。
   規約本文・データの取り扱い・問い合わせ窓口は発行者が確定する。架空のURLでは申請しない。
2. 必須項目を検証してportable ZIPを作る。exampleのままでは失敗し、ZIPを出力しない。

   ```bash
   uv run python scripts/build_codex_plugin.py /tmp/syncnesto-submission.zip \
     --submission --listing /path/to/confirmed-listing.json
   ```

3. OpenAIの管理画面で発行者確認を完了し、MCP付きプラグインとしてZIPをアップロードする。
   client ID=`syncnesto-openai-plugin`、token endpoint auth=`none`、scope=`mcp:work`。
   管理画面の正確なcallbackをBackendの `MCP_PLUGIN_REDIRECT_URIS` に登録・再配布する。
   ドメイン確認では管理画面が提示する `/.well-known/openai-apps-challenge` に正確なchallenge本文を配布する。
   現時点ではchallenge未発行のため、確認用URLは未配布。
4. 通常アカウントで、同意・許可Projectだけの一覧・下書き作成・1件ずつの箇所指定コメント・
   日程プレビュー・取消・閲覧/実行専用の拒否をホストから検証する。
   manifestに審査用テストケースを収録している。別途、審査用アカウント/Project・動画等を管理画面の要件に従って準備する。
5. 審査へ提出する。承認後、発行者が公開し、実際の紹介URLをFrontendの
   `MCP_PLUGIN_INSTALL_URL` に設定・再配布する。アカウント画面のボタンから一連の導線を確認する。

builderは固定allowlistのファイルだけをZIP化し、`.env`・DB・Git・ローカル設定・tokenを探索しない。
URLの形式検証は公開到達性や規約内容の審査を代行しない。審査・公開は自動実行しない。

2026-10-10の仕様確認では、OpenAI公式のMCP認証例はserver内の `extensions.com.openai.auth` を使用する一方、
Agent Plugins 1.0.0の汎用MCP schemaはserverの追加プロパティを拒否した。
申請用資材はOpenAIが案内する認証拡張の形式を維持し、汎用schemaへ通すために認証設定を削除しない。
この拡張を含むZIPの受理・OAuth設定の読込は、OpenAI管理画面への実アップロードで検証する必要がある。
rootの `plugin.json` は汎用公式schemaの検証を通過した。検証用Codex形式はこの汎用schemaを宣言しない。

仕様: [パッケージ](https://developers.openai.com/plugins/build/plugins)、
[申請項目](https://developers.openai.com/plugins/deploy/submission)、
[OAuth](https://developers.openai.com/plugins/build/auth)、
[CodexのHTTP MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。
