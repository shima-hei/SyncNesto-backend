# 外部参加者による固定内容への判断（Phase 2B-2）

`GUEST_FIXED_APPROVALS_ENABLED=true` の環境だけで新申請・新判断を許可する。停止しても現在の公開資格での本人履歴readと内部取消を保持する。新permission/role割当はない。既存の社内専用申請は要件/文書のまま。

独立対象はrequirement（個別合意）、requirement_document（正式承認）、specification（画面/API設計）、test_design（全項目・因子水準・マトリクス・書式）。設計承認は可変statusや実行/合否を更新しない。本文・添付は申請時の不変な公開版へ固定する。

## API

`P=/projects/{project_id}/guest-fixed-approvals`、`S=/shared/publications`。

| Method | path | 意味 |
| --- | --- | --- |
| GET | P/approver-candidates | 同じ公開版の有効な共有先1名を選択。publication_id/target_node_id/q/page/page_size |
| POST | P/basis-preview | 公開node IDへ写した外部合意根拠/例外要約。全体hashと署名cursor |
| POST | P/preview | publication_id/target_node_id/grant_id、外部公開するrequest_noteとexceptions。相手向け確認要約 |
| POST | P | 上記＋request_id/expected_preview_digest/confirm_public_scope。201/冪等200。固定承認の内部Readを返す |
| GET | S/approval-requests | 現在見られる自分の依頼だけ。q/status/publication_id/cursor/page_size |
| GET | S/{publication_id}/approval-requests/{approval_id} | 指定本人の公開依頼DTO |
| GET | S/{publication_id}/approval-requests/{approval_id}/bases | 申請時に固定した要件別根拠/例外の要約 |
| POST | S/{publication_id}/approval-requests/{approval_id}/decision-preview | 現在MFA/本人/資格/本文比較を束縛した最長5分のreview_proof |
| POST | S/{publication_id}/approval-requests/{approval_id}/decisions | version/operation_id/review_proof/decision/comment/確認3種。本人の終判断を一度だけ保存 |

内部の既存GET fixed-approvalsはaudience/publication_id filterとaudience表示を追加、GET詳細はguest_contextを追加する。既存POST decision-preview/decisionsは外部依頼を拒否。既存POST cancelは外部依頼だけRCの公開境界へ分岐する。公開停止後も必要な内部権限を持つ担当者が理由付き取消可能。

## 認可・UX

- 新内部操作はartifact_release:read/create、requirement:read/review、artifact_publication:read/publish、全同梱sourceのread。設計単体はdocument:update、テスト設計単体はtest_plan:updateも必須。添付があればdocument:read。候補は既存の有効な参加世代/許可から選び、Userや所属・roleを作らない。
- 共有5操作は本人/session/現在MFA/現在の公開・参加世代・許可・期限を毎回検証。既存のglobal MFA_REQUIRED=falseでも新判断は実MFA必須。再認証は既存POST /auth/mfa/reauth→/mfaを使い、reauth flowではrecovery codeを案内しない。既存read-only共有の認可は不変。
- 通常文書根拠は同Projectの同一要件本文への外部合意だけ。社内合意のみ/未合意/差戻し/競合/異版は理由付き例外を全件指定する。他人の判断ID/氏名/日時/コメントは共有DTOに含めない。例外文書承認で個別要件の状態を更新しない。
- 選択した設計の独立承認と、要件/文書に同梱した設計の確認を区別する。テスト設計の独立承認をテスト実行・合格と呼ばない。UIでは承認者を氏名/あなたで指す。
- 失効/公開停止後は履歴も含めて外部の新規取得不可。再参加・再許可で旧依頼を復活させず、内部で取消→新しい許可への再依頼。過去の判断事実は内部保持する。
- 同じoperation_idの同じ入力は現在資格を再検証して同じ結果。別入力/二重判断は409。確認後に本文/権限/状態が変わったら再確認する。公開DTOへ未公開の現在本文を返さない。
- 新APIの422は入力値/ctxを含めない。別本人/別公開版/失効は404、MFA/権限/CSRFは403、競合は409。no-store/no-referrer。proof・本文・理由を永続下書きやURLへ保存しない。

公開本文・添付は既存shared API/DLを使う。新規通知/メール送信や旧承認の自動移行はない。OpenAPIからOrvalを生成し、内部Read型を外部へ流用しない。
