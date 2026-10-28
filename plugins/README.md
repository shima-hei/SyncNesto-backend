# SyncnestoのCodexプラグイン

公開MCPは既存BackendのHTTPS `/mcp` を利用する。ローカルMCPの常時起動は不要。
`syncnesto/` がportableパッケージのソースで、資格情報を含まない。
公開状態と作業範囲の正本は[Project5文書14](https://syncnesto.vercel.app/projects/joined/5/documents/14)。
以下の過去の接続検証記録と現在の公開状態を混同しない。
希望する発行者名は `shime-hei`、窓口は `syncnesto@gmail.com`。選択する確認済みidentityの公開名との一致は管理画面で確認する。
サポート窓口、プライバシーポリシー、利用規約の実URLが必要。
文面案と未確定事項は [公開登録の草案](publication-draft.ja.md) を参照する。
追加請求なしの条件は維持する。本人確認の完了から費用ゼロやプラグイン審査済みを推定しない。
通常利用者のMFAは維持し、通常DB内の独立した審査用組織・専用Identityだけを期限付きで免除する方針を承認済み。
一般の匿名デモは従来どおり別DB・MCP連携不可。実装・適用状態は進捗文書で確認する。
分離した審査データ・8ケース・動画台本・静的検証は [審査準備](review-plan.ja.md) を参照する。
現在の状態・費用条件・再開時の作業は [公開申請の進捗](publication-status.ja.md)、
ツールの副作用分類は [annotation根拠](tool-annotations.md) を参照する。
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

### 登録済み接続を参照するホストテスト（2026-10-12）

テスト用パッケージはGit外の独立marketplaceに準備した。
初版0.1.0の `.app.json` にPlugin IDを入れた誤りを修正し、0.1.1ではMCP app ID
`asdk_app_6acc60ad42d08191aa90fa9404caa5af` を参照する。
Plugin IDとMCP app IDは別の識別子で、[公式の形式要件](https://developers.openai.com/plugins/deploy/submission-errors)に従う。
このパッケージにはローカルMCP、トークン、審査パスワード、hooksを含めない。
公式CLIの `plugin marketplace add` / `plugin add` と `plugin list` でinstalled/enabledを確認済み。
本人の再起動後もスキルだけが使え、MCPツールは公開されなかった。
0.1.1へ更新後、公式App Serverの読み取り専用 `app/read` は対象を `missingAppIds` に返し、
`app/installed` にも対象はなかった。現在のCodexアカウントから、このDraftのMCP appは参照できていない。
Portalの審査用接続・scan成功と、ChatGPT/Codexで利用可能な接続を分けて確認する。
再起動だけを解決手順にせず、[公式の接続手順](https://developers.openai.com/plugins/deploy/connect-chatgpt)に従い、
本人のログイン後、ChatGPT Pluginsに非公開の `Syncnesto MCP test` を登録・認証した。
操作直前の承認を受け、専用審査Identityの `Plugin review - consented` だけを許可した。
新しいMCP app IDは `asdk_app_6acccb97310081919c85440d4b1f0512`。
0.1.2へmappingを更新し、公式CLIで再インストール・source/cache一致を確認した。
公式App Serverでもこの接続の `enabled=true` / `callable=true`、28ツールすべて有効、
`list_projects` を含むことを確認した。ChatGPT詳細画面にもRead 14 / Write 14が表示された。
既存の会話には実ツールが未読込のため、次は新しい会話でP1の `list_projects` を呼ぶ。
接続設定とツール情報の確認を、モデルからの実呼び出し成功と混同しない。
CLIのインストール成功だけでOAuth接続や8ケースを合格扱いにしない。
公開プラグイン用の依存確認は未公開ローカルmarketplaceを解決せず、`plugin_not_found` となった。
これは参照IDの誤りや、既存OAuth接続の失敗が確定したことを意味しない。
別の同意・接続が必要になった場合は、審査用1 Projectの範囲を維持し、実際の同意画面で確認する。

## 公開申請の準備

非公開下書きのIDを先に取得する場合は、次の専用モードを使う。
未確定の掲載URLを補わず、OAuth設定と固定4ファイルだけをportable形式にする。
下書き保存は審査提出・公開とは別で、必須URLが空なら提出前の検証で止まる。

```bash
uv run python scripts/build_codex_plugin.py /tmp/syncnesto-draft.zip --draft
```

`--draft` と `--submission` / `--listing` は併用しない。
以下の最終申請用モードは従来どおり確定済みURLを必須とする。

1. `listing.example.json` を別ファイルへコピーし、空欄を実際の発行者・公開済みURLにする。
   規約本文・データの取り扱い・問い合わせ窓口は発行者が確定する。架空のURLでは申請しない。
2. 必須項目を検証してportable ZIPを作る。exampleのままでは失敗し、ZIPを出力しない。

   ```bash
   uv run python scripts/build_codex_plugin.py /tmp/syncnesto-submission.zip \
     --submission --listing /path/to/confirmed-listing.json
   ```

3. OpenAIの管理画面で確認済み発行者を選び、MCP付きプラグインとしてZIPをアップロードする。
   client ID=`syncnesto-openai-plugin`、token endpoint auth=`none`、scope=`mcp:work`。
   管理画面の正確なcallbackをBackendの `MCP_PLUGIN_REDIRECT_URIS` に登録・再配布する。
   ドメイン確認では管理画面が提示する `/.well-known/openai-apps-challenge` に正確なchallenge本文を配布する。
   現Draftではchallengeは設定・配布済みで、Domain verifiedを確認済み。
4. 通常アカウントで、同意・許可Projectだけの一覧・下書き作成・1件ずつの箇所指定コメント・
   日程プレビュー・取消・閲覧/実行専用の拒否をホストから検証する。
   manifestに審査用テストケースを収録している。別途、審査用アカウント/Project・動画等を管理画面の要件に従って準備する。
5. 審査へ提出する。承認後、発行者が公開し、実際の紹介URLをFrontendの
   `MCP_PLUGIN_INSTALL_URL` に設定・再配布する。アカウント画面のボタンから一連の導線を確認する。

builderは固定allowlistのファイルだけをZIP化し、`.env`・DB・Git・ローカル設定・tokenを探索しない。

承認済み版から更新するときは、PortalのDownload release ZIPで取得したZIPを保全し、
最終申請用コマンドに `--approved-package /path/to/approved.zip` を追加できる。
package名とMCP/OAuth設定の意味が一致する場合だけ、既存の動画/commerce/対象国を保持する。
任意の旧フィールドや審査用資格情報はコピーしない。掲載URLは従来どおり確定済みlisting JSONが必要。
更新ZIPの生成は、MCP拡張の本番有効化・OpenAIでの審査通過・Publishを意味しない。
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
