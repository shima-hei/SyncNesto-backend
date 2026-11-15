# Syncnesto Active CPU詳細調査（2026-11-05）

対象: Backend main `4eb0c51ddc1acec2468ae1c67c51f3d059e83a8b`。調査ブランチ `investigate/mcp-cpu-20261105`。今回の差分は計測・テスト・診断スクリプト・本書だけで、既存機能の追加最適化は行っていない。本番への配布、設定変更、負荷試験、業務データ更新、追加契約も行っていない。日付・時刻は特記以外JST。

## A. エグゼクティブサマリー

**「8:50〜15:00頃に約8分増えた」原因の請求上の確定には至っていない。** 利用者の追加説明により、瞬間的な増加ではなく約6時間10分の累積増加として調査した。日付は11月4日と仮定して照合しているが、正確な前後の使用量・対象プロジェクトフィルタ・観測日は未取得である。

確認した事実は次の通り。

1. この時間帯にはBackendの本番配布が3回ある。最新4点改善がREADYになったのは13:25:55であり、観測期間全体を最新改善後の消費として評価できない。READY時刻はalias切替時刻そのものではない。
2. 期間中の旧版 `8c69d81` のServiceメソッドと現行を、同じ実SDK HTTP・同じDB/資格情報/応答で比較すると、`tools/list` のCPU平均は **392.868→27.668ms、92.96%減**、SQLは151→16。既に配布した改善がMCP全体でも効くことを確認した。前回の「3.494ms」はcatalog Serviceのみの中央値で、今回のHTTP全体とは測定範囲が異なる。
3. 現行の主な共通負荷は、**MCPの2段階認証・認可に伴うORM処理と、Sessionごとの88モデル分のテナントSQL条件構築**。小さな結果を返すツールでもCPUがかかる。テナント条件は既にSession内で再利用されるが、次のSessionでは作り直す。
4. 実HTTPまで通す新Pythonプロセス5個では、app importのCPU中央値 **1,563.079ms**、初回list_projects **472.603ms**、後続の同要求 **32.391ms**。各processのimport+初回要求CPUの合計中央値は **2,038.736ms**。本番の起動回数は未計測で、8分の原因と断定できない。別の分解測定では初期化1,149.075ms、ウォームcreate_app再生成0.782msだが、本番が毎回create_appを呼ぶコードではない。
5. 全41ツール中、読み取り21ツールを実際のMCP HTTPで測定した。多くはCPU平均28〜59ms。隔離DBの文書1文字更新は平均95.631ms・60 SQLだった。数回のウォーム呼び出しだけで480秒を説明する結果ではない。
6. 別標本の初回ASGI profileでは、MCPに到達する前にFastAPIの実効route/dependency構築が376経路分走った。現行はRESTルーターの後にMCPをmountするため、この初回負荷をMCP要求も負担する。SDKの再生成ではなく、初回の共通ルーティング準備が大きい。

深刻度: CPU枠が小さいため調査継続の優先度は高い。ただし、現時点の残枠・枠到達時刻・本番の最上位ツールは未確認。まず最新配布後だけの要求数/プロセス起動数/CPUを対応付け、その後にテナントSQL式と認可処理の重複を改善候補として扱う。

## B. 実行アーキテクチャ

### 起動とVercel

- `pyproject.toml` のentrypointは `app.main:app`。`vercel.json` は `app/main.py` を1 Functionとして指定し、`maxDuration=60`。アプリ内375 API宣言は別々のFunctionとして設定されていない。VercelのPythonフレームワークも単一Functionとして説明されている。[公式Python Runtime](https://vercel.com/docs/functions/runtimes/python)
- `app/main.py` のモジュール末尾で `app=create_app()`。`create_app()` はルーター/ミドルウェアを登録し、設定が同一originのMCPの場合に `create_embedded_mcp()` を1回呼ぶ。リクエスト処理内でFastAPIやMCPServerを生成している箇所はない。
- 現行本番は `dpl_9c3W1YQxSBuBm4zWqK9MgXjXy63i`、SHA `4eb0c51`、READY、実行リージョン `sin1`。Python/FastAPI/SQLAlchemy/Pydantic/MCPのlock・設定は今回変更していない。

| 11月4日のBackend配布 | SHA | 作成 | READY |
| --- | --- | --- | --- |
| tenant SQL条件の再利用等 | `2eb557a` | 08:50:48 | 08:51:15 |
| 機能・タスクメタデータ更新 | `8c69d81` | 09:16:17 | 09:16:46 |
| 追加4点・横断共通化 | `4eb0c51` | 13:25:29 | 13:25:55 |

根拠: Vercel deployment APIのcreatedAt/ready/meta.githubCommitSha。Deploymentの作成/READYと、人が使った画面や特定要求の配布先確定は区別する。

### MCP・認証・DBの経路

1. 既存Ingress/Host/CSRF/ログの境界を通り、`/mcp` だけをSDKへ渡す。Cookie付きMCP、別Origin/Host、通常JWTの混用は既存ルールで拒否する。
2. `EmbeddedVerifier.verify_token` がworker threadで通常DB Sessionを開き、opaque access tokenのdigest・期限・接続・本人/所属/MFA関連状態・現在の権限を検証し、API専用の短命HS256 JWTへ交換する。
3. `initialize` は主にこの認証とSDK応答。`tools/list` / `tools/call` はさらに `EmbeddedMCP.request` を経由する。
4. 別のMCP DB Session/transaction/savepointで、専用JWTの署名/issuer/audience/期限と現在の接続・本人・所属・権限を再確認し、catalog/業務Serviceを実行する。embedded経路に自己宛HTTP通信はない。
5. `get_mcp_db` は成功時にcommitする。書き込みはoperation lock後にも再認可し、版・再送キー・監査・原子性を保持する。
6. SDK応答は `TextContent` のJSON文字列と `structuredContent` の両方に結果を含む。互換性を守るため今回も両方を維持した。

MCPは `stateless_http=True`、`json_response=True`。親FastAPIのlifespanがSDKのlifespanを開始・終了する。長期セッションをアプリで保持せず、GETのlegacy SSE/DELETEは405。`initialize` はアプリ全体の再起動と同義ではない。[MCP Streamable HTTP仕様](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)

SQLAlchemy 2.0.49 / psycopg2を使用。通常engine/sessionmakerはモジュール共有、demoのfactoryは遅延生成。Sessionの作成は物理接続の新設と同義ではなく、SQLAlchemyのpoolは必要時に接続を取得する。本番の接続新設数・TLS/Neon復帰時間は未計測で、今回のDB時間にも含めない。[SQLAlchemy pooling](https://docs.sqlalchemy.org/en/20/core/pooling.html) / [Neon pooling](https://neon.com/docs/connect/connection-pooling)

### キャッシュ・外部通信

- 固定Pydantic JSON Schemaは最大128モデルのプロセスキャッシュ。本人別catalogや資格情報はグローバル保存しない。
- 明示したread scope内の権限集合は `read_cache` で再利用。flush/transaction終了で失効する。MCPのBearer交換とAPI認証全体には、このread scopeは掛かっていない。
- `tenant_scope_options` はSession内でSQL式を再利用。tenant/user Contextが変わると作り直す。
- 起動時にはS3 clientを6個生成する。署名URLの生成は外部APIリクエスト件数とは区別する。
- 公開embedded MCPはローカルadapterの `httpx.AsyncClient` を使わない。ローカルadapterには15秒のexchange/60秒の操作timeoutがあるが、アプリ独自の自動リトライループはない。
- アプリ/MCP起動経路に常駐ポーリングは見つからない。別経路のパスワード通知や承認メールにはBackgroundTasksがあるため、「システム全体にバックグラウンド処理がない」とは言えない。クライアント側の再取得/再試行頻度は本番未計測。

## C. CPU消費分析

### 方法と誤差

使い捨てPostgreSQL・専用ダミーデータで、実SDKのStreamable HTTP、opaque token検証、JWT交換、DB権限確認を通した。1 manager/1 project、100 Task、1要件、空のテスト設計、80,000字・10,000見出しの文書等を使用した。ログイン/MFA手続きはfixtureで準備し、MFA強制は既存テスト設定のfalse。実運用のユーザー数・データ分布・MFA必須条件まで再現した数値ではない。実行環境はローカルmacOS/Python 3.14.0で、本番LinuxのCPU性能・ファイルキャッシュ・runtimeと同一ではない。起動測定はS3 client生成数の観測のためboto3を先にimportしており、完全に同じimport順でもない。

各read経路を13回直列実行し、最初の1回を分けて残る12回を集計。P95はnearest-rankなので12標本では最大値となり、安定した本番P95の推定には不足する。順番と一時的なCPU差によるノイズも含む。

- Wall: `perf_counter_ns()`。CPU: `process_time_ns()` 差分。後者は全threadのCPUを含み、sleep/I/O待ちとは異なる。[Python time](https://docs.python.org/3.14/library/time.html)
- TestClientとserverが同一プロセスのため、HTTP区間内にもclientやevent loopのCPUが混入し得る。厳密なサーバーthread別CPUではない。
- ASGI経過区間が重なった要求は `overlap=true`。集計ではそのCPUを合算しない。ただし別background thread等の混入はこのフラグだけでは検出できない。
- DB時間はcursor.executeのWall。SQL構築、pool checkout、fetch/ORM変換、transaction commit/rollback、DBサーバーCPUは含まない。WallからDB時間を引いた値をPython CPUとみなさない。[SQLAlchemy profiling](https://docs.sqlalchemy.org/en/20/faq/performance.html)
- serializationはMCP TextContent用 `json.dumps` のみ。SDK最終JSON/Pydantic変換や通常API全体のserializationは未計測。値がない箇所は「未計測」。
- CPUは区間差分でありVercelの請求値ではない。VercelはActive CPUとI/O待機を区別し、複数要求がinstanceを共有する。[Vercel Active CPU](https://vercel.com/docs/functions/usage-and-pricing)

### 読み取りツール上位10（ローカル1呼び出し当たりCPU平均順）

本番の呼び出し頻度順・消費総量順ではない。全ツールが同じ12標本で、空データの経路もある。CPU/Wallはms、サイズはHTTP応答bodyのバイト数。

| 順位 | MCP tool | 回数 | CPU平均 | Wall平均 / P95 | SQL/回 | 最大サイズ | 問題・観点 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | get_test_design | 12 | 58.870 | 94.638 / 101.434 | 29 | 377 | 空でも全graphをread後にcomponentを切る |
| 2 | get_requirement_context | 12 | 58.366 | 96.718 / 152.416 | 25 | 4,401 | 複数関連の取得・認可 |
| 3 | get_project_document_content (outline) | 12 | 51.844 | 73.470 / 144.285 | 18 | 9,238 | 返却50見出しでも全文走査 |
| 4 | get_project_document | 12 | 47.146 | 74.276 / 133.659 | 21 | 380,295 | 本文がtext/structuredに重複 |
| 5 | search_project_items | 12 | 44.255 | 74.247 / 91.672 | 19 | 14,508 | 複数種別を横断 |
| 6 | get_project_brief | 12 | 42.678 | 72.644 / 91.857 | 21 | 2,657 | 複数集計と認可 |
| 7 | list_test_designs | 12 | 40.297 | 67.174 / 107.418 | 19 | 510 | 小結果でも共通コスト |
| 8 | list_test_design_comments | 12 | 39.978 | 61.127 / 111.585 | 19 | 217 | 空結果でも共通コスト |
| 9 | list_requirements | 12 | 39.230 | 65.959 / 104.935 | 19 | 464 | 小結果でも共通コスト |
| 10 | list_tasks (100件) | 12 | 38.093 | 59.456 / 65.980 | 19 | 44,364 | 100行のORM/summary変換 |

他のreadも含む全21ツールの標本・合計CPU/Wall・サイズ平均/最大・SQL件数は `.verify/mcp-cpu-20261105/http.json`。本番ツール別回数/CPU上位10は**未計測**。残る20ツール（preview等を含む非readOnly）の順位は調査していない。

### 共通負荷の切り分け

| 処理 | 回数/条件 | CPU実測 | 根拠 | 解釈 |
| --- | --- | --- | --- | --- |
| 新processでapp import→実HTTP | 5 process、MCP ON | import中央値1,563.079ms、初回要求472.603ms | oauth-cold.json | import+初回の合計中央値2,038.736ms。毎要求ではない |
| 分解用の新process app import | 別5標本、MCP ON | 中央値1,149.075ms | startup.json | boto3等の事前import条件が異なる。上の群と合算しない |
| MCP OFFでapp import | 別5標本 | 中央値1,005.261ms | startup.json | 差143.814msはこの環境の差。MCP全体を分離した厳密因果値ではない |
| ウォームcreate_app | MCP ON、各process1回 | 中央値0.782ms | startup.json | 通常要求で呼んでいない対照 |
| S3 client初期化 | 起動ごと6個 | 合計中央値52.020ms | startup.json | 起動CPUの約4.53%。これだけを最大原因とはしない |
| 88モデルのtenant条件構築 | 新Sessionで30標本 | 中央値4.519ms | tenant-scope.json | 既存のownership_filter再帰261回/Sessionをprofileでも確認 |
| 同SessionでのSQL条件再利用 | 同30標本 | 中央値0.058ms | tenant-scope.json | 既存改善は効いている |
| 上記SELECTのcache key構築 | 同30標本 | 中央値2.315ms | tenant-scope.json | 88モデル分のoptionsの走査。DB実行なし |
| tools/listのBearer交換 | warm12標本 | 平均13.550ms | http.json phases | DB/認可込み、API認証と別区間 |
| tools/listのAPI認証 | 同12標本 | 平均10.431ms | http.json phases | 上記と合わせCPU平均の約78.1%。認証省略の許可ではない |
| 文書80,000字のtext JSON | 同12標本 | CPU平均0.214ms | http.json phases | 最終SDK encodingを含まない |

cProfileは別標本をworker内のCPU時計で採取。tenant_scopeの条件構築、SQLAlchemy cache key生成、RBAC取得が上位に出た。ただしprofile自体で約4倍以上遅くなる標本があり、profile付きmsを通常測定のmsへ加算したり、異なる表の区間を足したりしない。startupのprofileも通常5標本と分けた。

初回HTTPのevent loopも別の新process1標本でprofileした。FastAPI `routing.py` の `effective_candidates → _build_effective_context → from_api_route → _populate_api_route_state` が376回、`get_dependant` が4,652回、TypeAdapter初期化が3,152回呼ばれた。profile付き初回CPUは2,737.894msと大きく膨らみ、その中でroute/dependency準備が上位だった。これはCPU区間の手掛かりであり、通常初回472.603msの内訳をその比率で確定したものではない。対象版の公式ソースでも、候補の初回構築・バージョンに応じた再利用と、RESTルーターからの順次matchを確認した。[FastAPI 0.162.0 routing.py](https://github.com/fastapi/fastapi/blob/0.162.0/fastapi/routing.py)

## D. MCPと再現試験の結果

### A〜H

warmは各12標本。E/F/Hは同じread操作の初回と連続再実行で比較した。初回は「process cold」ではなく、その経路の最初の計測対象リクエストである。

| ケース | 操作 | 回数 | 合計Wall | 合計CPU | Wall平均 / P95 | SQL/回 | 応答平均 / 最大bytes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | アクセスなしで待機 | 0 | 255.022 | 0.028 | 未計測 | 0 | 0 / 0 |
| B | ヘルスチェック | 12 | 13.987 | 10.077 | 1.166 / 1.706 | 0 | 29 / 29 |
| C | initializeのみ | 12 | 226.378 | 159.471 | 18.865 / 21.128 | 7 | 562 / 562 |
| D | tools/list | 12 | 562.916 | 368.638 | 46.910 / 73.356 | 16 | 70,722 / 70,722 |
| E | list_projects初回 | 1 | 48.905 | 28.589 | 48.905 / 未計測 | 17 | 316 / 316 |
| F/H | list_projects連続・warm | 12 | 597.453 | 337.971 | 49.788 / 53.131 | 17 | 316 / 316 |
| G | 通常REST Task100件 | 12 | 691.882 | 454.100 | 57.657 / 94.269 | 15 | 80,420 / 80,420 |

CPU合計はプロセス区間差分の和で、直列・ローカル条件に限る。

- 初回に目立った例: `list_task_comments` CPU96.632ms→warm中央値33.695ms、`search_project_items` 133.036→42.721ms。初回経路のSQL構築等はwarmと区別する。
- 新process5個でappをimportして最初にlist_projectsを呼ぶ試験も追加した。import CPU中央値1,563.079ms/Wall1,595.689ms、初回要求CPU472.603ms/Wall515.479ms、同じprocessでの後続要求15標本はCPU中央値32.391ms/Wall52.331ms。初回/後続とも17 SQLだった。初回にSQL件数が増えた結果ではない。OSの真のcold filesystemやVercel runtimeまでは再現していない。
- OAuth authorization metadataはwarm CPU平均0.575ms・SQL0、resource metadataは0.706ms・SQL0。metadata単体は軽いが、休眠instanceを起こす場合の起動コストはこの値に入らない。
- `/oauth/token` は隔離資格情報を実際に回転して追加測定。refresh grantはwarm12標本、CPU合計325.659ms/平均27.138ms、Wall合計459.737ms/平均38.311ms/P95 86.153ms、13 SQL/回、応答224bytes。authorization_code grantは1標本だけCPU32.435ms/Wall48.706ms、12 SQL、応答224bytesでP95は未計測。access tokenは10分、API JWTは最大60秒、refreshは接続の絶対期限までという実装。実clientのrefresh/reconnect頻度は未特定。
- `notifications/initialized` は12回の実POSTで202/空body、7 SQL/回。最初はCPU381.996ms/Wall461.185ms、12回のCPU中央値22.768ms、2回目以降のCPU平均29.103msだった。全12回のCPU合計702.129ms/平均58.511ms、Wall合計924.360ms/平均77.030ms/P95 461.185msと、初回値が平均を押し上げる。サーバー側の独自自動再試行・tools再取得ループは見つからず、Codex側の不要な再取得や本番頻度は未確定。
- 文書更新は隔離DBだけで5回。80,000字文書の先頭1字を変更し、CPU合計478.154ms・平均95.631ms、Wall合計787.208ms・P95 184.083ms、60 SQL/回、応答565bytes。ロック・再認可・履歴・監査・再送結果の保存を含む。DBはランナーが回収した。

### 旧配布との差

`8c69d81` に存在するServiceメソッドをGitから読み、同一の現行SDK/HTTP/DBに差し替えた診断上の再現。完全な旧デプロイ環境の再実行ではない。前→後→前→後、各群2回warmup後6標本、合計12標本ずつ。HTTP body SHA256一致を確認。

| 対象 | CPU平均 旧→現行 | 削減率 | SQL/回 旧→現行 |
| --- | --- | --- | --- |
| tools/list | 392.868→27.668ms | 92.96% | 151→16 |
| MCP list_tasks 100件 | 62.318→40.916ms | 34.34% | 28→19 |

既配布の改善の検証であり、今回新しい最適化を導入した結果ではない。MCP list_tasksは通常RESTの表示用blocked判定と経路が異なり、前回REST100件の約90%削減をそのまま当てはめられない。

### 本番で取得できた範囲

11月5日1:43頃の直近30分を別途取得し、runtime eventは `/mcp`64、authorization metadata9、token7、resource metadata2。これは利用者の8:50〜15:00と同一期間ではなく、HTTP要求数とも同義ではない。件数を時間で延長して当該時間帯の使用量へ換算しない。

対象期間のCPUをMCPで照会すると400、同じproject/team/期間をVercel CLI 62.2.0のnative metricsでsum照会すると `payment_required: Observability Plus is required`。本番CPU/要求数/ツール頻度/コールド起動頻度を同一期間で取得できていない。ランタイムログもその過去期間を保持していない。未取得を0件と解釈せず、追加契約もしていない。

## E. 改善提案（今回未実装）

| 優先度 | 現在の問題・根拠 | 変更案 | 削減効果 | 難易度 | 副作用・必要な保護 |
| --- | --- | --- | --- | --- | --- |
| P0 | 本番CPUと要求・起動回数が未対応 | 最新版だけの短い観測期間で、既存Usageと要求/起動を対応付ける | 直接削減なし、原因確定の前提 | 小〜中 | 追加ログCPU、保存期間、請求値との区別 |
| P1 | 初回MCPがREST376経路の実効route/dependency準備も負担 | /mcp等の正確な既存SDK経路を先にdispatchできる局所的な経路順を検討する | 未検証。初回472.603msの一部が対象で、import1.563秒全体は消えない | 中 | 共通Ingress/Host/CSRF/ログ・SDK認証を維持。REST初回へ移しただけなら総CPU削減と呼ばない |
| P0 | Sessionごと88モデル条件を構築、cache keyも全options走査 | 対象ORM/alias/subquery/relationshipへ同じ条件が必ず伝播する設計を検討し、構造の再生成/走査を減らす | 未検証。構築4.519ms/Sessionとkey2.315ms/SELECTが対象区間 | 大 | テナント漏えい防止が最優先。条件を単に削除・無効化しない |
| P1 | eligibleの複数permission照会。2段階認証は必要な境界 | 1回の検証操作内で、本人/所属確認後に取った権限集合を同じContext内で再利用する案 | 未検証。16 SQLのcatalog等で減る可能性。78.1%全てが削減可能ではない | 中 | JWT検証、credential失効、MFA、所属、接続同意、書込ロック後再認可を維持 |
| P1 | get_test_designがgraph全体を展開後にcomponent/pageを切る | 指定componentの取得とcountをDB段階へ寄せる | 未検証。空でも29 SQL。大graphで増えるか追加測定 | 中 | 既存順序、total、権限、component契約、原履歴を維持 |
| P1 | outlineは返却50件でも80,000字全体を走査 | 権限確認後、document/versionに対応する見出し等の派生結果再利用を検討 | 未検証。転送量減少とCPU減少を同一視しない | 中 | tenant/文書/版、削除/失効、上限、保存先と寿命を設計 |
| P1 | クライアントのtools/list・initialize・reconnect頻度不明 | 計測後に必要なら接続の再利用・不要な一覧再取得を削減 | 未検証。削減した呼出数×実測単価で評価 | 中 | client挙動はサーバーだけで制御できない。権限変更を古い一覧で通さない |
| P2 | import時モデル・FastAPI dependency構築のCPU | 未使用経路の重いimportを遅延できるか局所比較 | 未検証。本番cold回数次第 | 中〜大 | 初回経路遅延、登録漏れ、lifespan、エラータイミング |
| P2 | 起動時S3 client6個・約52ms | 用途/設定/realm/timeoutが同じ場合の共有・遅延生成案 | 未検証。全廃でも今回のstartupの4.53%が対象上限の目安 | 中 | 設定の混同、demo分離、thread safety、client寿命 |
| P2 | MCPの本文が2表現へ重複 | 既存の部分取得の利用を優先。応答互換性を変えるなら別途合意 | CPU削減未検証。全文380,295→outline9,238bytesはデータ転送差の実測 | 中〜大 | structured/textを使うclient互換性。片方を勝手に削除しない |

DB接続設定変更、サーバー移設、有料キャッシュや分散キャッシュ導入を現時点のCPU根拠だけで推奨しない。認可結果を要求間で共有するキャッシュや署名/失効チェックの省略は提案対象外。

## F. コスト影響と「8分」の解釈

HobbyのActive CPUは月4時間（14,400秒）。8分=480秒はその3.33%。これは枠に対する算術であり、今回の増分が確実にこの量だったと検証した値ではない。[公式の含有量と課金範囲](https://vercel.com/docs/functions/usage-and-pricing)

今回のローカル単価に480秒を割り付けると、旧tools/listなら約1,222回、現行tools/listなら約17,349回、隔離文書更新なら約5,019回、新processのimport+初回要求なら約236 processに相当する。**いずれも仮定上の換算であり本番の回数ではない。** 通常API/他client/旧版/初回起動等の混在、実CPU性能差、計測対象外のruntime処理、Usage反映時刻を確定できていない。

旧→現行のtools/listをN回置き換えた場合のローカルCPU差は `N × 365.199ms`。例えば1,000回なら365.2秒=約6.09分。一方、月間全体の削減率を92.96%とすることはできない。仮に月間CPUのうち旧tools/listが占める割合をfと置けば、その部分だけの効果は `f × 92.96%` だが、fは未計測である。

残るtenant条件構築・認可・起動を改善した場合の削減率は未検証。条件構築を全て消せるという非現実的な前提や、profile付き時間の比率を使って大きな削減率を約束しない。

## G. 次の実装タスク案

業務タスクの登録や日程変更はしていない。以下は独立して採否を判断できる提案。

| ID | 対象 | 方針・受け入れ条件 | 検証方法 |
| --- | --- | --- | --- |
| CPU-01 | 本書/診断設定/既存Usage | 最新配布固定・対象project/UTC開始終了・Usage前後値を揃える。要求数、MCP method/tool、first-observed processを分ける。CPU帰属不能を隠さない | 本番は許可された短い自然利用だけ。負荷試験なし。重複/overlap排除、追加CPUと請求値の差を記録 |
| CPU-02 | app/db/tenant_scope.py、tenant境界テスト | 88モデル共通optionsの構築とcache key走査を減らす設計を先にレビュー。認可/条件の意味不変 | tenant/user/normal-demo切替、alias/subquery/relationship、ORM/Core経路、更新/削除、権限剥奪の代表ケースとfull。現行/案を同データで直列比較 |
| CPU-03 | app/services/mcp_auth.py、authorization.py | 1回の検証内だけ権限集合の重複取得を削減。Bearer/APIの境界、署名/期限/失効/同意を保持。書込ロック後は再確認 | OAuth・期限/取消・MFA・審査Identity・非所属/権限変更・write原子性。HTTP応答一致/SQL数/CPU前後比較 |
| CPU-04 | app/services/mcp_operations.py、test_design系Repository | 指定component/page以外のgraph展開を減らす。既存のitems/total/順序を保持 | 0/1/100超・別component・権限外・大graph、取得行数と返却サイズ/CPU/SQLの関係を測定 |
| CPU-05 | app/services/mcp_document_content.py等 | 見出し等の版付き派生結果の再利用を設計。認可後だけ使用し保存量を制限 | 同版/別版/更新/削除/失効/別tenant/Unicode/fence/継続offset。全文hashと結果一致、CPU差、メモリ上限 |
| CPU-06 | app/main.py、ルーターimport、storage.py | 起動profileで大きい箇所を1対象ずつ遅延/共有の案として評価 | 新process×5、初回要求、warm、schema/全route/Storage設定/realm。初回へコストを移しただけかも確認 |
| CPU-07 | syncnesto_mcp/server.py、クライアント設定 | 頻度の実測後に不要な再初期化/一覧取得の抑制を検討 | read/write/権限変更/接続期限/再接続。削減したHTTP件数とCPU単価を別々に検証 |
| CPU-08 | app/main.py、app/routers/__init__.py、transportテスト | 既存のMCP正確経路だけをREST探索より先にdispatchする案を比較。/mcp以外を包括mountで隠さず、境界とSDK処理を維持 | 全経路の到達性、metadata、末尾slash/405/Host/Origin/Cookie、OAuth/認可、cold→MCPのみとcold→MCP→RESTを同じ回数で計測。単なるコスト移動を除く |

## 追加した計測の扱い・変更ファイル

`CPU_DIAGNOSTICS_ENABLED` は既定false。起動時にtrueにした場合だけASGI wrapper、SDK method middleware、normal engineのSQLイベントを追加する。OFFで起動した通常APIにはこのwrapper/SQL listenerを登録しない。MCPの局所phaseはOFFでもContextVarの確認だけ残る（空phase中央値ではなく平均448ns/回の単独loop実測）。一度ONで作ったprocessをOFFへ戻す場合は再起動し、既存listenerの残存を避ける。

計測値は `app.core.cpu_diagnostics` loggerから1要求1JSON。LOG_LEVEL=INFOで採取し、集計用JSONLにはLOG_FORMAT=jsonを併用する。内部生成のdiagnostic_id/process_instance、正規化route、既知MCP method/tool、CPU/Wall、SQL数/execute Wall、text serialization、送出body bytes、HTTP status、応答完了、first-observedフラグを記録する。外部から来るRequest ID、UA/IP、Cookie、token、SQL/parameters、本文、tenant/userの生IDはこの診断に記録しない。未知pathはunmatched、未知toolはnull。例外は本文を保存せず、status未確定をnullとする。ASGI本体を透過的に包み、本文を先読みしない構成は[Starletteのmiddleware設計](https://starlette.dev/middleware/)を参照した。SDK method hookは現在固定のMCP 2.3.0の内部server middlewareに接続しており、SDK更新時はtransport回帰検証が必要。

first-observed/診断module ageは起動の推定情報で、真のprocess開始時刻やVercel cold-start判定ではない。response bytesはASGI body（Vercel圧縮後や通信headerを含まない）。demo engineのDB数/時間はこのprobeの対象外。serialization nullは0ではない。区間は重なるためphasesを単純合計しない。

計装ON/OFFの同条件MCP比較40標本ずつでは、clientを含むCPU中央値 **28.539→30.790ms（+7.89%）**、平均31.094→33.460ms（+7.61%）だった。ヘルスチェック中央値0.650→0.705ms。観測コストはゼロではなく、この結果はログ収集込みのローカル値。本番で常時ONにする設計として採用したものではない。ON/OFFでMCP成功本文一致、無効token401/Cookie400/不正Origin403の境界一致を確認した。

| 変更ファイル（Backend基準） | 理由 |
| --- | --- |
| app/core/cpu_diagnostics.py | opt-in ASGI/SQL/区間/MCP method計測、並行/秘匿境界 |
| app/core/config.py | 既定OFF設定 |
| app/main.py | 明示ONのときだけ計装を登録 |
| app/mcp.py | Bearer/API認証・text JSON区間、SDK method観測 |
| syncnesto_mcp/server.py | 同じjson.dumps出力のままencode_resultを観測可能にする |
| scripts/profile_startup.py | 既存env/通常DBを継承しない新process起動測定 |
| scripts/summarize_cpu.py | 重複/overlapを扱う集計、平均/P95/SQL/サイズ |
| tests/core/test_cpu_diagnostics.py | OFF/情報保護/並行/例外/集計回帰 |
| tests/performance/benchmark_mcp_cpu.py | 実HTTP21 read・A〜H・隔離write・worker profile |
| tests/performance/benchmark_mcp_history.py | 旧配布と現行のHTTP応答一致・CPU差 |
| tests/performance/benchmark_mcp_probe.py | ON/OFFのCPUと認証境界の一致 |
| tests/performance/benchmark_oauth_cold.py | 隔離grant回転、通知、子processの初回HTTP/profile |
| tests/performance/benchmark_tenant_scope.py | 既存条件生成/再利用/cache keyのCPU |
| .gitignore / 本書 | 調査レポートをGit管理対象にする |

API/DB schema、依存、既存権限、生成frontendコード、課金設定の変更はない。通常3000/8000・本人UATのソース/プロセス/DBは切り替えていない。調査は既存提出worktree `/Users/kohei/syncnesto/.worktrees/ux-release-20261023/backend` で行った。報告・検証成果物はローカルで、GitHub CI/レビュー/本番反映とは分ける。本番DB変更禁止の依頼に従い、Syncnesto文書/Taskへの書き込みも行っていない。

### 再現・証拠

```bash
uv run python scripts/profile_startup.py
uv run python scripts/verify.py --profile full tests/performance/benchmark_mcp_cpu.py
uv run python scripts/verify.py --profile full tests/performance/benchmark_mcp_history.py
uv run python scripts/verify.py --profile full tests/performance/benchmark_mcp_probe.py
uv run python scripts/verify.py --profile full tests/performance/benchmark_tenant_scope.py
uv run python scripts/verify.py --profile full tests/performance/benchmark_oauth_cold.py
```

結果は `.verify/mcp-cpu-20261105/` の `startup.json`、`http.json`、`historical-http.json`、`mcp-probe-overhead.json`、`tenant-scope.json`、`phase-overhead.json`、`oauth-cold.json`、`cold-event-loop-profile.json`、`production-evidence.json`。通常pytestは手動benchmarkを収集しない。各ランナーは一時DBを作成/回収する。起動スクリプトは環境をallowlistで作り、dummy設定のみを与え、DB/S3へ問い合わせない。HTTP用の子processは同じ使い捨てDBを参照する。出力先・入力データを本番DBへ切り替えて使わない。

検証: 新規境界/条件診断6 passed、HTTP診断2 passed、旧版比較1 passed、ON/OFF比較1 passed、OAuth/cold1 passed、初回event loop profile1 passed。Ruff/Pyright/format/diff確認成功。共通処理変更の全体検証は **1,626 passed、既存xfail 3、skip 5、complete=true**、993.48秒。timeout/失敗なし、一時DB回収成功。結果は `.verify/20261104T165815-5915c861/result.json`。全体テスト開始後に本番runtimeコードを変更していない。追加の手動cold診断は上記の専用ランで別に成功しており、full件数へ加算しない。GitHub CIは未実行。初回HTTP診断はTestClientのlifespanを同じSDK instanceで二重開始して失敗し、harnessを1 lifespanへ直して成功した。初期の型エラーも修正済み。失敗を削除/skipしていない。

### 完了条件の評価

- 起動/認証/DB/シリアライズ/キャッシュの構造、cold/warm、21 read toolの局所CPU、通常APIと隔離write、観測の限界、提案・独立タスク、計測ON/OFF負荷は確認した。
- 本番8分の因果確定、本番ツール別CPU上位10、正確なcold-start/refresh頻度、最終SDK/通常API全体のserialization、本番の取得行数は未計測。本書に未計測を明記し、ローカル値で代用しない。
