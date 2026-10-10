# アカウント設定からCodexへ連携する

## 合意

2026-10-10: ユーザーから「Syncnesto内でCodexと接続するみたいなボタン」を起点にした連携を希望され、
プラグインの追加画面からOAuth同意へ進む方針に「その方針で実装して」の承認を受けた。
OpenAI側のプラグインは未登録であることを確認した。

- 配置は既存のアカウント画面。プロフィール編集とは別の「外部サービス連携」に、Codexの連携開始・接続状態・解除をまとめる。
- 通常アカウントの現在のProject権限を維持し、閲覧専用・テスト実行専用・デモは接続できない。
- 公開MCP・業務ツール・監査・既存接続を再利用する。通常DBとデモDB、APP_ENVとDEMO_MODEは変更しない。
- プラグイン紹介URLは未登録のため未設定。審査・公開後に設定するとボタンを有効化する。
  未設定時は準備中を表示し、存在しない紹介URLや接続成功を作らない。
- CLI設定を一般利用者向けの導線にしない。Codex側への初回追加と本人のOAuth同意は必要。
- 公開申請用パッケージと、公開前の動作検証用パッケージを準備する。
  OpenAIへの本人確認・申請・審査・公開と、正式なプライバシーポリシー等の確定は別の実作業として残る。

## 認証・APIへの影響

- 既存のloopback clientを維持し、プラグイン専用のpublic clientを追加する。両方ともPKCE S256を必須とする。
- プラグインのHTTPS callbackはOpenAI管理画面の正確なURLをBackendのallowlistへ設定してから使う。
  ChatGPTの公式callbackパスだけを許可し、ワイルドカードや任意のHTTPS redirectを追加しない。
- 認可コード・refresh・取消をclient IDに結び付け、別clientからの資格情報の使用を拒否する。
- ブラウザ向けAPIへ現在の利用資格の確認を追加し、同意レスポンスへ検証済みcallbackを追加する。
  Frontendの型とOrvalを再生成し、Frontendでも同意したcallbackへの完全一致を確認する。
- DBモデルの変更はなく、追加migrationは不要。通常APIのBFF/Cookie/CSRF、MCPのBearer境界を維持する。

## 根拠

- [公式のプラグイン配布・直接リンク](https://developers.openai.com/plugins/deploy/app-review)
- [OAuthとcallbackの仕様](https://developers.openai.com/plugins/build/auth)
- [プラグインのパッケージ仕様](https://developers.openai.com/plugins/build/plugins)

## 実装

- Backendへ `syncnesto-openai-plugin`、完全一致callback allowlist、clientに結び付いたcode/refresh/revoke、
  利用資格確認API、同意レスポンスの `redirect_uri` を追加した。既存clientと公開MCPの28業務ツールを維持。
- Frontendのアカウント画面へCodexの開始・状態更新・解除確認を集約した。Orvalを再生成した。
  `MCP_PLUGIN_INSTALL_URL` 未設定・不正時は公開準備中。閲覧/実行専用は開始不可、デモには表示しない。
- `plugins/syncnesto/` に公開申請の元資材、`scripts/build_codex_plugin.py` に固定allowlistのZIP生成を追加した。
  検証用はCodex形式、申請用は実際の発行者・4つの公開URLを必須とするportable形式。
  業務用skillは1指摘1コメント・正確なセル/引用・version・再送キー・日程案の確認手順を含む。
- 公開用MCPのOpenAI認証拡張と汎用Agent Plugins schemaに不一致を確認した。
  公式OpenAI例の形式を維持し、管理画面での受理・接続確認を残した。詳細は `plugins/README.md`。

## 検証と公開状況

- Frontend: 73テスト、format、typecheck、lint、production build、CSP検査を通過。
- ローカルの一時表示fixtureで準備中/接続中/期限切れ/解除済み、解除確認、公開後のリンク、閲覧専用の制限を確認。
  fixtureと一時的な認証ページ設定は削除済み。通常DBや公開データへの書き込みは行っていない。
- Backend: Ruff・Pyright・skillのfrontmatter検証、パッケージのallowlistと未確定申請拒否を確認。
  全体907件を実行し897 passed / 5 skipped / 3 xfailed。
  新しいアカウント用APIをテスト側のIdentity API一覧へ追加していなかった2件が失敗したため修正し、
  MCP・組織境界・設定・パッケージの関連スイートを再実行して133 passed（94.68秒）を確認した。
  全体の再実行はGitHub CIで行う。
- `APP_ENV` / `DEMO_MODE` / DB / Storage / 公開環境の変数は変更していない。AI支援のstashは保持。

## 公開に残る作業

1. 発行者名・問い合わせメールは回答済み。保持/削除・運営条件を確定し、問い合わせ・プライバシー・規約ページを公開する。
2. 日程previewのannotation修正を配布して確認し、期限切れMCP記録等の保持・実削除方針を確定する。
3. OpenAIへ登録・ZIPアップロード、ドメインchallengeの配布と検証、管理画面のcallback完全一致登録。
4. ホスト上で本人のOAuth同意と業務操作の確認。審査用の分離した通常Project/アカウント・動画等を準備して申請。
5. 承認後の公開、実紹介URLの `MCP_PLUGIN_INSTALL_URL` 設定とFrontend配布、ボタンからの最終確認。

未登録・未申請・未審査・未公開である。架空の紹介URL・規約URLを登録して連携可能と表示しない。

## マージ・公開と申請資料の準備

2026-10-10: PR作成後の「次に進めよう」を受け、両PRの成功したCIとheadを確認し、Backend先行で公開する。

- Backend PR #17のCIは899 passed / 5 skipped / 3 xfailed。Ruff・Pyright・本番依存監査も成功した。
- PR #17をmain `b81e93e6d28a3bbce0f9bf3879db8e2696e1c3ef` へsquash mergeした。
  [公開用CI 38020686359](https://github.com/shima-hei/SyncNesto-backend/actions/runs/38020686359)も全成功。
  全体899 passed / 5 skipped / 3 xfailed（1212.30秒）。通常・専用デモの既存migration確認も成功。
- Backend Production `dpl_E3ckMpy9Draotnhxx9Jw36MDJuGo` はREADY。
  対象は `shima-hei / syncnesto-api`、公開commitは上記、frameworkはFastAPI、canonical URLは
  `https://syncnesto-api.vercel.app`。Vercelのbuild開始からREADYまで約26秒。
- Backend公開後の12項目が成功。healthと2つのdiscoveryは200、未認証/無効BearerのMCPは401、
  Cookieは400、GETは405、通常API・利用資格APIの直接呼出しとOpenAPIは403。
  プラグインclientの有効なloopback認可開始は302、任意のHTTPS callbackは400。
  作成した未承認要求は10分で失効する。接続の承認・資格情報の発行・業務書込みは行っていない。
- 上記の確認後にFrontend PR #23をmain `f02cda86484bcd487189c592be9612b2a47e9368` へsquash mergeした。
  [公開用CI 38022063555](https://github.com/shima-hei/SyncNesto-frontend/actions/runs/38022063555)は全成功。
  73件・format/型/lint/build/本番依存監査/CSPを通過。
  Production `dpl_AgoMsspL7McX8BHS6bQaenazkpLR` はREADY。
  対象は `shima-hei / syncnesto`、公開commitは上記、canonical URLは `https://syncnesto.vercel.app`。
  Vercelのbuild開始からREADYまで約15秒。
- Backend新deploymentの公開後から03:52 UTCまでのerror/fatalログは0件。
  この短時間の確認を継続監視の保証として扱わない。

ユーザーから規約は「未作成」、発行者名は **shime-hei**、公開窓口は **syncnesto@gmail.com** と回答を受けた。
GitHubの `shima-hei` から推測して公開名の綴りを変更しない。
manifest・listingの発行者名、窓口、初版release notesへ反映し、パッケージの2テストが成功した。
最新の検証用ZIPを再生成した。公開用の実URLやOpenAIの確認済みidentityを作ったことにはしない。

問い合わせ・プライバシー・規約のレビュー用案を `plugins/publication-draft.ja.md` に保存した。
現在の保持/失効/実削除を区別し、未確定項目を明示した。Webページとして公開していない。
OpenAI管理画面はログイン待ちで、ZIPの実受理・本人確認・challenge・callbackは未確認。
日程previewは再送結果を保存するため、現行の `readOnlyHint=true` は公開審査前の修正対象として記録した。
通常DB/Storage・`APP_ENV`・`DEMO_MODE`・MCP設定・AI支援stashには変更を加えていない。

## 公開後のBFF修正

公開確認で `/api/integrations/mcp/availability` が404になることを発見した。
Backend側の資格APIと生成クライアントは存在するが、Frontend BFFの許可一覧へ追加していなかった。
アカウント画面の利用資格判定に影響するため、完全一致パスを追加した。
OAuth・他の連携APIを公開せず、Cookie/BFF共有キー・Backendの資格/未認証応答を維持する。

- [Frontend PR #24](https://github.com/shima-hei/SyncNesto-frontend/pull/24) に修正とハンドラー経由の回帰テストを追加。
  修正前に資格APIの中継テストが失敗し、修正後に成功することを確認した。
- ローカル75件・format/型/lint/build/CSPとPR CIが成功した。
  main `54369069eb7f559dd939b670e45f163e51e2d6f6` へsquash merge。
  [公開用CI 38022732848](https://github.com/shima-hei/SyncNesto-frontend/actions/runs/38022732848) はchecks/deployとも成功。
- 初回smokeの残り2件は確認コードの前提誤りだった。アカウント未認証は正しく `/login` へ307、
  同意画面は有効なUUIDの `request_id` を付けると戻り先付きログインへ307となる。
  アプリの遷移や機能フラグを変更せず、確認コードの入力と期待値を修正した。

- 再配布したProduction `dpl_6PnZCwJFMQgausE2g3fMSh9A3eYV` はREADY。
  `https://syncnesto.vercel.app` にmain `5436906` が割り当てられていることを確認した。
  Vercelのbuild開始からREADYまで約11秒。
- 再確認17項目は全成功。利用資格APIはBFF経由で未認証401へ戻り、直接呼出し403を維持した。
  health/discovery、ログイン、Cookie/Bearerの境界、アカウント/同意画面の遷移、callback制限も成功。
  有効なloopback認可開始で作った未承認要求は10分で失効する。本人の承認・資格情報発行・業務書込みは行っていない。
- 04:06:45 UTC以降のログ再確認時点で、対象Backend/Frontend deploymentのerror/fatalログはどちらも0件。
  ログ取得範囲の確認であり、継続的な監視や認証後の全業務操作の検証を意味しない。
- Frontendのタスク/ローカル作業メモの「MCP検討は後続」という古い記載を更新し、公開MCP・連携欄の実装済みと
  OpenAI未登録・本人同意未検証を区別した。デプロイ資料から本決定記録へ参照を追加した。
- 今回の追加は申請メタデータと草案・記録のみ。パッケージ2件とRuffが成功した。
  Backendの稼働コードは `b81e93e` のまま。通常/デモの資源・環境変数・AI支援stashは保持した。

## 審査開始の試行・費用条件・preview属性修正

ユーザーから審査まで進める指示と、請求が発生するなら方法を変える条件を受けた。
追加質問への回答は「追加請求なしと確認できれば、本人操作でカード登録して審査を続ける」。
追加請求の有無を未確認のままカード登録を依頼する承認ではない。

- OpenAI管理画面へのログインは完了。プラグインのアップロード開始は未確認のDeveloper identityで止まった。
  個人の本人確認を開始すると既定の有効な支払方法が必須と表示された。
- API課金は未開始・残高0.00 USD。カード保存画面に請求額・費用ゼロの明示がなく、
  公式OpenAI Docsでも登録・本人確認・審査の追加請求ゼロは確定できなかった。
  カード情報入力・保存、クレジット購入、有料利用開始、APIキー作成、モデルAPI呼出しは行っていない。
- 費用の確認事項・停止位置・再開条件を `plugins/publication-status.ja.md` に記録した。
  本人確認とカード情報入力は本人操作とする。方式は無断変更せず、費用条件を満たせない場合は相談する。
- NeonとSupabaseの既存組織はFreeプランを管理APIで確認した。Vercelの今回の管理API応答にはプラン情報がなかった。
  既存クラウドのプラン・資源・公開変数は変更していない。
- 独立して進められる修正として、日程previewの `readOnlyHint` を `false` にした。
  previewは案と再送結果・監査を保存するが、業務日程は変更しないため `destructiveHint=false` を維持する。
  DB・認可・REST APIの変更はなく、FrontendのOrval再生成は不要。
- `plugins/tool-annotations.md` に28ツールの根拠をまとめた。日程previewの回帰テストは修正前に失敗し、
  修正後にパッケージテストと合わせて3件成功した。公開MCPのcatalogと日程の原子的適用の既存テスト2件も成功。
  Ruff・Pyright・`git diff --check` を通過した。全体テストはPRのCIで実行し、公開先のtool scanとの一致は配布後に確認する。

審査は未提出。今回のpreview属性修正も、この記録時点ではローカルの変更であり未配布。
