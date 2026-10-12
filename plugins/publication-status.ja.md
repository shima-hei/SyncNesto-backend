# 公開申請の進捗と費用条件

2026-10-12更新。ユーザー提示の画面でIndividualの **Verified** を確認した。
発行者の本人確認は完了したが、プラグインのZIPアップロード・公開審査提出・公開は未完了。
以下の本人確認前の料金確認・停止経緯は当時の記録として保持する。
規約草案は [publication-draft.ja.md](publication-draft.ja.md)、
実装・配布の記録は [決定記録](../docs/decisions/2026-10-10-codex-plugin.md) にある。

## ユーザーの条件

- 追加請求を発生させない。請求が必要なら方法を再検討する。
- **追加請求なしと確認できれば**、本人操作でカードを登録して審査を継続する。
- 上記はAPIクレジット購入、有料利用開始、自動チャージ、クラウドの有料化を許可するものではない。
- 公開MCP、本人の現在の権限、通常/デモのDB・Storage分離は維持する。

## 本人確認の申請前に確認したこと

1. OpenAIのプラグイン管理画面でアップロード開始を試みると、確認済みの発行者identityが必要として止まった。
2. 個人の本人確認を開始すると、既定の有効な支払方法を先に登録するよう求められた。
3. 支払方法ページでは未課金プラン、Billing overviewではFree trial・API残高0.00 USDと表示された。
   ChatGPTの契約とAPI課金は別である旨も表示された。
4. カード保存フォームには、登録・本人確認に伴う請求額や費用ゼロの保証が表示されなかった。
   ページが提供する読み取り専用の注文情報でも合計額は未設定で、将来請求の可能性を示す値だった。
   この値だけから現在の請求や定期契約があるとは判断しない。
5. [OpenAIの申請手順](https://developers.openai.com/plugins/deploy/submission) と
   [MCP審査要件](https://developers.openai.com/plugins/deploy/app-review) では本人確認の必須条件を確認した。
   申請料・本人確認料・カード登録時の請求がすべてゼロという明示は確認できていない。
6. 既存のNeon組織とSupabase組織は管理APIでFreeプランを確認した。プランや資源は変更していない。
   Vercelは既存のHobby運用記録があるが、今回の管理API応答では契約プランを再確認できなかった。

## 本人確認の申請前の停止記録

カード情報は入力・保存していない。APIクレジットの購入、APIキーの作成、モデルAPI呼出し、
自動チャージの設定、有料プランの開始も行っていない。
カードフォームは未入力のまま閉じ、本人確認前の状態を保持した。
この時点では追加請求ゼロを確定できず、本人へのカード登録依頼と審査継続を保留した。

次にOpenAIの公式窓口または管理画面上の明示条件で確認する事項:

> 公開プラグインの個人Developer verificationを目的に支払方法を登録したい。
> APIクレジットの購入や有料API利用を開始せず、追加請求0 USDで登録・本人確認・審査提出を完了できるか。
> カード確認の一時的な与信・引落し、最低購入額、その他の必須料金があるか。

問い合わせはまだ送信していない。料金条件が判明しても、本人確認とカード情報入力は本人が行う。
費用条件を満たせない場合は、ユーザーと相談して配布方法を再検討する。
公開審査を通さない方式へ無断で切り替えない。

## 現在の状態と再開時の作業

2026-10-11、本人確認のVerifiedを受け、追加請求を発生させない条件で公開申請作業を再開する。
本人確認をやり直さない。希望する公開名は `shime-hei`、窓口は `syncnesto@gmail.com`。
確認済みidentityの表示名と希望する公開名の整合、および公開時に表示される名前の扱いを選択時に確認する。
実管理画面のUpload Plugin入口まで確認した。現在選択されている本人確認済みidentityの表示名は
希望する公開名と異なるため、公開表示の扱いを本人と確認してから選択・アップロードする。
実名はこの公開リポジトリへ記録しない。ZIPは未アップロード、追加費用を伴う操作も未実施。

本人が実施したカード登録・本人確認の請求条件を再確認したわけではない。
今回読んだOpenAI公式資料にも、登録・本人確認・審査の追加料金がすべて0であるという明示はなかった。
一方、公開申請手順にAPIキー作成・APIクレジット購入・モデルAPI呼出しは要求されていない。
料金や購入、有料利用開始を伴う操作は行わない。料金が必要、または金額が不明な操作に到達した場合は、
その操作を止めて条件を確認する。Verifiedは無料の保証ではない。

### 準備した申請資材

- manifestの基本listing、starter prompts、release notesを英語にし、日本語のsubtitle/descriptionを `ja-JP` に保持した。
- 審査用の正常系5件・異常系3件を、具体的な入力・期待ツール・観察可能な結果へ整理した。
- [審査準備と動画台本](review-plan.ja.md)に分離したfixture、実行記録、録画順、ZIPの静的検証をまとめた。
- `listing.example.json` のsupport/privacy/termsは未公開のため空欄を維持する。
  このJSONはbuilderが受け付ける5項目専用で、listing本文やtranslationsはmanifest側に置く。

以上は資材の準備であり、審査用アカウントの作成、ホストからのケース実行、動画の録画・共有、
ZIPの実アップロード、審査への提出を完了した記録ではない。

### MFA審査要件と既存方針の衝突

OpenAIの[申請手順](https://developers.openai.com/plugins/deploy/submission)と
[MCP審査要件](https://developers.openai.com/plugins/deploy/app-review)は、
審査用の専用サンプルアカウントに、MFA承認・メール/SMSコード・magic link・私設ネットワークを必要とせず
直ちにログインできることを求めている。
Syncnestoの[通常アカウント全員にMFAを必須とする合意](../docs/decisions/2026-10-10-mfa-authorization.md)と衝突する。

審査だけのMFA除外、常設バイパス、匿名デモへのMCP開放は決定していない。
OpenAIが認める認証手順の代替を確認するか、運営者が方針を決定してから審査アカウントを準備する。
この未決事項を解消するまで、審査用ログインが要件を満たすと表明して提出しない。
2026-10-12、運営者が承認したMFA審査に関する問い合わせを、ログインした公式Help Centerから送信した。
費用については質問していない。AIサポートに公開MCP審査・OAuth接続であることを補足し、
審査担当者またはサポート担当者への確認を依頼し、人間のサポートスペシャリストへの引き継ぎ受付を確認した。
画面には数日以内の回答とメール通知が案内された。正式回答は未受領。
送信文面は[審査準備](review-plan.ja.md)に記録した。MFA必須の方針は変更していない。

### 2026-10-12の再開確認

- ユーザーはシステム設定・組織の機能許可実装を保留し、デモlogout修正とMCP審査準備の継続を指示した。
- 既存Help Center会話を再確認したが、MFAを維持する審査手順への担当者の正式回答はまだない。
- 公開名は `shime-hei` を維持するとの回答を受領。公式申請資料ではディレクトリ名が確認済みidentityから設定され、
  実管理画面では希望名と異なる確認済みの個人identityだけが選択できた。別の公開表示名を設定する項目は確認できなかった。
  その個人名を選んだZIPアップロードや、本人確認の再申請・組織名変更は行っていない。
- 本人の送信承認を受け、活動名 `shime-hei` を公開発行者名として使える正式な手順を既存サポート会話へ追記送信した。
  費用・実名・本人確認書類・秘密情報は送っていない。MFAと公開名のどちらも、一般的なAI回答を正式な受理条件と扱わない。
- 検証用Codex ZIP（申請用ではない）を再生成し、固定4ファイルのみの収録を確認した。packageテスト2件とruff成功。
  ソースの28ツールと正常系5・異常系3ケースの対応を照合した。ホスト上で8ケースを実行した結果ではない。
- 公開のOAuthメタデータ・protected-resourceは200、issuerは末尾 `/` を含む既存の正確な値と一致。
  PKCEはS256、public clientは`none`、scopeは`mcp:work`。未認証POST `/mcp`はBearer challengeを伴う401。
  GET `/mcp`は既存のstateless設計で405になる。GET結果だけで認証異常と判断しない。
- `/support`・`/privacy`・`/terms`は404で未公開。規約草案にはMFAの保存情報と運営/業務の権限分離を追記した。
  保持期間・提供対象などの未確定事項を決定したように公開せず、listingの未確定URLは空欄のまま維持する。
- 私有の検証結果・ZIPはGit外に保存した。callback/challenge・審査用資格情報・録画は未取得、審査提出は未実施。

### 残る作業

1. MFAはBackend [PR #19](https://github.com/shima-hei/SyncNesto-backend/pull/19)とFrontend [PR #25](https://github.com/shima-hei/SyncNesto-frontend/pull/25)を公開済み。
   [MFA公開確認](../docs/mfa.md#2026-10-11の公開適用記録)と、公開aliasのcommit `6813a3f` / `b3516a5`を照合した。
   日程previewのannotation修正 [Backend PR #18](https://github.com/shima-hei/SyncNesto-backend/pull/18)は同Backend配布に含まれる。
   本人のMFA登録・OAuth再接続後、ホストのtool scanで `preview_task_schedule.readOnlyHint=false` を確認する。
2. 提供範囲・保持/実削除・運営条件を[規約草案](publication-draft.ja.md)の未決事項から確定する。
   ログイン不要の問い合わせ・プライバシー・規約ページを公開し、同じ発行者を示す4つの実URLを検証する。
   MFA・復旧に伴う取扱情報も実装と運用に合わせて説明する。
3. 公開済みURLを別の確定listingへ記載し、portable ZIPを静的検証する。
   管理画面のApps Management権限とglobal data residencyのprojectを確認する。
4. ZIPを実アップロードし、OpenAI認証拡張の受理とOAuth読込を確認する。
   client `syncnesto-openai-plugin`、PKCE S256、token endpoint auth `none`、scope `mcp:work` を維持する。
   管理画面が発行した正確なcallbackだけを `MCP_PLUGIN_REDIRECT_URIS` に登録して配布する。
   challengeは指定hostの `/.well-known/openai-apps-challenge` へ正確なtokenだけをplain textで配布し検証する。
5. MFAの未決事項を解消し、他利用者から分離した通常Projectと専用審査アカウントを準備する。
   credentials・login URL・Project情報・ログイン手順は公開ZIPへ入れず、管理画面の安全な審査欄へ入力する。
   [8ケース](review-plan.ja.md)をsupported ChatGPT/Codex surfacesで実行し、観察結果と動画を用意する。
6. 公開先のtool scanと28ツールのannotationを照合する。
   [公式guidelines](https://developers.openai.com/plugins/plugin-guidelines)ではannotationのjustificationは不要だが、
   [submission-errors](https://developers.openai.com/plugins/deploy/submission-errors)には必須の記載が残る。
   実ポータルの要求を確認し、既存の[annotation根拠](tool-annotations.md)を必要に応じて使う。
7. 必須エラーと上記未決事項を解消後に審査へ提出する。承認後に発行者が公開し、
   管理画面の実紹介URLを `MCP_PLUGIN_INSTALL_URL` へ設定・再配布してアカウント画面から確認する。

公開MCP自体は有効化済み。公開プラグインの登録とは区別する。
紹介URLは未設定で、FrontendのCodex連携開始は引き続き準備中。
今回の資材準備では、DB・Storage・公開設定・認証情報を変更していない。

ChatGPTとCodexの公開ディレクトリは共通で、このパッケージのMCP部分は公開MCP審査を受ける。
ローカルmarketplaceやworkspace内配布を行っても公開審査の完了にはならない。
既存ホストの無料枠を、無制限利用や稼働保証とは扱わない。
SyncnestoのMCP実装にはOpenAIのモデルAPI呼出しを追加していない。
Codex自体の契約・利用上限は別であり、公開手続だけから無料を保証しない。
