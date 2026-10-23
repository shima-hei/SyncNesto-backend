# 固定要件の確認表 V1a

既存の要件・固定承認・テスト実行を変更せず、確認の準備を保存する。
`REQUIREMENT_VERIFICATION_PREPARATION_ENABLED` は既定false。公開/MCPでは有効化しない。

## API

`/projects/{project_id}/requirement-verification-plans` を基点とする4 URL/6操作。

| 操作 | 契約 |
| --- | --- |
| GET 基点 | requirement_id任意、page_size既定20/最大50、cursor。enabled/state/can_prepare/items/next_cursor |
| POST 基点 | source_requirement_id/release_id/target_node_id/expected_release_digest/request_id。新規201、同一再送200 |
| GET `/{plan_id}` | 選んだ固定要件/詳細、現在の確認表、現在本文とのsame/changed/unavailable、can_prepare |
| PUT `/{plan_id}` | version/operation_id/checklist/confirm_coverage。追記保存。同一操作は保存した版を返す |
| GET `/{plan_id}/revisions` | 不変の保存履歴。page_size既定20/最大50、版に結び付いたcursor |
| GET `/{plan_id}/execution-candidates` | row_id任意。省略は有効な関連項目の選択肢だけ。page_size既定20/最大50。直近実行を限定取得 |

候補の`browse_patterns=true`は、その保存済み行が参照する項目のパターンを選択用に表示する。
`selected=false`は確認対象に含まない。未選択を合格/対象外へ読み替えない。
cursorはProject/確認表版/行/参照設計版/選択モードへ結び付け、変更時は400で再取得を求める。
候補の`comparison`は現在のテスト内容と実行時内容の比較で、要件自体の合否ではない。
`requirement_version_recorded=false`を常に明示し、旧実行の採用・実行作成・証跡保全はしない。

## 入力・原子性

空の確認表から開始できる。最大100行、256KiB、対象参照計1000件。
引用位置はUnicode code point。全文/本文引用/受け入れ条件引用、test/manual/not_applicableを使う。
不完全な下書きは保存可。対応の明示確認は、0行/未設定の方法/未入力の確認内容/不正引用を拒否。
coverageの対象digest/行digest/本人/日時はサーバーが保存する。編集後は自動継承しない。

封印nodeを再利用し、対象の再割当と既存revisionのUPDATE/DELETEをDBで禁止する。
行lock/版確認/追記/ヘッダー更新/監査を同じREPEATABLE READ transactionで保存。
40001/40P01と冪等操作の一意競合は最大3回。別入力を自動mergeしない。
初期ヘッダーは遅延FKの参照先を次のINSERTで作るため、認可済みCore INSERTを使用する。
既存のtenant ORM検証を緩めず、新2表の所有経路とDB guardで所属・対象を確認する。

全応答はprivate,no-store/no-referrer。DBは新2表のみ、既存列は変更しない。
App DB roleには新表のSELECT/INSERTとヘッダーの版/更新者/更新日時UPDATEだけを許可する。
固定本文/確認表は元の要件・テストの回収に巻き込まれない。

## 状態とエラー

一覧はdisabled/restricted/readyを分離し、権限不足時にID/名称/件数/本文を返さない。
詳細/操作の無効は404、権限外403、他Projectのplan404、入力構造422、引用/参照不正400、容量413。
版違い409 VERSION_CONFLICTはcurrentのversion/checklist_digestだけ。同一操作ID・別入力409。
競合時はGETで最新版を取得し、自分の入力との比較を明示してから次の保存へ進む。

固定対象は1MiB/詳細500件、比較素材は8MiB/関連セル10000、候補応答512KiBまで。
関連セルは素材行に項目の固定列/拡張列の数を加えた保守的な予算。超過・不正な旧sourceは
not_evaluatedとして示し、実行0件やOKへ変換しない。

隔離の代表契約はtests/routers/test_requirement_verifications.pyとtest_verification_candidates.py。
性能測定は10→50ケースで業務SQL7/全SQL28が一定（約0.21秒、1環境の測定）。
保持を優先し、戻す際はflag OFF。履歴を消すdowngradeや既存承認の変換は行わない。
