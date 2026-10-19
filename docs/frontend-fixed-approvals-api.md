# Phase 2A-2 固定内容への内部合意・正式承認

指定本人1名が、保存済みの要件／要件定義書と明示した設計・テスト設計・ready添付構成を判断する。最新版へ承認を引き継がない。顧客公開・通知・代理・自己承認は対象外。既存role/permission割当は追加しない。

## API（全て /projects/{project_id} 配下）

正確な型・operationIdはOpenAPIを正本とし、FrontendのOrvalを生成する。

一覧itemsの `requester_name` / `approver_name` はnullableの現在表示名。閲覧可能な返却ページに登場する申請者・承認者だけを一括取得し、削除済みはnull。メールや他組織の所属を返さず、当時の氏名の証明として扱わない。保存済み原記録・判断者ID・取込証拠は変更しない。

| Method | Path | 内容 |
| --- | --- | --- |
| POST | /fixed-approvals/preview | 固定構成・本人・根拠と例外を確認。副作用なし |
| POST | /fixed-approvals | 確認digest付き申請。201／同一再送200 |
| GET | /fixed-approvals | 新旧履歴。page/page_size、target_kind/source_id/status/mine/scheme |
| GET | /fixed-approvals/basis | release_id＋target_node_id、またはapproval_id。署名cursor/page_size |
| GET | /fixed-approvals/approver-candidates | release_id、任意preservation_id/q/page/page_size |
| GET | /fixed-approvals/{approval_id} | 新方式UUID、固定manifest・最大2イベント・操作可否 |
| POST | /fixed-approvals/{approval_id}/decision-preview | 対象と現在の全本文比較。副作用なし |
| POST | /fixed-approvals/{approval_id}/decisions | 指定本人のapprove/reject |
| POST | /fixed-approvals/{approval_id}/cancel | 未判断のみ理由付き取消 |
| POST | /approval-migrations/preview | 旧記録のarchive/switch確認。副作用なし |
| POST | /approval-migrations | archive、または旧pendingの原子的切替 |
| GET | /approval-migrations/{import_id} | 元対象purge後も取込時点の証拠を署名cursorで参照 |

## 入力・状態

申請は `release_id,target_node_id,approver_id,preservation_id` または明示 `attachments_none:true`、任意 `request_note,legacy_import_id`、要件別 `exceptions:[{node_id,reason}]`。保存時は `request_id,expected_preview_digest` を追加。本文・status・actorは任意入力できない。同じ固定版・対象node・添付構成のpendingは指定先を変えても二重作成できない。

文書の各要件は同じID・version・本文・詳細digestで照合する。親nodeや配置やrelease UUIDは除く。同本文に合意と差し戻しが併存すればconflicting。従来記録は合意済み根拠へ昇格しない。未合意等は全件の例外理由を必要とする。`approved_with_exceptions` でも個別要件の状態は変えない。要件0件は100%合意と換算しない。

判断入力は `version,operation_id,expected_review_digest,decision,comment,confirm_fixed_scope,confirm_changed_or_unknown,confirm_exceptions`。差し戻しcommentと取消reasonは空白不可。変更・比較不能・例外を明示確認する。子要件・章・同梱設計・テストgraphも比較し、親versionだけで一致と判定しない。現在添付bytesとの比較は行わず保全添付を判断する。

状態はrequested→approved／approved_with_exceptions／rejected／cancelled。新しいRR transactionで現在権限を確認し、row lock＋versionで終イベントを一つだけ保存する。状態・イベント・根拠・監査は同一commit。同一operation IDの同一入力だけを冪等再送とし、異入力や二重判断は409。業務判断履歴と監査は別に保持する。

## 従来履歴

archiveは旧行・対応ログ・元ID・日時・実操作者・不明点を変更せず保存。同一project/旧ID/source_digest/modeは一件。switchは対応要件／文書pendingだけをロックし、新pending・取込・ガードを一括保存する。切替済み旧行は旧APIと旧buildからのUPDATEをDBで拒否し、旧APIは409 APPROVAL_MIGRATED。未切替の旧操作は維持する。未判断filterでは切替元を二重作業として数えず、全履歴では元状態を保持する。

旧terminal（approved/rejected）のarchiveから今回の内容を再確認する場合、通常申請へ `legacy_import_id` を指定。同じ元種別・元IDを検証し、現在日時の新pendingにする。過去の承認を固定版へ変換せず、今回の本人判断完了まで未承認。同じ履歴を複数の将来版へ関連付けられる。旧pendingのarchiveを指定してswitchガードを迂回することはできない。

旧schemaには完全な本文・添付snapshotがないため現状はhistory_only／subject_verified:false。指定先を実操作者に代用せず、対応ログなし／重複／actor不明／不一致／自己／対象種別外を明示。証明済み旧判断の直接確定方式は未実装。元対象purge後も取込記録を保持する。

## 認可・容量・エラー

Cookie/session/MFA/CSRF/tenant所属に加えinternal・非demo・非MCP。申請・取消・移行はartifact_release:create＋requirement:review、閲覧はartifact_release:read＋requirement:read、判断はさらにrequirement:approve＋actor==approver!=requester。全同梱source read、添付ありならdocument:readも確認。標準managerは申請まで、project_adminが本人判断。system/tenant管理権限だけでは迂回不可。候補は有効所属・internal・必要permissionの積集合でemailを返さない。存在・件数・根拠・再送にも現在権限を適用する。

各page最大100、入力最大1MiB、例外最大1,000件・理由2,000文字。旧証拠は各レコード最大1MiB・全体32MiB。GETは1MiB以内で分割。根拠は集合digest付きcursorでページ間変更なら409。切捨てない。

既存401/403/404/422/413と409 VERSION_CONFLICT／DUPLICATE_RESOURCE／APPROVAL_SCOPE_CONFLICT／APPROVAL_MIGRATED。競合currentはversion/status等metadataのみ。入力理由を維持して再確認する。

## 移行・停止

Alembic c9e3f6a1d547（親b8d2f5a0c436）が4表・index・完全性/不変/切替guardを追加。旧業務行・permission割当は不変。既存旧承認は自動取込しない。

停止buildは新preview/申請/判断/取消/移行を止め、reader・4表・migration・旧UPDATEガードを保持。履歴ありdowngradeは拒否。旧Git commitへ単純に戻して二重判断を再開したり新履歴を消したりしない。空の隔離DBだけでdowngrade/reupgradeを検証する。

UIは「何を満たすか」内「合意・承認」。既存承認欄・固定版から入れる。既存編集、ボード、ガント、テスト設計Editorは置換しない。
