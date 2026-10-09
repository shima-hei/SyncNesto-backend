# 常時ローカル起動を不要にするリモートMCP

## 合意と変更範囲

2026-10-09: ユーザーの「利用中に起動しっぱなしにしておかなきゃ行けないのやだな」に対し、
既存Vercel FastAPI BackendへMCPを組み込む方針を提示。「OK、その方針で修正して」の承認を受領した。

- Codexから公開Backendの `/mcp` へ接続する。利用者のMac上のMCP常駐プロセスは不要。
- 既存のOAuthブラウザ同意、許可Project、本人の現在の権限、失効、監査、28ツールを維持する。
- 通常アカウントは既存の通常DB・Storage。MCPは通常DBだけを使い、デモアカウントからは利用不可。
- `APP_ENV=production` と `DEMO_MODE` の役割を変更しない。MCPの設定で接続先をデモへ切り替えない。
- 要件・テスト設計の作成と箇所を指定した1件ずつのレビューコメント、タスク起票・編集・日程策定を維持する。
- AI支援・Eveの実装やstashには触れない。新しいクラウド資源・別の有料ホストは追加しない。

## 実装

公式Python SDKのstateless Streamable HTTPを既存Backendへmountする。
親アプリのlifespanからSDKのlifespanを開始・終了し、JSON応答でFunction内に接続セッションを保持しない。
SDKを本体依存関係へ移し、Vercelの通常ビルドにも含める。

認証は既存 `McpAuthService.exchange` を通常DBに対して呼び、用途を分けた60秒の連携API資格情報を取得する。
既存 `authenticate_api` と `McpOperationsService` を直接呼び、自己宛の公開HTTP通信を省く。
業務Service内のcommitは既存 `get_mcp_db` のsavepointへ閉じ込め、書き込み・監査・再送結果を原子的に確定する。
スレッドプールで同期DB処理を実行する。監査には従来どおり本人・接続・操作名を記録する。

公開resourceはissuerと同じoriginのHTTPS `/mcp` のみ許可する。別ホスト・パス・query・fragment・URL内資格情報を拒否する。
MCPはBearer専用でCookieを拒否し、通常APIのBFF必須・Cookie・CSRFを維持する。
直接経路は `/mcp` とresource metadataの完全一致だけを追加し、共有レート制限とHost/Origin検証を適用する。
認証基盤の障害は資格情報の失効と区別して503を返し、内部例外本文を返さない。

旧loopback adapterと登録済みclient IDは開発・既存設定との互換性のため維持する。
公開Backendで別originのローカルMCPを使う構成は廃止する。初版公開時点でMCPは無効であり、既存の公開接続はない。
追加のDB migration・Frontend API変更・Orval再生成は不要。

設計の根拠は[公式Python SDKのmount例](https://github.com/modelcontextprotocol/python-sdk/blob/main/examples/stories/starlette_mount/README.md)と
[VercelのFastAPI lifespan仕様](https://vercel.com/docs/frameworks/backend/fastapi#startup-and-shutdown)。
Functionの終了時処理へ書き込みの確定や失効を依存させず、リクエスト内とDBで完結させる。

## 検証・適用範囲

- MCP関連48件を含むBackend全体890ケースを実行し、882成功・5skip・3既知xfail。失敗なし。
- 組み込みHTTPS入口からOAuth・初期化・28ツール一覧・タスク作成・同一入力の再送・失敗時rollback・監査を検証した。
- 要件の下書き作成、絵文字を含む原文の引用位置を指定したコメントが成功し、古いversionを409として拒否した。
- 別Project、閲覧専用への降格、取消、通常JWTの流用、Cookie、異なるHost/Originを拒否した。認証障害時は内部例外を含めず503。
- GET/DELETEは即時405となり、長時間接続を開かない。MCP有効時も既存OpenAPIが一致し、health=200、未認証POST=401+metadata、未登録path=404を確認した。
- Ruff・format check・Pyright・差分の空白検査が成功。通常ビルド依存関係にSDKが含まれ、固定依存関係の監査で既知の脆弱性なし。
- FrontendとInfraは差分なし。既存のAI支援stashは維持した。

実装時点では公開環境のMCPフラグ・環境変数・Codex登録を変更せず、公開有効化を別の承認対象としていた。
後述の承認を受け、[接続手順](../mcp.md)に従いBackend、Frontendを再デプロイした。
Codexの登録、本人のブラウザでのProject許可、公開環境での一連のツール操作は後続の確認対象とする。

期限切れの接続・認可要求・資格情報・再送結果を物理削除する定期処理は別課題として継続する。

## 公開有効化

2026-10-09: ユーザーから「公開MCPの有効化まで進めて」の承認を受領した。
Backend PR #16のマージ・公開、両ProjectのProduction用MCP設定、Frontendの再デプロイ、公開入口の検証を行う。
通常DB・Storage、`APP_ENV`、`DEMO_MODE`、Preview/Developmentの設定は変更対象に含めない。
本人によるCodexのOAuth同意とProjectの選択は後続の操作とする。

PRの最初のCIはテスト80%まで失敗なく進行したが、20分のjob上限でcancelledとなった（run `37861542876`）。
停止やアサーション失敗ではなく全体実行時間の不足と判断し、checks上限を35分へ変更した。
失敗時の短いtracebackと遅い10ケースの時間を出力し、公開前の全体テストを維持する。アプリコードは変更しない。

PR #16を通常のsquash mergeでmainへ反映した（`fab778657aaa353bd7e26abf22b2924ab1875d49`）。
次の設定をVercelのProductionにだけ追加し、既存の環境変数を変更していないことを確認した。

| Project | 設定 |
| --- | --- |
| `syncnesto-api` | `MCP_ENABLED=true`、`MCP_ISSUER_URL=https://syncnesto-api.vercel.app`、`MCP_RESOURCE_URL=https://syncnesto-api.vercel.app/mcp` |
| `syncnesto` | `MCP_ENABLED=true` |

- Backend main CI [run 37869997089](https://github.com/shima-hei/SyncNesto-backend/actions/runs/37869997089) が成功。
  全体テストは882成功・5skip・3既知xfailで失敗なし（1262.93秒）。Ruff・Pyright・固定依存関係の監査も成功した。
- BackendのProduction deployment `dpl_2jyadacm84XNaGBQFA3LVUvAFpkL` がREADY。
  公開コードは `fab778657aaa353bd7e26abf22b2924ab1875d49`、frameworkはFastAPI。
  CIでのprebuilt作成は約2秒、Vercelのbuild開始からREADYまで約22秒。
  canonical URLは `https://syncnesto-api.vercel.app`。
- Backend公開後の12項目が成功。health=200、resource/authorization server metadata=200、
  未認証・無効BearerのMCP POST=401、Cookie=400、GET/DELETE=405、通常APIの直接アクセス=403を確認した。
  無効refreshの交換も400となり、接続を発行していない。
- この確認後にFrontend mainの再デプロイをdispatchした。
  [run 37871905063](https://github.com/shima-hei/SyncNesto-frontend/actions/runs/37871905063)、
  対象commitは `a235adaaca8a834e63de3fd793eb69c2e02b8bd7`。
  format・型検査・lint・71テスト・依存関係監査・build・Production CSP確認・deployがすべて成功した。
- FrontendのProduction deployment `dpl_C1ZGwiGkx46aCrnjENewmrZenUHG` がREADY。
  frameworkはNext.js。CIでのprebuilt作成は約38秒、Vercelのbuild開始からREADYまで約14秒。
  canonical URLは `https://syncnesto.vercel.app`。
- 両環境の公開後、Backendの12項目にFrontendの4項目を加えた16項目がすべて成功。
  `/login`=200、未認証のBFF `/api/auth/me` と `/api/demo/status`=401、
  `/mcp/authorize` からログイン画面へ戻り先を維持するredirectを確認した。
- 有効なPKCEを含む未認証のOAuth開始も302となり、正しいFrontendの同意画面を経由してログインへ誘導された。
  接続の承認・資格情報の発行・業務データの書き込みは行っていない。
  検証で作成した未承認の認可要求は10分で失効する。
- 2026-10-09 10:56 JST時点で、両ProjectのProductionの直近15分にerrorログなし。
  Teamの外部log drainは既存どおり0件。継続監視やアラートの新設は行っていない。
- 設定追加後も、MCP以外の既存環境変数のID・key・type・targetが一致し、Production以外にMCP設定を追加していない。
  通常アカウント・デモの接続先と権限境界は維持する。

以上の実環境確認は未認証の公開経路までであり、本人によるCodex OAuth同意と認証後のツール実行は未実施。
この結果の追記は文書のみの変更として記録し、検証済みアプリの公開commitは上記のまま維持する。
