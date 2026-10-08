# ポートフォリオ用デモ

公開環境は通常・デモともに `APP_ENV=production` とする。
`DEMO_MODE=true` のときだけ、ログイン画面から登録不要で体験できる。
未設定時はデモ無効。環境と機能の分離方針は[決定記録](decisions/2026-10-08-demo-mode.md)を参照する。
2026-10-08に通常利用との共存を公開済み。最新のデプロイ・CI・公開検証と制限は同じ決定記録を正とする。
訪問者ごとに一時User・Tenant・Projectと要件・タスク・テスト設計書のサンプルを作る。
通常業務と組織内管理を公開し、`tenant_owner` / `project_admin` を使う。
System Roleは付与せず、Backendで運営権限を拒否する。
既存の共有Identity・通常組織・Role・Permission・デフォルトアバターは保持する。
ドキュメント管理・版履歴・添付も同じ所有経路と回収に対応済み。
AI支援・Eveへの変更は取りやめ。将来のCodex向けMCPで要件定義・テスト設計の作成を扱う。

## 認証とデータの境界

既存のHttpOnly Cookie・JWT・DB session・CSRF・tenant ORM guardを使用する。
通常ログインと本人確認メールのリンクは既存DB、通常Storageと通常の署名鍵を継続する。
デモ開始は`DEMO_DATABASE_URL`、デモファイルは`DEMO_AWS_*`へ固定する。
デモJWTは独立した`DEMO_SECRET_KEY`と固定audienceで署名・検証し、検証後にだけ専用DBを選ぶ。
数値ID・未検証claim・利用者指定の接続先で切り替えず、通常DBへのfallbackもしない。
通常とデモのファイル登録用トークンも署名鍵を分け、デモUUIDに紐付ける。
デモのsidと一時組織を一対一で結び付け、組織の切り替えを許可しない。
業務データは既存のProject所属とpermissionで認可する。
共通User・Tenant・所属・Session検索にもデモの所有範囲を追加するため、
完全一致メール検索でも別デモや既存Identityを追加・参照できない。

組織内で追加するUserのメールはサーバー側で架空アドレスへ置き換える。
入力したメールは保存・検索に使わない。デモのIdentityは通常ログインから利用できない。
パスワード再設定・メール変更申請は送信せず、既存Identityの認証情報を変更しない。
通常メールのprovider設定は維持し、デモセッションの申請だけを抑止する。
利用者には実情報を入力しない案内を出す。

同じブラウザのタブは同じCookieで同じ環境を利用する。
有効Cookieがある状態の匿名`start`は409で拒否する。
リセットは本人のCSRF・Origin検証と認証が必要。
ログアウト・リセット・認証失効はBroadcastChannelで別タブにも通知する。
BFCacheから戻った業務画面は再読み込みして認証を確認する。

## 寿命と破棄

- 無操作15分、開始から最大60分。通常sessionの設定は変更しない。
- 業務APIの利用でidle期限を延長し、absolute期限を超えない。
- `/auth/me`と`/demo/status`の監視リクエストはidle期限を延長しない。
- UIは30秒ごとに状態を確認し、既知の期限で再確認する。期限後に確認不能な場合もローカル入力を破棄する。
- デモのフォーム・テスト設計の下書きはメモリだけに保持する。
  localStorage / IndexedDBへの書き込み・旧形式の下書き復元はしない。
  リロードすると未保存下書きも消える。通常Userの下書き保存は継続する。

ログアウト・期限切れ・リセットは、まずsessionの失効と`cleanup_pending`を同時にcommitする。
業務更新は各commit直前にデモ行をロックして再検証するため、失効後の書き込みも拒否する。
ログアウト・リセット・期限切れを検知したリクエストではその後に物理回収を試みる。接続・S3・DB削除失敗でも失効を戻さない。
失敗は回収待ち台帳に残し、再試行時刻と固定のエラー種別だけを保存する。

回収はPostgreSQL行ロック`FOR UPDATE SKIP LOCKED`で重複処理を防ぐ。
Function終了・接続切断でロックが解放されるので、永続leaseの期限待ちは不要。
組織の所有経路から先に全対象PKを確定し、FK依存の逆順で削除する。
自己参照の返信・親タスク・User参照は先に解除する。
要件・履歴・コメント・タスク・テスト・実行・証跡・通知・下書き・業務監査・所属・
一時User・session・本人確認申請を削除し、台帳の参照IDも消す。
新しい業務表を追加するときは`tenant_scope.OWNERS`と回収対象を必ず更新する。

全デモファイルは`demo/<UUID>/`配下の非公開オブジェクトにする。
PUT URLを出す前・multipartの書き込み前に、ファイル容量と最終有効期限をDBへ予約する。
S3登録後のDB失敗や未完了のアップロードもprefix回収で除去する。
PUT URLは失効済みsessionでも残り時間内に使用できるため、初回削除後も台帳を残す。
最後のURL期限+60秒後に再度prefixを掃除してから`cleaned`にする。
GET URLは最大60秒、本人のデモ状態を参照する箇所ではsessionの残り時間も上限とする。
発行済みURLの即時失効は保証しない。

日次Cronは期限切れ・回収失敗・遅延PUTを最大10件回収する。
匿名開始でも発行回数を先に消費した後、期限切れの最大2件を回収して容量を空ける。
`cleaned`台帳も24時間経過後のCronで削除する。
ブラウザを閉じただけの場合、idle失効後の物理削除は次の回収まで遅れる。
Vercel HobbyのCronは1日1回で時刻にも幅があるため、翌日の回収となる場合がある。
障害時はさらに再試行が必要。バックアップ内の即時消去は保証しない。

## 上限と保存テーブル

全FunctionでDBの発行ロック・デモ行ロックを共有する。DBが使えない場合は発行を拒否する。

| 対象 | 上限 |
| --- | --- |
| 開始試行 | IPごと3回/UTC時刻枠1時間、IPはHMACのみ保存 |
| 未回収デモ | 全体10件。失効後の回収待ちも含む |
| Project | 1デモ3件。論理削除も含む |
| 一時User | 1デモ10人。利用者本人を含む |
| 業務行 | 1デモ合計500行。履歴・論理削除を含み、通知・業務監査は既存の生成経路で管理 |
| ファイル | 1デモ10予約・合計20MiB・1件5MiB |

直接送信の一時キーと確定キーは同じ予約を引き継ぐ。
失敗した送信の予約もデモ終了まで保持し、連続した失敗で上限を迂回できない。
削除済みファイルも予約を解放せず、使い過ぎの上限はデモ単位の累積として扱う。
上限超過は429`DEMO_LIMIT_REACHED`。

| テーブル | 内容 |
| --- | --- |
| `demo_sessions` | 寿命、失効・回収状態、再試行時刻、完了時刻 |
| `demo_owned_users` | 明示的に一時発行したIdentityの所有台帳 |
| `demo_uploads` | 容量予約と発行済みPUTの期限 |
| `demo_start_budgets` | HMACと時刻枠ごとの開始回数 |

Alembic `4c7c0372061a`はこの4表だけを追加する。既存データの更新・削除は行わない。

## APIと公開設定

| API | 用途 |
| --- | --- |
| `GET /demo/csrf` | 匿名開始用token Cookie、204 |
| `POST /demo/start` | CSRF・完全一致Originを検証して開始、201 |
| `GET /demo/status` | 本人の状態、200 |
| `POST /demo/reset` | 本人の旧環境を失効・回収して再開始、201 |
| `GET /internal/demo/cleanup` | Cron専用。CookieやBFFキーでは認可しない |

`/auth/me`と本人更新の応答は任意の`demo`フィールドを追加する。
通常Userはnull。OrvalはBackend OpenAPIから再生成する。
BFFは`/demo`だけを追加公開し、`/internal`は公開しない。

デモ公開前に、専用DB・制限付きruntime role・専用非公開バケットを用意する。
本番データを複製しない。既存のNeon・S3互換接続の構成は維持する。
Backendには`APP_ENV=production`、`DEMO_MODE=true`、`DEMO_DATA_ISOLATED=true`、
通常設定と別の`DEMO_DATABASE_URL`・`DEMO_SECRET_KEY`・`DEMO_AWS_*`、
HTTPSの`FRONTEND_PUBLIC_URL`、32文字以上の`CRON_SECRET`が必要。
一般の公開設定検証も同時に適用する。
Frontendも`APP_ENV=production`を維持し、server専用`DEMO_MODE=true`で開始ボタンを出す。
秘密は`NEXT_PUBLIC_*`に置かない。旧`APP_ENV=demo`は起動・デプロイ設定生成時に拒否する。

Terraformの`app_env`は`production`のみを許可し、`demo_mode`と`demo_data_isolated`は既定false。
デモ接続を追加してから`demo_mode = true`と`demo_data_isolated = true`を明示し、
通常runtime設定を変更せず、デモ専用設定が専用資源を参照することを確認する。
Terraformはバケットの実際の分離を検証・作成しないため、このフラグを設定確認の代用にしない。
`CRON_SECRET`はBFFキーと別のBackend専用秘密を生成する。

CIは`vercel pull`後に`scripts/configure_demo_deployment.py`を実行し、
秘密を含まない`.vercel/deploy-config.json`を生成する。
`APP_ENV=production`を確認し、デモ受付が有効、または分離確認済み専用接続が残っている間はデモの日次Cronを追加する。
明示した`DELETED_DATA_CLEANUP_MODE`が有効なら、通常DB向けのごみ箱の日次Cronも追加する。
デモを無効にしても専用接続を残し、回収Cron・APIと専用CLIで未回収データを回収する。
デモ受付・専用接続・通常回収がすべて無効ならCronを追加しない。ローカルの手動リリースも同じ手順を使う。
この変更のローカル検証ではcloud apply・Production migration・デプロイを行っていない。

## 検証

PostgreSQLのAPI回帰で別デモのID・組織ヘッダー・User完全一致検索、運営権限拒否、
追加Userと通常Identityの保存、旧Cookie、idle/absolute/JWT期限、監視による延長防止、
リセット、同時発行上限、失敗と遅延PUTの回収を検証する。
Frontendではメモリ下書き・終了後の遅い書き込み・通常下書きの継続を検証する。
Terraform mock planでは通常設定と分離確認なしのデモ拒否を検証する。

2026-10-07のローカル検証ではBackend全体690件成功・5件skip・3件xfail、
ruff / pyright、Frontendの31件・format / typecheck / lint / build / CSP検証、
Terraform validateと7件のmock planが成功した。
別の一時PostgreSQL・LocalStackバケットとProductionビルドのブラウザで、
デモ開始、タスク更新、組織メンバー追加、画像のpresigned PUT、
リセットと別タブの再読込、ログアウトと物理回収、期限切れのアクセス拒否を確認した。
終了後の遅延ファイルもCron APIで回収し、業務行・一時User・ファイルが残らないことを確認した。
390px幅では長いメールも折り返し、横にはみ出さないことを確認した。
ローカルHTTP接続に限りBackendのHTTPS・Secure Cookie要件をテスト用に置き換えたため、
実際のVercel・Neon・非公開ストレージの設定と公開後の動作は別途検証が必要。
