# 固定要件の最終判断（V2）

LOCAL限定。`REQUIREMENT_VERIFICATION_DECISIONS_ENABLED`と既存の確認表/証拠flagがすべてtrueの場合だけ利用する。既定false。旧正式承認、自己承認禁止、通知、テスト実行の保存契約は変更しない。

## 資格と正本

- 有効な同組織・同Projectの社内所属、現在のsession/MFA、`project:read`・`requirement:read`・`test_plan:read`・`test_case:read`・`requirement_verification:read`を要求する。文書添付を含む構成は`document:read`も必要。
- `requirement_verification:prepare`で依頼作成/判断待ち取消。割当は既存のまま。
- `requirement_verification:decide`をproject_admin/manager/memberへ各付与。指定本人だけ確認/判断/撤回でき、自己指定を認める。運営権限、別人、ゲスト、閲覧専用、MCP、demo、公開環境へ流用しない。
- 指定時の所属ID/版と権限を固定し、変化後は取消＋再依頼。表示名だけの変更は資格変更にしない。
- 固定要件と保存確認表版・明示選択したready entry/manifestだけを根拠とする。本文の正本や実行履歴を書き換えない。

## 12操作

base=`/projects/{project_id}/requirement-verification-requests`

| URL | method | 入力/用途 |
| --- | --- | --- |
| base | GET | requirement_id任意、cursor、page_size 1〜50（既定20） |
| base | POST | previewと同じ選択、operation_id、expected_preview_digest |
| base/preview | POST | plan_id/version、judge_id、entries(set_id/entry_id)、前の依頼/見直す判断、理由 |
| base/candidates | GET | 現在の社内read/decide候補。選択後は添付の分野権限も再検証 |
| base/{id} | GET | 対象・固定条件・根拠・現在照合・操作可能性 |
| base/{id}/review | POST | version/review_version。本人sessionに結び付く確認開始/再開 |
| base/{id}/review/advance | POST | 同上。保全添付1件のVersionId/bytes/hashを検査 |
| base/{id}/decide | POST | version/review_version、operation_id、result、reason、対象外の確認行、未解決行、旧要件差分の確認 |
| base/{id}/cancel | POST | version、operation_id、reason。判断待ちだけ |
| base/{id}/withdraw | POST | 同上。確定本人だけ、元の結果を保持 |
| base/{id}/events | GET | cursor、page_size 1〜50（既定20） |
| /projects/{project_id}/requirement-verdicts | GET | requirement_idsを反復、1〜100件。明示系列の終端と現在への照合 |

Cookie/CSRF・既存エラー形式を維持。全応答（422/認可失敗を含む）はprivate/no-store/no-referrer。別Projectは404、本人/分野権限不足403、入力400/422、容量413、版/二重操作/根拠変更409、ファイル取得不能503。同一operation_id＋同一入力は同じ業務記録へ返す。入力を変えた再送は409。

## 判断と現在への有効性

`requested → decided/cancelled`、`decided → withdrawn`。結果はpassed/failed/not_applicable。2表に固定basisと追記eventを保持し、DBガードで本文不変・遷移・履歴完全性・後継1つ/循環拒否を検証する。source回収や撤回で過去の判断を削除しない。

合格には、空でない確認表、条件対応確認、全行/全対象ケースの対応、少なくとも1つの実際のOK、理由付き対象外の本人確認が必要。未実施/NG/保留/不足/相反/現在と違うテスト/未保全を合格にしない。全条件が対象外なら合格と区別する。不合格は理由と未達/未解決行を記録し、不足を確認済みにしない。

元の要件本文だけが変わっても依頼は固定内容に残る。差分を明示確認したうえで旧内容へ判断できるが、現在への合格には表示しない。確認表・対象構成・実行が判断待ち中に変われば取消＋新依頼。

判定時にケースごとのrun番号/IDと意味的source digestを保存し、以降の同じ対象の全履歴からNG/保留を検査する。最新OKだけで過去NGを消さない。手動の相反記録も同版のまま古いOKを選び直して解決しない。再判定はprevious_request_idと実際に見直すreviewed_decision_idを明示する。取消/撤回後に古い合格へ自動復帰しない。

現在照合のunavailable/not_evaluatedと保存済みのresultは別。上限/欠損/権限不足を0件へ補完しない。概要は添付をダウンロードせず、確認日時とfiles_rechecked=falseを返す。添付のある合格は画面で「合格記録・添付未再照合」と表示する。

## 整合性と上限

新しいREAD COMMITTED transactionで現在の資格・対象・版を再確認。既存更新と同じ親design/要件を短くlockし、待ち1秒→409、40001/40P01だけ最大3回。IO中にDB lockを保持しない。lease90秒、確認結果15分・本人/session/依頼版/basisへ結び付ける。1ファイルずつhash検査し、確定前にVersionIdの存在を再検査して、再認可/対象/実行が同じ場合だけ確定。取消・失効・後着NG・期限切れは採用しない。拒否後は自分のleaseを解放し、失効後は期限に任せる。

S3の確認とDB commitは原子的ではない。証明するのは記録した確認時点のbytesであり、管理操作による後の消失は過去判断を書き換えず取得不能として扱う。

100行/1000対象組、1000選択entry/50set、20file/各20MiB/合計100MiB、入力512KiB/basis2MiB。SQLで選択entryだけ投影し、巨大なset本文全体を一括取得しない。概要はREQ100/1000組/比較素材8MiB/検査履歴5000件、超過は未集計。10→50件でN+1なし・業務SQL20以内を検証する。

## 検証・戻し方

`tests/routers/test_verification_verdicts.py`で自己/代理/role・全ケース・対象外・未確認・旧版差分・NG→OK・明示系列・取消/撤回・session/MFA/期限・IO中取消・保全不一致を検証する。既存正式承認、確認表、証拠の全体回帰を維持する。

通常LOCALは保全/復元検証後に追加migrationを適用する。障害時はflag OFF＋API再開で機能を停止し、判断履歴を削除しない。確認表/判断履歴や独自権限割当があるDBのdowngradeは拒否する。旧依存・運営管理差分・Storage設定を維持し、PRマージ/本番反映は別判断。
