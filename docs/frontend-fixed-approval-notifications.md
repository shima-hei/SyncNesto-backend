# 固定承認のアプリ内通知・次操作（Phase 2B-3a）

新規5URL/5操作。DB構造/permission/role/依存の追加なし。
`FIXED_APPROVAL_NOTIFICATIONS_ENABLED` / `NEXT_PUBLIC_FIXED_APPROVAL_NOTIFICATIONS_ENABLED` は既定false。

## 正本と宛先

固定request/event/bindingが正本。通知はイベントと同一transactionで追記し、失敗時は判断/監査もrollbackする。
`fixed_approval:{event UUID}` と受信者の既存一意制約で重複を拒否。自己通知・過去分の遡及生成なし。

- requested → 指定本人（社内/外部）。
- approved/例外承認/rejected → 申請者。
- cancelled → 申請者・指定本人のうち取消した本人を除く。失効した外部へは通知しない。

既存通知4操作は新type3/target1を追加する。新固定承認分だけ、全source read/保全read・所属をcount/page前に検証する。旧assigned/mentionedの仕様は維持。
通知の既読は承認でもタスク完了でもない。理由は通知snapshotへコピーせず、正本の承認詳細で確認する。

## 共有API

組織選択を要求せず、現在のsession・実MFA・公開/参加/許可の同じ世代・自然期限を検査する。
一覧・未読数・個別/全件既読の全経路で同じ認可集合を使用。失効は行/件数/既読からも除外し、個別は404。
公開停止/再許可だけで旧binding通知は復活しない。更新は既存CSRF/Origin必須。エラーもno-store/no-referrer。

| 操作 | 挙動 |
| --- | --- |
| GET `/shared/notifications` | page/page_size（20、最大100）/unread_only。現在許可された集合のitems/total/page/page_size |
| GET `/shared/notifications/unread-count` | count |
| POST `/shared/notifications/read-all` | 現在読める分のupdated_count。再送0 |
| POST `/shared/notifications/{event_id}/read` | 本人のevent UUIDを指定、最初のread_at不変。現在投影した通知を返す |

公開DTOにはopaqueなevent/approval/publication UUID、公開対象名・申請者名、eventと現在status/can_decide、時刻、資格期限/revisionだけを含む。
内部notification連番/tenant/project/user/grant ID・内部manifest・本文/理由・署名DL URLを返さない。
MFA_REQUIREDは画面内で既存のpassword→TOTP/Passkeyへ接続する。既存APIの再loginを変更しない。

## 社内の次操作

GET `/projects/{project_id}/fixed-approvals/{approval_id}/next-actions`。
固定本文と同じreadに加え、actionごとの現在のread/update/create/publication権限と元の親/Projectを検査する。
返すのは現在status、次actor区分/名前/is_you、固定enumのactions（元確認/修正、固定版入口、公開管理入口）。
生URLは返さず、Frontendの既存ルートresolverを使用。元削除/失権時は編集リンクを返さない。
依頼中でも外部資格が失効していれば次の対応者は申請者。取消後に新しい依頼を作る。

## 停止とrollback

flag=falseで新通知生成を停止し、新5APIは404、新UIは非表示。既存承認・共有本文・添付・招待は維持。
社内の新通知readerはflag=falseでも型互換を維持する。新enumを持たない旧buildへ無条件に戻すと500になるため、
互換readerを含むrollback artifactを使う。通知/判断履歴の削除をrollback手順にしない。
メール配送・新permission・実顧客送信・PRマージ/本番反映はこの変更に含まない。
