# ユーザー通知

## 既存構造の調査と責務

- `User` は有効状態と論理削除を持つ。`ProjectMember` とrole/permissionが案件所属・閲覧可否を決める。
- 担当割当は `Task.assignee_id`、`Requirement.owner_id`、`RequirementOpenIssue.assignee_id` に存在する。テスト設計・テストケースは作成者・実行者を持つが担当者割当ではないため、今回の割当通知には含めない。
- コメントの宛先は4種類共通の `comment_mentions` 関連。本文のUTF-16出現情報とは別に、コメントとユーザーの関係が一意になる。
- 変更履歴は各業務Serviceから `TaskChangeLog`、`RequirementChangeLog`、`TestDesignCommentChange` 等へ保存される。案件のActivityはこれらや実行履歴を集約する表示であり、汎用イベント配信基盤ではない。
- 認証は既存のCookie JWT・サーバーセッション、更新はCookieとヘッダーによるCSRF検証。リアルタイム通信は存在しない。
- フロントは共通Header、TanStack Query、Orval、Next.js BFF。HOMEは従来プレースホルダー、案件概要には履歴・状況の集約表示がある。

業務Serviceで差分を確定し、履歴と通知へそれぞれ渡す。履歴の全件を通知に変換しない。既読状態はタスク完了等の業務状態を変更しない。

## データモデル

`notifications` に以下を保存する。

| カラム | 意味 |
| --- | --- |
| id | 通知ID |
| recipient_user_id | 宛先本人 |
| type | `NotificationType`。今回 `assigned` / `mentioned` |
| actor_user_id | 操作者。物理削除時はNULL |
| project_id | 案件。物理削除時はNULL |
| target_type / target_id | 種類と安定ID。URLは保存しない |
| snapshot (JSONB) | 発生時のactor_name、project_name、target_title、excerpt |
| context (JSONB) | document_id、requirement_id、task_id、design_id、subject_type/subject_id |
| event_key | 対象種別・ID・バージョン・通知種別 |
| read_at | 未読はNULL、既読は最初の既読日時 |
| created_at | タイムゾーン付き作成日時 |

APIの `is_read` は `read_at` から導出し、状態を二重保存しない。通知の宛先ユーザー物理削除時だけ通知もCASCADEで削除される。業務対象には多態的なIDを保存し、業務対象やコメントの削除では過去通知を消さない。

宛先＋作成日時＋ID、宛先＋既読日時＋作成日時＋ID、宛先＋案件＋作成日時＋IDの複合Indexを用意する。

完全な日本語文章は保存せず、型とスナップショットからフロントで表示する。名前・対象名の変更後も発生時の文脈を保持し、多言語化時は表示テンプレートだけを追加できる。本文抜粋は240文字までで、HTMLとして描画しない。

## 通知生成

`app/services/notification.py` の `NotificationService` が共通ポリシーを担う。

- `assignment_changed`: タスク、要件オーナー、未決事項の作成・更新、未決事項から要件への昇格時の新規割当。A→A、解除、自己割当、別フィールドのみの変更は除外。
- `mentions_changed`: 保存された `mention_targets` のユーザーID集合から以前の集合を引く。自己メンション・同一ユーザーの複数出現は除外。編集時は追加された宛先だけ通知し、削除時は過去通知を残す。削除後の再追加は別の編集イベントとして通知する。従来の要件コメントには編集APIがないため作成だけが対象。
- `emit`: 有効な案件所属・対象閲覧permissionを確認し、スナップショットと共に保存する。

通知Repositoryはcommitしない。対象の作成/更新をflushした後、通知を生成して既存のService/履歴保存のcommitへ参加させる。通知保存が失敗した場合は、その業務変更もcommitされない。更新時は対象行をロックしてから既存version検証を行う。

`(event_key, recipient_user_id)` の一意制約とPostgreSQL `ON CONFLICT DO NOTHING` により、同じイベントを再評価しても1通知となる。別バージョンの再割当や再メンションは別通知になる。過去の履歴からの遡及生成は行わない。

## API

すべて `get_current_user` で認証し、宛先IDをリクエストから受け取らない。system_adminも他人の通知は読めない。更新は通常のCSRF検証を通る。

| Method | Path | 用途 |
| --- | --- | --- |
| GET | /notifications | 本人の通知一覧 |
| GET | /notifications/unread-count | 本人の未読件数 |
| POST | /notifications/{notification_id}/read | 個別既読。繰り返してもread_atを維持 |
| POST | /notifications/read-all | 全件または指定案件の既読化 |

一覧パラメーターは `page` (1以上)、`page_size` (1〜100、既定20)、`unread_only` (既定false)、`project_id` (任意)。既存一覧APIに合わせたページ番号方式で、`created_at DESC, id DESC` の安定した順序。レスポンスは `{items, total, page, page_size}`。

件数と一括既読も任意の `project_id` で絞れる。他人または存在しない通知の個別既読は `404 NOT_FOUND`。一括既読の返却値は `{updated_count}`、未読件数は `{count}`。

各通知は `target_status: available | deleted | forbidden` を持つ。対象種別ごとの一括問い合わせで、対象と親案件・定義書・設計書の削除も確認する。メンションはコメント自体とコメント対象の削除も確認する。対象permissionは一覧内の案件・permissionごとに確認し、通知の存在をアクセス許可として使わない。遷移先APIも既存の認可を必ず実施する。

## フロントエンド

Orval生成クライアントを既存BFF `/api/notifications` 経由で利用する。通知クエリのキーにログインユーザーIDを含め、アカウントを切り替えた場合に他の宛先のキャッシュを表示しない。既読化後は通知一覧・全案件/案件別未読件数をまとめて無効化する。

- Header: 全案件の未読件数、Popoverは直近8件。0件時は数字を表示しない。Popoverは高さを制限しスクロールする。
- `/notifications`: 20件ずつの一覧、すべて/未読、日別グループ、ページング、一括既読。`?project=ID` で案件別表示。
- HOME: `NotificationSummary` で直近5件。
- 案件概要: 同じ `NotificationSummary` に `projectId` を渡す。
- 共通の `NotificationItem` / `NotificationList`: dot、文字の太さ、控えめな背景で未読を区別。相対日時と正確な日時のtitle。空状態、取得失敗時の再試行、削除済み/権限なし表示。

クリックは個別既読APIの成功後に遷移する。削除済み/権限なし項目は遷移できず、未読なら「既読にする」だけ実行できる。

遷移は `features/notifications/lib/notification-display.ts` に集約する。タスクは詳細、要件は定義書配下の要件詳細、未決事項は定義書の `?tab=issues`、コメントは親の詳細へ移動する。テスト項目/パターン表は既存 `item` / `tab=patterns&table` を使用する。コメントIDによる直接フォーカスは既存画面にないため、コメント位置への自動スクロールは追加していない。

45秒のpolling、画面遷移時の件数再取得、ウィンドウ復帰時のrefetchを使用する。非表示タブではpollingを停止し、Header一覧はPopoverが開いているときだけ取得する。WebSocket/SSEは追加しない。

## 今後の拡張箇所

1. `NotificationType` と必要なら `NotificationTargetType` を追加する。型はPythonのStrEnum、保存は拡張可能な文字列。
2. 関連業務Serviceでイベントと宛先の差分を確定し、共通 `emit` を呼ぶ。元の更新と同一トランザクションにする。
3. 新しい対象には `TARGET_PERMISSIONS` と `NotificationRepository.available_targets` の一括解決を追加する。
4. 必要なスナップショット/コンテキストschemaを更新し、OpenAPIからOrvalを再生成する。
5. フロントの表示テンプレートと遷移resolverを追加する。HeaderやHOMEを別実装する必要はない。
6. リアルタイム化する場合も生成ポリシーを維持し、UIの再取得トリガーだけを置き換える。

## Migrationと検証

Migration: `a83d6e91b402_add_notifications.py`。`uv run alembic upgrade head` で適用する。

`tests/routers/test_notifications.py` は3割当機能と4コメント種類を対象に、差分・重複・自己通知・削除・宛先分離・既読・ページング・改名スナップショット・通知失敗時のrollbackを検証する。既存のメンションRouterテストも継続実行する。

フロントの `notification-display.test.mjs` は全対象の遷移、削除/権限なしのリンク抑止、文言、相対日時の年跨ぎを検証する。

検証結果: バックエンド全体は385件成功、3件は既存の想定失敗。追加した同時割当・更新失敗rollback・案件別既読・CSRFと要件機能の再検証は111件成功。編集APIがない従来要件コメントの編集テスト1件は対象外としてskipする。

フロントの表示・遷移テスト4件、format、typecheck、lint、buildを確認する。実ブラウザでは空状態、31件のページング、Header直近8件、個別/全件既読、タスク詳細遷移、削除済み表示、390px幅、ライト/ダークを確認する。ブラウザ確認用の通知は専用event_keyで作成し、検証後にその通知だけを削除する。
