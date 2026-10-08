# CodexからSyncnestoを操作する

## 構成と権限

Codex → Mac上のHTTP MCP → FastAPIの連携API → 通常DB。MCPはDB、BFF共有秘密、Supabase・Neon管理資格情報を持たない。
通常アカウントは従来のDB・Storageを維持し、デモとは接続先・認証を分離する。初版ではデモアカウントのMCP接続を許可しない。

接続した本人の現在の権限を毎回検証する。接続で許可したProjectと現在の権限の両方を満たす操作だけ実行する。
標準のproject_admin / manager / memberへ `mcp:connect` を付与する。閲覧専用・テスト実行専用は不可。guest属性でも編集権限があれば利用できる。
カスタムロールは `mcp:connect` と少なくとも一つの要件・設計・タスクの作成/更新/コメント権限が必要。
組織管理者・運営管理者の権限でProjectの業務権限を迂回しない。

## 初回設定

Backendでmigrationを適用し、Git対象外の環境変数を設定する。

```env
MCP_ENABLED=true
MCP_ISSUER_URL=http://127.0.0.1:8000
MCP_RESOURCE_URL=http://127.0.0.1:8765/mcp
FRONTEND_PUBLIC_URL=http://localhost:3000
```

Frontendにもサーバー環境変数 `MCP_ENABLED=true` を設定し、両方を再起動する。
公開APIを対象にする場合のissuerは `https://syncnesto-api.vercel.app`。BackendとFrontendで有効化するまで公開環境の挙動は変わらない。
`APP_ENV` と `DEMO_MODE` はMCP設定によって変更しない。
公開時は既存の `scripts/migrate_production.py` により通常DBと専用デモDBの両方へmigrationを適用してから配布する。
デモからMCPを利用しなくても、デモ破棄処理が参照するスキーマを揃える必要がある。

```bash
cd /Users/kohei/syncnesto/syncnesto-backend
uv run alembic upgrade head
uv run --extra mcp python -m syncnesto_mcp \
  --api-url http://127.0.0.1:8000 \
  --resource-url http://127.0.0.1:8765/mcp
```

上記プロセスを動かしたまま、別のターミナルでCodexへ登録する。Codex CLI 0.162.0-alpha.2でオプションを確認した。

```bash
codex mcp add syncnesto \
  --url http://127.0.0.1:8765/mcp \
  --oauth-client-id syncnesto-codex-local \
  --oauth-resource http://127.0.0.1:8765/mcp
codex mcp login syncnesto
```

ブラウザで通常アカウントにログインし、同じ組織内から許可するProjectを選ぶ。
手動トークン発行・コピーは不要。CodexがOAuth資格情報を管理し、アカウント画面の「Codex・MCPとの接続」で取り消せる。
Mac上の127.0.0.1だけで待ち受ける。Origin/Host保護を無効にしたり、0.0.0.0へ変更しない。

## 業務操作

- 要件定義書/セクション/要件の下書き作成、参照、レビューコメント。
- テスト設計書の作成とグラフへの追加、要件との紐づけ、項目・マトリクスセルのレビューコメント。
- タスクの検索、起票、編集、担当・状態・日程・工数の更新。要件IDを指定した作成、親子タスク。
- 終了→開始の依存関係、マイルストーン作成、日程のプレビューと一括適用。
- 一覧は最大100件、設計の追加は構成要素ごとに最大100件（表20件）、日程変更は最大50件。

権限で実行できるツールだけを一覧に返す。モデルの判断・文章生成はCodexが行い、Backendは入力と参照先を検証して既存Serviceで保存する。
文書やコメント中の文章はレビュー対象データとして扱い、操作の指示として実行しない。
テスト設計に存在しないdraft状態・休日カレンダー・担当者稼働率は追加していない。
日程案ではそれらの前提を `assumptions` に示し、変更前後を利用者が確認してからapplyする。自動スケジューラーではない。

## 指摘箇所・競合・再送

1指摘=1コメント=1回の書き込み。要件は対象種別・ID・version・field・quote・quote_startを必須とする。
quote_startは**元の文字列のUnicode code point単位**。絵文字を含むJavaScriptのUTF-16 offsetと混同しない。
取得した版と引用範囲が一致しない場合は409で拒否し、再取得・再レビューする。

マトリクスの因子水準セルは `target_type=combination`、`target_id=pattern UUID`、
`field=level:<factor UUID>:<level UUID>` で、選択されていない水準も指定できる。
期待値セルは `field=expected:<expected UUID>`。通常の項目は `target_type=test_item` と項目UUID・列キーを指定する。
既存の `level:<factor UUID>` コメントの読込・移動は維持し、新しいMCP指摘には正確な水準IDを要求する。

全書き込みに `idempotency_key` を要求する。同じ呼び出しの再送は同じキー・入力を使う。
異なる操作・入力に同じキーを使うと409。省略とnullを区別し、タスクの部分更新を取り違えない。
既存Service内のcommitはsavepointに閉じ込め、操作・監査・再送結果を外側のtransactionで同時に確定する。
設計への追加はサーバーが最新グラフへ統合し、既存のレイアウト・ケース・実行履歴を保持する。
日程プレビューは15分で失効し、全件の版を確認して一括適用する。

## 認証境界

Authorization Code + PKCE S256。登録済みpublic clientのみで、動的client登録や任意のmetadata URL取得を行わない。
callbackは `http://127.0.0.1:<port>/callback`（または安全なcallbackサブパス）だけを許可する。
resourceは設定したローカルMCPのURLと完全一致が必要。
要求10分、認可コード2分、access10分、接続/refresh絶対期限30日。refreshは一回使用後にローテーションする。
認可コード・refreshの再利用は接続全体を失効する。DBは高エントロピーtokenのSHA-256だけを保存する。
パスワード・メール変更、ユーザー無効化、所属解除・降格・明示的取消を次の操作で反映する。

MCP宛accessは `/oauth/exchange` で60秒の連携API専用JWTへ交換し、通常ログインJWTとは鍵の用途・issuer/audienceを分ける。
通常Cookie・通常JWT・MCP宛accessで専用連携APIを呼べず、派生資格情報で通常APIにログインできない。
同意/取消は既存Cookie/BFF/CSRF。直接経路はOAuth discovery/authorize/token/exchange/revokeとcatalog/operationsの完全一致だけ。
直接経路も公開環境の共有レート制限を通り、偽装したBFFヘッダーからIPを信用しない。通常APIのBFF必須は維持する。
監査には本人・source=mcp・connection_id・操作名を記録し、本文・tokenは含めない。

## 検証

```bash
uv sync --extra dev --extra mcp
uv run ruff check .
uv run pyright
uv run --extra mcp pytest -q
```

OAuth・所属/権限変更・流用拒否・再送・競合・ロールバックと公式SDKのHTTP transportをテストする。
実際の利用者のCodexブラウザ同意、公開環境の有効化は別途実施が必要。
期限切れの接続・認可要求・資格情報・再送結果はアクセスを拒否するが、現時点では定期的な物理削除を行わない。
