# 固定版に付ける添付の保全（Phase 2A-1b）

固定本文APIは `frontend-artifact-releases-api.md` を参照。本文の固定と添付構成の確定は別操作。顧客公開・正式承認・テスト設計graphの固定はこの段階に含まない。

## 権限と境界

すべて `/projects/{project_id}/artifact-releases/{release_id}/attachment-preservations` 配下。内部利用者のみ。既存 `artifact_release:read/create`、固定本文に含まれるすべての種別の閲覧権限、`document:read` を確認し、更新には `document:update` も要求する。初期のUI入口はproject_admin/manager。system roleだけでは操作できない。tenant・Project・session/MFA・CSRFは既存の境界を利用する。元添付の削除後も、現在のProject/本文/資料閲覧権限は必要。

## API

| Method / 末尾 | 入力・結果 |
| --- | --- |
| POST `/preview` | `attachments:[{document_id,attachment_id}]` → 表示属性、合計容量、`plan_digest`。業務保存・コピーなし |
| POST `/` | previewと同じattachments、UUID `request_id`、`expected_plan_digest` → 準備を201。完全に同じ再送は200、同IDで異なる入力は409 |
| GET `/` | page/page_size（既定20、最大100）→ 現在権限で同一範囲のitems/total |
| GET `/{preservation_id}` | 状態・ファイルごとの進捗。内部Storageキー/leaseは返さない |
| POST `/{preservation_id}/advance` | 最大20MiBの1ファイルを処理。全件完了したときだけready |
| POST `/{preservation_id}/cleanup` | `{cancel_preparation:false}` → 非採用コピーを回収。trueで未完成の準備を取消。完成構成の取消・削除は409 |
| GET `/{preservation_id}/files/{file_id}/download` | readyの採用実体のみ。現在権限を確認し `url,expires_in:60`。Cache-Control: no-store |

選択は1〜20件。同じ添付の重複・任意キー・余分な入力を422で拒否。別Projectの添付は両案件の権限があっても混在不可。元資料の版や添付属性が変わった場合は409となり、選択を確認し直す。

## 再開・整合性

- preparing → ready / failed / cancelled。failedは同じ準備IDから再開可能。cancelledは終端。readyの本文との対応・構成・内容hashはDBで封印する。
- コピーの前に専用キーと120秒leaseをDBへ記録する。I/O中はDBロックを保持しない。同時advanceは409。期限切れは新しい試行キーで再開し、遅れて戻る旧処理の採用を拒否する。
- 元のbytesとコピー後のbytesのSHA-256を比較する。全件検証後のmanifestは固定本文hashとファイル属性/hashを含む。元添付のpurgeは新しい専用キーに作用しない。
- 失敗/取消の非採用キーは台帳を残して回収し、cleanupの再実行で遅れて到着したPUTも再回収する。元添付と完成した採用実体は回収対象にしない。
- 更新・DL時に再認可する。署名URLは発行時から最大60秒。転送開始済み・保存済みファイルは回収できない。
- ブラウザは確認したpreviewとrequest_idを再試行中維持する。通信断後は一覧/詳細を読み直し、準備を再開する。

## 移行・戻し

`a7c1e4f9b325` は2表・index・保護triggerのみを追加し、既存資料・添付・Storageキー・permissionは変更しない。保全台帳があるDBのdowngradeは拒否する。コードだけを旧版へ戻す場合も新migration履歴と保全表/実体を残す。新機能の停止とデータ消去を混同しない。

Storage自体の障害・消失に対するバックアップを代替する機能ではない。LocalStackのローカル再起動に伴う実体消失は別の保存運用課題として扱う。
