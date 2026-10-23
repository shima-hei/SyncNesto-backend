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

## V1b: 証拠の提出と保全

`REQUIREMENT_VERIFICATION_EVIDENCE_ENABLED` は既定false。V1aの有効化も必要。
保存済みの確認表版へ証拠を結び付け、最終合否は決めない。`ready` は保全完了だけを示す。

基点は `/{plan_id}/evidence-sets`（上記Project/確認表prefixの配下）。

| 操作 | 契約 |
| --- | --- |
| GET 基点 | `plan_version`任意、cursor、page_size最大50。固定本文を含まない提出一覧 |
| POST `/preview` | 保存版・行・実行/手動/対象外候補・選択ファイルを検証し、固定内容とdigestを返す |
| POST 基点 | preview時の内容・digest・request_idを送信。新規201/同一再送200 |
| GET `/{set_id}` | 保全状態、現在内容との比較、ファイルの保全状態。本文詳細は別取得 |
| GET `/{set_id}/entries/{entry_id}` | 指定した1件の固定根拠と現在の比較。ほかの根拠本文は返さない |
| POST `/{set_id}/advance` | versionを指定し、添付を1つ進める。自動pollingしない |
| POST `/{set_id}/cancel` | 進行中の提出を取消。固定記録と試行台帳は残す |
| POST `/{set_id}/cleanup` | 未採用コピーだけ再走査・回収。採用済みVersionIdを削除しない |
| POST `/{set_id}/files/{file_id}/download` | 最新の本人認可・保全hashを照合して最大60秒の署名URLを返す |

入力は1〜200件、添付20個/各20MiB/合計100MiB。行の確認方法と証拠種別を一致させる。
実行は選択ケースの直近実行のみ。既存実行に要件版は記録されていないため、現在の固定要件と
照合した本人・理由・日時を別に保存する。変更済み内容は明示した参考記録としてだけ提出できる。
テスト添付は当該実行のTestEvidence、手動/対象外の添付は同Projectの文書添付から明示選択する。
手動確認の実際の内容・結果・理由・確認日時、対象外候補の理由を必須にする。新規アップロードや
代理提出はない。次の確認表版へ自動継承しない。

新permission `requirement_verification:submit` はproject_admin/manager/memberへ付与。
有効tenant/社内Project所属・project/requirement/test_plan/test_case/requirement_verificationのreadが必要。
文書添付を含む提出は詳細の一部取得でもdocument:readが必要。readのみでは提出不可。
自身の提出を操作でき、prepareとsubmitの両方を持つ担当者は他者の提出も操作できる。
当初の提出者と実際の保全/回収操作者は区別して記録する。guest/demo/MCP/運営権限だけの非参加者は不可。

コピーは既存SeaweedFSの専用private bucketで行い、元keyや署名URLを本文・監査へ保存しない。
90秒leaseで試行を排他し、IO中のDB lockを避け、完了前に版・本人資格・元記録を再確認する。
VersionIdとSHA256を保全し、元Storageへのfallbackはしない。DB lock待ちは1秒で409、
serialization/deadlockだけ最大3回。失効・削除・取消・期限切れの後着コピーは採用しない。
回収失敗は`safe_error=cleanup_retry_required`、未確認試行数は`comparison.cleanup_pending`。
これは採用済みファイルの保全状態と別であり、`ready`を不合格に書き換えない。

同一request_idの別内容/版・preview以降の変更は409。無効404、権限外403、入力422/400、容量413。
全応答はprivate/no-store/no-referrer。一覧は本文を遅延読込せず、件数に伴うSQL増加を避ける。
Storage障害で元URLを代用せず、同じ操作の再確認/再試行から続ける。
復旧はflag OFFを基本とし、履歴のあるDBをdowngradeしない。保全の復元にはDBだけでなく、
VersionIdを保持する停止済みSeaweedFSデータ領域の整合したバックアップが必要。
