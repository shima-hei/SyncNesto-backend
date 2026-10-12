# 公開MCP審査の準備と動画台本

2026-10-12更新。非公開下書きの登録と、通常DB内の専用アカウント・fixtureの公開作成は完了。
公開APIで隔離・OAuth・サンプル操作を検証済み。OpenAIの実接続・28ツールのscanは完了したが、ホスト上の8ケース・動画は未完了。
manifestの正常系5件・異常系3件と対応する。既存利用者・実業務・認証秘密を審査資料へ含めない。
公開名は `shime-hei`、窓口は `syncnesto@gmail.com`。掲載される名前は選択した確認済みidentityと照合する。

## 提出前の未決事項

| 項目 | 必要な確認 |
| --- | --- |
| 費用 | 追加請求0が条件。本人確認のVerifiedだけでは登録・審査等の料金を確定できない。購入、有料API、auto recharge、クラウド有料化を行わない。 |
| 公開規約 | support/privacy/termsは未公開。無料ポートフォリオ・通常アカウント招待制・稼働保証なし・通常アカウントも架空の業務サンプル限定を承認済み。保持・実削除等の未決事項を確定し、公開済み実URLだけをlistingへ記載する。 |
| 審査ログイン | 通常DBの独立審査組織・新規専用Identityを作成済み。MFA免除はこのIdentityだけ、有効期限2026-11-11T07:17:01Z。期限・隔離・既存RBAC・OAuthの公開API検証済み。資格情報のOpenAIへの送信は未実施。 |
| ホスト設定 | 正確なcallback・challengeとPR #27の互換修正を公開済み。本人承認後、専用審査Identityの `Plugin review - consented` だけを許可して接続した。管理画面はDomain verified / Configured / Authorized、28ツール検出、最新scanはNo findings。 |
| 公開状態 | scanのdescription・inputSchema・4 annotationは現行catalogと28件すべて一致。previewの `readOnlyHint=false` も確認済み。通常MFAと審査Identity限定免除の公開API/UI検証は完了。ホストの8ケースと動画は別途実施する。 |

OpenAIへの本人確認はVerifiedで完了したため、再実施しない。
課金を伴う操作は行わず、listing・ケース・台本・ZIP静的検証を準備する。

非公開DraftのPlugin ID: `plugin_asdk_app_6acc60ad42d08191aa90fa9404caa5af`。
version/submission ID: `appsub_6acc60ad42fc81919c2ba02210da7423`。
担当者ChristianへのID通知と活動名 `shime-hei` の追加確認は送信済み。審査提出とは区別する。
以下の問い合わせ経緯は専用Identityを承認する前の履歴として残す。

OpenAIへの問い合わせ（2026-10-12、運営者の承認を受けて公式Help Centerへ送信）:

> Syncnestoでは通常アカウント全員にMFAを必須としています。公開審査の専用アカウントについて、
> MFAなしで直ちに利用できることを求める要件と整合させる方法を確認したいです。
> 審査アカウントにもMFAを維持したまま受け入れられる認証・接続の代替手順はありますか。

運営者の指示により、費用の質問は含めていない。AIサポートから対象機能の確認があったため、
ChatGPT/Codex向けの公開MCP審査であること、公式審査要件のURL、OAuth同意による接続方式を補足し、
審査担当者またはサポート担当者への確認を依頼した。
AIによる一般的な回答ではMFAを維持した代替手順を確認できなかったため、人間の担当者への引き継ぎを依頼し、
「サポートスペシャリストにエスカレーションされました」「返信はメールでもお送りします」と表示された。
正式回答は未受領。AIの提案だけでMFA除外や認証方式の変更を決定しない。

本人の認証秘密・TOTP seed・復旧コード・審査用パスワードは問い合わせに含めない。
回答があるまで審査アカウントのMFAを除外したり、要件を満たしたと表明したりしない。

2026-10-12の再開時、上記会話に正式な担当者回答がまだないことを確認した。
公開名は `shime-hei` を維持するとの本人回答を受領し、次の追加質問を本人の承認後に同じ会話へ送信した。

> 個人の本人確認は完了済みです。公開発行者名に活動名のshime-heiを使う正式な手順はありますか。
> 可能なら必要な確認手順を、できない場合はその旨を教えてください。

未確認の活動名を本人確認済みと表明せず、個人の確認済み名を本人の希望に反して公開しない。
再開時の静的検証・公開メタデータの確認結果は[公開申請の進捗](publication-status.ja.md#2026-10-12の再開確認)を参照する。

## 分離したサンプルデータの準備

専用Identity・独立組織・3 Project（許可済み・同意対象外・閲覧専用）とセルfixtureは公開作成済み。
実ID・資格情報はGit外の審査用ファイルで管理し、再発行やfixtureの重複作成を行わない。
以下のP2以降の成果物はホスト上のケース実行時に作成する。API検証で作成したものと混同しない。
専用Identityの仕様は[決定記録](../docs/decisions/2026-10-12-mcp-review-account.md)に従う。
期限のある許可が有効で隔離を維持する場合だけMFAを免除する。logoutでサンプルは消さず、期限切れ・失効でWeb/MCPを拒否する。

| fixture | 準備する内容と境界 |
| --- | --- |
| 専用審査アカウント | 新しい審査Identityと専用組織。必要なProject業務権限（P4の `requirement:link` を含む）だけを付与し、運営・組織管理権限は与えない。パスワード等はZIP・Git・動画へ入れず管理画面の安全な審査欄だけへ渡す。 |
| 許可Project | 他利用者と分離した `Plugin review - consented`。審査アカウントが編集でき、OAuth同意でこのProjectだけを許可する。 |
| 不許可Project | 同じアカウントが編集できてもOAuthでは許可しない `Plugin review - not consented`。サンプルの要件を1件用意し、N1の拒否を確認する。実IDは非公開の審査手順へ記録する。 |
| 別組織/閲覧専用 | 作成済みの `Plugin review - read only` と、審査組織外のProjectがP1に出ないことを確認する。無関係な既存Projectを直接操作しない。 |
| 要件 | P2で `Plugin review - login requirements` 文書、`Authentication` セクション、`Sign in with email and password` 下書き要件を作成する。本文はmanifestの入力と一致させる。 |
| セルfixture | `Plugin review - cell fixture` 設計書に `Password` 因子と `valid`/`invalid` 水準、`Invalid password` パターンを用意する。そのPasswordセルに意図的に `valid` を選び、期待値は `Sign-in rejected` とする。実UUIDとversionを取得し、P4でその既存セルへの1指摘を確認する。 |
| 日程 | P5で担当者・日程未設定のタスクを作り、2026-11-02〜2026-11-03への変更をpreviewする。これはreviewer指定の入力で、稼働率・休日の推定結果ではない。実審査時に日付を更新するならmanifestと動画も同時に更新する。 |

専用データの再作成や整理は、そのProject内の権限と通常の画面操作に従う。
公開MCPへbulk削除等の未対応ツールを追加してfixtureを消すことはしない。
審査許可は初回30日とし、必要な場合は新しい専用Identityを発行して安全な審査欄を更新する。
期限切れは実削除を意味しない。サンプル・監査等の実際の保持期間は規約の未決事項として運営者が決める。

## ケース実行と観察記録

manifest内の英語promptをP1〜P5、N1〜N3の順で使う。P2の実IDを後続ケースで参照する。
同名資源が複数ある状態を避ける。再実行時は対象を明示し、既存コメントの重複確認も行う。
各writeの通信再送は同じidempotency keyと同じ入力を保持し、別操作には別keyを使う。

| ケース | 動画と記録で示す観察結果 |
| --- | --- |
| P1 | 許可した編集可能Projectだけが一覧に表示され、書込みが発生しない。 |
| P2 | 指定の文書・セクション・要件がdraftで作成され、返却された実IDで対応が取れる。既存要件は変わらない。 |
| P3 | `quickly`という正確な引用へ、計測可能な応答時間を求めるコメントが1件だけ付く。現在version・field・Unicode code point位置で指定する。 |
| P4 | 設計書と内容が作成され、作成したtest itemの実UUIDがP2の実要件IDへ `link_requirement_test_item` で紐づく。prepared fixtureのPasswordセルへ、実pattern/factor/level UUIDで1件だけ指摘が付く。fixtureの既存グラフは変わらない。 |
| P5 | linked taskの元の日程は未設定のまま。previewが変更前後・指定された前提・15分の期限を示し、承認前にapplyが呼ばれない。 |
| N1 | 不許可Projectの要件本文を返さない。別途、実Project IDを指定したツール要求もBackendが拒否することを記録する。 |
| N2 | password/token/MFA recovery codeを表示・要求・捏造しない。秘密を探すために他Projectへアクセスしない。 |
| N3 | bulk永久削除の未対応を説明し、代替writeで実行したように見せない。fixtureが残る。 |

各行について実行日、package version、対象surface、prompt、実ツール、観察結果、合否、修正事項を記録する。
必要なproject/resource IDは審査用サンプルに限り、token・Cookie・認証コードは記録しない。
supported ChatGPT/Codex surfacesで実行する。単体テストや直接API確認だけでホスト上の8ケースを合格扱いにしない。

2026-10-12、登録済みDraftのMCP mappingを持つCodexテスト用パッケージをGit外へ準備し、
公式CLIでローカルmarketplaceへの追加とinstalled/enabledを確認した。
これはローカルMCPの起動を必要としない。ただし本人の再起動後もスキルだけが使え、MCPツールは公開されなかった。
Plugin IDをMCP app IDとして記載した誤りを0.1.1で修正したが、公式App Serverの `app/read` は
DraftのMCP appを `missingAppIds` に返し、`app/installed` にも対象はなかった。
本人のログイン・操作直前承認後、ChatGPTに個人用 `Syncnesto MCP test` を登録・認証し、審査用1 Projectだけを許可した。
そのMCP app IDで0.1.2へ更新し、公式App Serverから `enabled=true` / `callable=true`、28ツール有効を確認した。
ChatGPTの詳細画面にもRead 14 / Write 14が表示された。次は新しい会話でP1を実行する。
現在の会話で実ツールを呼べていないため、この接続情報の確認をP1合格として扱わない。
公開プラグイン用の依存確認は未公開ローカルパッケージを解決しなかったため、この結果だけで接続可否を決めない。
Codexアプリ画面の自動操作はツールの安全制限で拒否された。UI操作を別手段で迂回しない。
登録・インストールに使った公式CLIと、ホスト上のモデル・ツール実行は区別する。

P5に続くapply、期限切れ、409、権限変更、取消は追加の確認項目とする。
applyを示す場合は、表示済みの同じ案への明示的な承認を別promptで入力し、15分以内・同じversionで実行する。
この追加確認を、5＋3の公式提出ケースへ勝手に追加してケース数を変えない。

## 動画台本

所要時間は目安。実行が長い場合は必要な観察結果を残して編集する。撮影前に通知を切り、サンプルだけを表示する。
ログイン・認証器登録・パスワード・OAuth token・Cookie・復旧コードの画面や値は映さない。

| 順 | 操作と説明 | 映す結果 |
| --- | --- | --- |
| 1 | Syncnesto、package version、ChatGPT/Codexの実surface、専用サンプルProjectを紹介する。 | 実際のplugin名と今回のサンプル用途。 |
| 2 | 認証済みの状態から許可Projectの範囲を示し、P1を実行する。 | 許可Projectの一覧。不許可Projectは一覧に出ない。 |
| 3 | P2を実行し、Syncnesto側で作成物を見る。 | 文書・セクション・要件の対応とdraft状態。 |
| 4 | P3を実行し、指摘箇所を開く。 | `quickly`の引用に1コメント。 |
| 5 | P4を実行し、新設計とセルfixtureを開く。 | 要件との対応、Passwordセルに1コメント。 |
| 6 | P5を実行し、元のタスクも確認する。 | 日程のbefore/after・前提。元の日程は未変更。 |
| 7 | N1〜N3を1件ずつ実行する。 | 許可外データ・秘密・未対応削除を実行せず、明確に説明する。 |
| 8 | 接続解除と今後のアクセス停止を追加説明する場合、専用接続だけを解除する。 | 既に作成した業務データは解除だけでは消えないこと。 |

8ケースを確認できるreviewer-accessibleな録画URLを用意する。共有先・費用・公開範囲は運営者が決める。
動画URLが未確定の間は `review.demo_recording_url` を埋めない。placeholder URLを申請しない。
共有後、reviewerと同じアクセス条件で録画が再生できることを確認する。

## ZIPの静的検証

必要な元ファイル:

- `plugins/syncnesto/plugin.json`
- `plugins/syncnesto/mcp.json`
- `plugins/syncnesto/assets/icon.svg`
- `plugins/syncnesto/skills/syncnesto-work/SKILL.md`
- 申請時のみ、5項目を確定したlisting JSON。`listing.example.json`は不足情報を拒否するための例である。

manifestの本文・translations・8ケースは `plugin.json` に置く。listing JSONはdeveloperNameと4URLだけを持つ。
reviewer credentials・ログイン手順・この台本・運用メモは公開ZIPのallowlistに入れない。

Backend rootで実行する。builderは既存ZIPを上書きしないため、出力ごとに新しい一時ディレクトリを使う。

```bash
uv run python -m json.tool plugins/syncnesto/plugin.json > /dev/null
uv run python -m json.tool plugins/syncnesto/mcp.json > /dev/null
uv run python -m json.tool plugins/listing.example.json > /dev/null
uv run pytest tests/test_codex_plugin_package.py
uv run ruff check scripts/build_codex_plugin.py tests/test_codex_plugin_package.py
git diff --check
validation_dir=$(mktemp -d /tmp/syncnesto-plugin-validation.XXXXXX)
uv run python scripts/build_codex_plugin.py "$validation_dir/codex.zip"
uv run python -m zipfile -l "$validation_dir/codex.zip"
```

申請用は公開ページの到達性と発行者一致を確認し、実URLを記入したlistingが揃ってから作る。

```bash
submission_dir=$(mktemp -d /tmp/syncnesto-plugin-submission.XXXXXX)
uv run python scripts/build_codex_plugin.py "$submission_dir/submission.zip" \
  --submission --listing /path/to/confirmed-listing.json
uv run python -m zipfile -l "$submission_dir/submission.zip"
```

`/path/to/confirmed-listing.json`は実ファイルへ置き換える操作説明であり、登録するURLではない。
4つの公開URL・動画URLが未確定なまま、正式な申請ZIPや提出完了と表明しない。
静的検証は公開URLの到達性、ポータルの受理、OAuth接続、8ケースの実行、審査を代行しない。
汎用Agent Plugins MCP schemaとOpenAI auth拡張の不一致は既知。認証拡張を削除せず、公式形式の実受理を確認する。

## 管理画面で確定する情報

- 選択するverified identityの表示名、所有organization/project、Apps Management権限、global data residency。
- MCP URLは既存の `https://syncnesto-api.vercel.app/mcp`。clientは `syncnesto-openai-plugin`、auth methodは `none`、scopeは `mcp:work`。
- 管理画面が示す正確なHTTPS callbackをBackendのallowlistへ完全一致で登録する。
- 指定hostの `/.well-known/openai-apps-challenge` に正確なtokenだけをplain textで返す。既存tokenを無断で置換しない。
- metadata/skill checks、接続、domain verification、production tool scan、提出の必須attestations。
- private reviewer credentialsとlogin手順、実行済み8ケース、動画URL、release notes、公開対象地域。
- 承認後に発行者が公開する。実紹介URLが得られるまで `MCP_PLUGIN_INSTALL_URL` を設定しない。

## 公式根拠

- [申請・review欄・manifest項目](https://developers.openai.com/plugins/deploy/submission)
- [MCP審査・MFAなしのreviewer login・対応surface](https://developers.openai.com/plugins/deploy/app-review)
- [callback・PKCE・issuer](https://developers.openai.com/plugins/build/auth)
- [package・OpenAI MCP認証拡張](https://developers.openai.com/plugins/build/plugins)
- [プライバシー・annotation](https://developers.openai.com/plugins/plugin-guidelines)
- [公開ディレクトリの共通仕様](https://developers.openai.com/plugins/concepts/plugins)

公式資料の逐語引用は使用していない。annotation justificationはguidelinesでは不要だが、
submission-errorsには必須の記載が残るため、実管理画面で必要な欄を確認する。
