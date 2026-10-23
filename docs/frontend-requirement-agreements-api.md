# 現在の要件内容への合意（A3b-2a）

`GET /projects/{project_id}/requirement-agreements?requirement_ids=1&requirement_ids=2`

表示対象の正整数IDを1〜100件、繰り返しqueryで渡す。順序/重複を正規化し、同じProjectの有効な要件/文書/章だけを対象にする。対象外IDが混在した場合は全体404。新しい保存/判断操作はない。

有効な社内tenant/Project所属とproject:read/requirement:readを要求する。MCP/guestは不可、demoはdisabled。PROJECT_TRACEABILITY_ENABLEDと新PROJECT_REQUIREMENT_AGREEMENTS_ENABLEDの両方が必要（後者は初期false）。artifact_release:read/document:read/test_plan:readのいずれかが欠ければ、存在を0件へ補完せず全体restricted、items=[]。新permission/role付与はない。

応答はenabled/state（disabled/restricted/ready）/evaluated_at/items。行はrequirement_id/version/internal/externalだけ。各結果はstate/reasonと代表approval_id/event_id。本文・氏名・メール・添付・全履歴は含めない。

| state | 表示の意味 |
| --- | --- |
| agreed | 同じ対象内容・版への指定本人の合意あり。全員合意ではない |
| conflicting | 同じ内容へのapproved/rejectedが両方ある |
| rejected / pending / cancelled | 同内容の差し戻し／依頼中／取消のみ |
| changed | 異なる内容・版への依頼だけがある |
| legacy_unverified | 対象内容を証明できない従来履歴だけがある（社内） |
| unagreed | 必要なreadが揃い、合意記録がない |
| not_evaluated | サイズ上限・同時変更・整合しない判断証跡 |

優先順位は相反→合意→差し戻し→確認待ち→同内容取消→別内容→従来→記録なし。外部bindingの有無で社内/外部を分け、文書の例外承認から個別要件の合意を作らない。外部失効後も社内の適格な本人が過去の判断を参照できるが、外部への再公開/依頼再開はしない。

既存req-subject-v1で要件本文と詳細を比較する。詳細にはversionがないため親versionだけでは判断しない。同一statementで容量を確認してから条件付き投影し、再読取で詳細の追加/削除/変更も照合する。1対象1MiB/詳細500、1batch8MiBを上限とし、切り詰めた本文で合意を認定しない。DBのJSON表現と安全余白による保守的なサイズ判定を行う。

Backendはno-store、BFFは成功/失敗ともprivate,no-storeとno-referrer。Queryはtenant/本人/Project/ID集合で分離。復帰・手動更新で再取得し、取得中/失敗/対象版不一致では古い合意表示を使わない。Orvalの配列queryを使い、要件ごとのHTTPは発行しない。既存の追跡GET/承認判定/固定本文を変更しない。

戻す場合は新flagをfalseにしてAPIを再起動する。保存行や権限を変更していないためDB downgradeは不要。
