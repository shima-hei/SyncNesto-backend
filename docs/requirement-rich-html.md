# 要件定義書の構造化HTML出力

業務上の採用範囲・受入条件はProject5文書10 §15–17、作業100を正本とする。PDFの方式確認は作業101。

## 有効化と保護

- `REQUIREMENT_RICH_HTML_ENABLED`は既定false。`html`形式だけを切り替える。
- OFFは旧HTML。Markdown/PDFはflagにかかわらず既存rendererを使う。既存の固定版・保存済みファイルを再生成せず、原文も変換保存しない。
- 既存API・`requirement:read`認可・章選択・コメント/履歴の指定・結果schemaは変更しない。出力成功後の既存EXPORTED履歴を使い、描画失敗前には記録しない。
- 取得・絞込・REQコード順は共通のRequirementExportDataに集約し、REQの印刷区間はモデルIDで確定する。本文のh4等で区切りを推測しない。
- 文書/REQ属性とタイトルは通常文字列としてescapeする。本文・理由・受入条件・章本文・未決事項・コメントはMarkdownとして描画する。

## 解析と描画

markdown-it-py 4.2.0とmdurlのみを追加。CommonMark基盤に表を有効化し、raw HTMLを無効化する。改行は現行出力に寄せて保持する。GFM全体や画面との完全一致は保証しない。

画像はimgにせず、代替文・参照先・タイトルの文字表示へ置き換える。外部/ローカル画像を取得せず、Mermaidは実行しない。未対応脚注を参照定義として消費しないためreference ruleも無効化し、参照型リンク/定義は文字として残す。追加pluginや外部CSS/フォント/JSを読み込まない。

短いREQは同頁を優先し、長いREQは継続可能。理由/受入条件/解決内容等のラベルは次の本文と離れないようにする。表は折返しと印刷時のヘッダー再掲を使う。PDF出力エンジン・フォントは変更しない。

## 検証と反映

- 旧commitの同一入力から固定したMarkdown/旧HTML/PDFのSHA、HTML限定flag、構造境界、表/強調/code、raw HTML/画像/未対応構文、選択・任意情報、失敗時の履歴を代表ケースで検証する。
- ChromiumのA4 QAは通常/長い表/横長表/一頁超セルで本文末尾・範囲・改ページを確認する。一時的なサンプル出力と専用一時DBのAPIテストを使い、通常DBに検証用出力履歴を追加しない。
- 技術結果は`.verify/task100-html/`、ブラウザ生成物は`output/playwright/task100-html/`。LOCAL・CI・本番を区別し、UXのmerge/本番反映は別承認。

## 切り戻し

出力回帰はflagをfalseにして再起動/再deployする。新依存のimportは有効なHTML経路でだけ行う。依存や起動そのものの障害は直前の検証済みcommit/deploymentへ戻す。DB migration/downgrade、業務データ削除は不要。
