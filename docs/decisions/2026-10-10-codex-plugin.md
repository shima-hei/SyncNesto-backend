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

1. 発行者・問い合わせ窓口・プライバシーポリシー・利用規約の確定と公開。
2. Backend/Frontend変更のレビュー・マージ・Backend先行配布。
3. OpenAIへ登録・ZIPアップロード、ドメインchallengeの配布と検証、管理画面のcallback完全一致登録。
4. ホスト上で本人のOAuth同意と業務操作の確認。審査用の分離した通常Project/アカウント・動画等を準備して申請。
5. 承認後の公開、実紹介URLの `MCP_PLUGIN_INSTALL_URL` 設定とFrontend配布、ボタンからの最終確認。

未登録・未申請・未審査・未公開である。架空の紹介URL・規約URLを登録して連携可能と表示しない。
