# 通常データの定期回収

30日保持の既存ごみ箱回収を、明示した通常組織だけで日次実行する。
Demoはログアウト・セッション期限で全破棄する既存回収を使う。
監査ログの1095日保持・削除済みProjectの回収はこのジョブの対象外。
既存テーブルを使うためmigrationは不要。公開APIの契約・Orvalクライアントは変更しない。

## 有効化

`DELETED_DATA_CLEANUP_MODE` は既定 `disabled`。次の順で運用者が設定する。

1. 通常環境のDB・非公開バケットと、対象組織IDを確認する。
2. 既存CLIで候補の種類・ID・削除日時を確認する。
   `uv run python -m scripts.cleanup_deleted_data --tenant-id <ID>`
3. `DELETED_DATA_CLEANUP_TENANT_IDS` に対象IDをカンマ区切りで設定する（1〜20件）。
4. `DELETED_DATA_CLEANUP_MODE=dry_run` にしてデプロイし、回収候補と監査ログを確認する。
5. 対象が正しいことを確認して `execute` へ変更する。停止は `disabled` と再デプロイ。

`CRON_SECRET` は32文字以上でBFFキーと別にする。Backend専用で、ブラウザ・公開設定JSONには含めない。
有効化には有限の正の保持期間・対象組織指定を必須とし、誤記・Demoとの併用を起動時に拒否する。
DB上のDemo台帳を持つ組織・存在しない組織は対象に含めない。

## 実行境界

`GET /internal/trash/cleanup` は専用Bearer secretだけを受け付ける。
Cookie、運営者・組織管理者権限、BFFキー、queryの組織ID・execute指定は認可・対象指定に使わない。
無効環境は404、不正な秘密は403。正規の応答は `private, no-store`。
内部運用の入口なのでOpenAPIには公開しない。

Vercelの取得済み環境設定からCronを生成する。通常環境ではmode有効時だけこの入口を日次登録し、
`DEMO_MODE=true`では既存 `/internal/demo/cleanup` だけを登録する。
公開環境の`APP_ENV=production`は通常・Demoともに維持する。前のモードのCronは生成設定へ持ち込まない。
UTC 18時（日本時間03時台）。Hobbyは日次まで・時刻に幅がある。
[Vercel Cronの制限](https://vercel.com/docs/cron-jobs/usage-and-pricing)。

## 件数・失敗・結果

- `DELETED_DATA_CLEANUP_LIMIT` は全組織合計の対象ルート資源数。既定20、1〜100。
  所有する子・履歴・添付の件数とは異なり、総件数を示す値ではない。
- 古い削除日時を優先し、各組織を別Sessionで扱う。既存の期限再確認・復元競合対策を維持する。
- PostgreSQLのtransaction advisory lockを、各資源のcommitとは別接続で保持する。
  多重実行は `status=busy` で処理せず終了し、接続終了でロックを解放する。
- 時間予算は既定20秒、1〜40秒。候補・資源・各S3削除の間で確認するsoft budget。
  実行中のDB処理・通信は予算より後に完了する場合がある。S3の接続・読取を5秒、1試行に限定し、
  回収トランザクションのDB lock待ち1秒・各statement 5秒に制限する。
- ファイルの一部だけ削除済みでも、失敗・時間切れならDB行を残す。次回は未完了の候補を再取得する。
  個別失敗では他の候補も処理し、失敗件数ありは503。秘密を含み得る例外本文は回収ログに出さない。
- 結果は `candidate_count` / `purged_count` / `failed_count` / `skipped_count` / `has_more`。
  `has_more` は上限・時間予算による残件の目安。今回見つけた候補数を返し、全体総数ではない。
- 対象がある組織へ `trash.cleanup` を記録し、mode・件数・完全削除数・失敗数・保持日数を監査UIで確認する。
  個別の成功は既存 `<kind>.purged` に対象IDを記録する。

Vercelは失敗時に自動再試行しない。次の日次実行、または対象確認済みの既存CLI `--execute` で再試行する。
大量の残件や60秒のFunction制限に収まらない資源は、運用者の外部ジョブから既存CLIで回収する。
[失敗・多重配信の仕様](https://vercel.com/docs/cron-jobs/manage-cron-jobs)。

コード追加だけでは実データを削除しない。Productionの設定変更・デプロイ・実回収は別のリリース操作として行う。
