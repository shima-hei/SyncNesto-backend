# 固定内容の公開許可と本人限定閲覧（Phase 2A-3b）

公開準備版の本文・添付構成を変えず、宛先と期限を明示する。公開は個別合意・正式承認とは別操作。
招待・メール・共有先の承認操作・新しいダウンロード経路は含まない。

## 内部API

P=`/projects/{project_id}/artifact-publications`。GETはpublication:read、公開・停止はpublication:publishを要求する（実コードは`artifact_publication:*`）。
本文を返す・許可を追加する際はartifact_release:readと全同梱sourceのreadも必要。
停止操作には本文を返さず、source read喪失後もread/publishがあれば止められる。
有効internal所属、Cookie/session/MFA、更新時のCSRFを既存方式で検証する。
demo/MCP/審査アカウントは利用できない。

| Method | URL | 内容 |
| --- | --- | --- |
| GET | P/recipient-candidates?q=&page=1&page_size=20 | 当該Projectの有効な外部参加資格だけを検索 |
| POST | P/{id}/publish | preparedからpublishedへ。1〜20人を原子的に許可 |
| GET/POST | P/{id}/grants | 許可履歴／公開中の版へ明示追加 |
| POST | P/{id}/withdraw | 全体停止、全grant失効 |
| POST | P/{id}/grants/{grant_id}/revoke | 指定grantだけ失効 |
| GET | P/{id}/events | 追記の業務履歴。監査ログとは別 |
| GET | /projects/{project_id}/guest-participations | 参加台帳。既存project:invite_member／組織管理権限 |
| POST | /projects/{project_id}/guest-participations/{id}/revoke | 世代全体を失効。既存project:remove_member／組織管理権限 |

公開bodyは`request_id(UUID), expected_state(prepared/published), publication_version, public_digest,
confirmed_full_content=true, recipients:[{participation_id,version,expires_at}]`。
日時はタイムゾーンを必須とし、未来・参加期限以内とする。最終確認で相手・実期限・全添付構成を表示する。
停止bodyは`request_id,version,reason(空白不可/最大1000)`。
成功は`PublicationOperationResult`でpublication/grant/participationの現在状態を返す。
同じProject/actor/request_idと同じpayloadは同じ保存結果、異なるpayloadは409 DUPLICATE_RESOURCE。
version競合は409 VERSION_CONFLICT、変更された資格・封印制約は409 CONFLICT。
通信失敗後の同一再送には同request_idとpayloadを保持する。

既存生成・一覧・詳細は`state:prepared|published|withdrawn`と`publication_version`を返す。
封印本文のversionとは別。旧buildの「常に未公開」という表示へ戻さない。

## 共有API

| GET | 内容 |
| --- | --- |
| /shared/publications?q=&kind=&cursor=&page_size=20 | 現在許可された集合だけの検索・total・ページ |
| /shared/publications/{id} | 公開metadata、expires_at、不透明なauthorization_revision |
| /shared/publications/{id}/nodes?cursor=&page_size=50 | 順序付きの全文ページ。1MiB以内、省略なし |
| /shared/publications/{id}/files | 公開ファイル名・種類・容量・hashの一覧 |

選択Tenantを送らない。許可のない名称/件数・内部ID・元URL・actor・他宛先・Storage key・署名URLを返さない。
無効/他本人/停止/期限切れの有効UUIDは認証後同一404。認証・MFAは既存401/403。
cursorは現在本人/session/許可集合へHMACで結び、別sessionや変更後の再利用は409。
本文全ページ・files取得後にmetadataを再取得し、revisionが変わった場合は表示しない。
HTML/BFF/API成功・エラーはprivate,no-store/no-referrer。ページを離れる、別本人、focus、pageshow、
期限、offline、auth通知で本文を隠し、再認可が終わるまで古い内容を見せない。
共有UIは通常の内部ナビゲーション/HOME/検索/通知/編集/下書きを起動しない。
login後の戻り先は既存MCP同意画面に加えて、正確な共有一覧/UUID詳細だけを許可する。

## DB・競合・停止

新3表で参加世代、本人別許可、追記イベントを保持する。複合FKで同一Projectを強制する。
本文封印は維持し、公開状態2列だけを進める。公開停止・世代失効から復活しない。
User/所有Tenant/Project停止、同所有組織への所属、含まれる元資源7種類の削除をDBガードでも検出する。
元の通常編集は公開内容を変えない。復元しても旧許可を復活させない。
回収はStorage削除前に公開停止を確認し、固定本文・採用保全object・公開履歴を回収対象から除く。

資格調整のstatement境界（共有readerはshared、資格writerはexclusive）→Project境界を使う。
新writerは既存管理処理とのFK逆順を避けるためactor/ProjectのKEY SHAREを先行する。
新処理だけREAD COMMITTEDで待機後の新statementを使い、期限はclock_timestamp。
新処理の40001/40P01は同requestで最大3回。既存snapshot/管理処理の分離レベルは変更しない。
これは[PostgreSQLのlock](https://www.postgresql.org/docs/16/explicit-locking.html#ADVISORY-LOCKS)と
[snapshot規則](https://www.postgresql.org/docs/16/transaction-iso.html#XACT-READ-COMMITTED)に基づく実装で、
専用並行試験と既存API回帰を併用する。配送済み・保存済み本文の回収は保証しない。

`publication_access_context.PUBLICATION_ACCESS_ENABLED=False`で新公開/共有取得を停止する。
停止/失効、内部reader、現在状態、DBガード/履歴は残す。証跡ありdowngradeは拒否する。
初期ロール付与はpublish permission1つをproject_admin/managerへ計2割当のみ。
