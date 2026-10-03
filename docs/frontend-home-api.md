# 個人HOME API

## 調査と設計

タスクは `Task.assignee_id`、状態、`start_date` / `due_date` / 実績日、優先度を持つ。日付は時刻のないDate、作成日時等はタイムゾーン付きdatetime。既存の期限超過判定は `done` と `cancelled` を除外し、完了件数は `done` のみ。案件とユーザーの所属は論理削除可能な `ProjectMember`、閲覧可否は既存のsystem/project RBACで判定する。

従来のHOMEは通知サマリーだけ。タスク一覧APIは案件単位、案件概要も案件単位の集約APIであり、HOMEのために全件取得や案件ごとの多数の呼び出しを行う構成は避ける。読み取り専用のHomeService / HomeRepositoryと2 APIを追加する。通知は既存Notification APIをそのまま利用し、このAPIには含めない。

既存業務モデル・ルートは維持し、DB変更やmigrationは追加しない。Taskの担当者・案件・期限、ProjectMemberのユーザー・案件には既存Indexがある。集計はDBで行い、案件サマリーは先に表示対象の案件を絞ってから一括集計する。案件ごとのAPI/SQLは呼ばない。

## 共通契約

Cookie認証の本人向けGET。宛先や担当ユーザーIDを受け取らない。管理者でもHOMEは有効な所属がある案件だけ。案件の閲覧権限を必須とし、作業とタスク集計にはタスク閲覧権限も必要。

- `timezone`: IANA timezone、既定 `Asia/Tokyo`。フロントはブラウザのtimezoneを渡す。不正な値は422。
- `limit`: 1〜10、既定8。返却行数のみを制限し、総件数や集計を切り詰めない。
- `today`: サーバーのUTC現在時刻を指定timezoneに変換した暦日。同じ日付を表示と期限判定に使用する。Date値はUTCの日時としてブラウザローカルへ変換しない。

従来の案件内タスク画面のtimezone仕様は変更しない。HOMEではブラウザとUTCサーバーの「今日」の不一致を防ぐ契約を明示する。

## GET /home/tasks

返却: `{today, timezone, summary: {total, overdue, due_today, due_soon}, items}`。

対象は本人担当・未削除・有効な参加案件・閲覧可能な未完了タスク。未完了は状態が `done` / `cancelled` 以外。開始日が未来のタスク、期限なし、backlogも未完了として含める。Taskモデルの全種別を扱い、不具合種別も含める。

優先順位は期限超過、今日まで、明日から7日以内、その他。区分内では期限昇順（NULLは最後）、同一期限内はcritical / high / medium / low、最後にID昇順。客観的な日付と保存済み優先度を使う。

各行は `id, project_id, project_name, project_code, task_code, title, status, priority, start_date, due_date`。業務状態や通知既読を変更しない。

## GET /home/projects

返却: `{today, timezone, total, items}`。未削除の参加案件をID順、上限付きで返す。案件のstatusは既存値を維持し、所属が有効なら完了案件も参加案件に含める。

各行は `id, project_code, name, tasks`。`tasks` はタスク閲覧権限がなければNULL。閲覧可能なら次を返す。

- `my_open_count`: 本人担当の未完了件数。
- `overdue_count`: 案件全体の未完了かつ期限がtodayより前の件数。
- `done_count`: 案件全体の状態doneの件数。
- `total_count`: 案件全体の未削除Task件数。中止も全体に含む。
- `next_due_date`: 案件全体の未完了タスクで、today以降の最小期限。期限超過・完了・中止・削除済みは含めない。

タスクがない案件は0件・期限NULLを返す。ここでの「次の期限」はTaskのみで、要件やテスト等の期限は統合しない。

## GET /projects の追加条件

`member_only=true` で管理者も参加案件だけに絞る。一般ユーザーは所属に加えてproject:readを確認する。省略時の既存動作は維持し、system権限があれば管理用の全案件一覧、なければ従来の所属一覧となる。日常用 `/projects/joined` はtrue、管理用は省略する。

## フロントと拡張

Homeの作業・案件・既存通知を独立したReact Queryで並列取得し、各セクションにloading/error/emptyを設ける。クエリキーを本人IDで分離し、60秒polling・ウィンドウ復帰時のrefetchを使用する。タスク・案件・所属変更後にHOMEキャッシュを無効化する。BFFに `/home` を追加し、Orvalでクライアントを生成する。

専用マイタスクは未実装。HOMEは既存タスク詳細へ直接移動し、案件表の自分のタスク件数から `tasks?assignee=me` へ進める。既存一覧は本人担当で絞るが、既存の状態フィルタはすべてのまま。専用画面を追加するときは同じ所属・権限・未完了判定を再利用し、ページングとフィルタを加える。Sidebarへ空の導線は追加しない。

レビュー依頼や期限情報を増やす場合は、既存業務モデルに明確な宛先と状態がある情報だけを追加する。通知の既読を業務完了と扱わず、通知の生成ポリシーやデータをHOMEへ複製しない。

## 検証

`tests/routers/test_home.py` で本人・所属・閲覧権限・管理者の所属制限・完了除外・期限区分・安定順序・件数上限・案件集計・所属解除・削除・不正timezone・UTC境界・DSTを検証する。350タスクと22参加案件でも返却件数を制限し、1案件と10案件でSQL実行数が増えないことを確認する。

フロントの `home-display.test.mjs` はDateのtimezone変換ずれ・年跨ぎ・既存詳細URL、`navigation.test.mjs` は管理導線のrole制御とパンくず・既存ルートを検証する。
