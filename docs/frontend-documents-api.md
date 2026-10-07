# プロジェクトのドキュメント管理

要件定義書とは独立した、作業ガイド・議事録などの共有文書。
既存のCookie / CSRF / 現在組織 / Project RBACを適用する。
全APIのprefixは `/projects/{project_id}/documents`。

## APIと権限

| 操作 | Path | permission |
| --- | --- | --- |
| GET一覧・POST作成 | 空 | `document:read` / `document:create` |
| GET・PATCH・DELETE本文 | `/{id}` | `document:read` / `document:update` / `document:delete` |
| GET版一覧・指定版 | `/{id}/revisions`・`/{id}/revisions/{number}` | `document:read` |
| GET添付一覧・POSTサーバー添付 | `/{id}/attachments` | `document:read` / `document:update` |
| POST送信計画・確定 | `/{id}/attachments/upload-plan`・`upload-complete` | `document:update` |
| GETダウンロード・DELETE添付 | `/{id}/attachments/{uuid}/download`・`/{uuid}` | `document:read` / `document:update` |
| GET関連候補 | `/link-candidates?target_type=task&q=...` | `document:read` + 関連先のread |
| GET・POST関連 | `/{id}/links` | `document:read` / `document:update` + 関連先のread |
| DELETE関連 | `/{id}/links/{link_id}` | `document:update` |

viewerは閲覧、member・managerは作成と編集、project_adminは削除まで可能。
最終判断はBackend permissionによる。
別組織・別Project・削除済みProject / 文書のIDを子APIでも受け付けない。

## 本文と履歴

作成は `{title, body}`、更新は `{title, body, version}`。
タイトルは空白のみ不可・200文字まで、Markdown本文は10万文字まで。
作成時は版1、タイトルか本文が変わると新しい版を同じトランザクションで追加する。
同じ内容の保存では版を増やさない。過去版を上書きしない。
409 `VERSION_CONFLICT`の`current`は最新の本文を含むDocumentRead。
削除はversionをqueryで要求し、本文・版・添付への取得を閉じる。
論理削除の保持期間と復元UIは後続課題。

一覧は`page` / `page_size`（最大100）/ `q`（最大200文字）。
title・bodyの部分一致検索で、`%`・`_`をワイルドカード扱いしない。
本文は一覧レスポンスへ含めない。版概要は最新100版まで、指定版APIはそれ以前も取得できる。
版履歴の対象はタイトルと本文。添付・関連付けは現在状態。

## 添付

20件 / 文書、20MiB / 件。デモでは既存の5MiB / 件、合計20MiB、10予約の制限も適用。
許可形式はPDF、PNG、JPEG、WebP、UTF-8 TXT / Markdown / CSV / JSON。
拡張子・MIME・署名またはUTF-8の検証を確定前に実施する。
Office・HTML・SVG・実行形式は対象外。検証はマルウェア検査を代替しない。
CSV / Markdownは`text/plain`で送信する。ファイル名の制御文字は拒否する。

既存`FILE_UPLOAD_MODE=server|presigned`を使用し、Vercelでは本文を直接S3へ送る。
計画は既存FileUploadRequest、確定は`{upload_token}`。
tokenは本人・文書・期限に結び付き、確定はUUID単位で再送可能。
一時保存から検証後の別keyへ確定し、同じPUT URLで確定ファイルを上書きできない。
添付一覧へS3 keyや署名URLは含めない。
download APIは認可後に`{url}`を返し、attachment disposition / octet-streamを署名する。
発行済みURLは設定された有効期限まで使用可能。デモは残り時間と60秒の短い方が上限。

## 関連付けと監査

`{target_type: requirement|task|test_design, target_id}`、同一Projectのみ、最大50件。
要件・タスク・テスト設計書のread権限を追加検証する。
削除済みまたは閲覧不可の関連先はtitleと要件定義書IDをnullにし、内容を漏らさない。
関連先のtitleは登録時に複製せず、取得時に現在値を参照する。
作成・更新・削除・添付・関連の重要操作を`document.*`として既存監査へ記録する。
本文・token・署名URLを監査metadataへ入れない。

## Migrationとデモ

`48bb3c9773b3`は文書・版・添付・関連の4表とindexのみを追加する。
既存データの変更や移動はない。tenant scopeとデモのFK順回収へ4表を登録する。
デモ開始時に作業ガイドと初版を用意し、終了時は追加・削除済みも含めて回収する。
クラウドへのmigration適用とデプロイは別途リリース作業で行う。
