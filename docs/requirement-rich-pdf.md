# 要件定義書の構造化PDF出力

業務方針はProject5文書10 §19–20、方式決定は作業101、実装は作業103。

`REQUIREMENT_RICH_PDF_ENABLED`は初期false。PDFだけを切り替えます。
OFFは既存PDF、HTML flagとは独立です。ReportLab/font読込は新PDF経路だけ。
元データ、Markdown/HTML、保存済み出力/固定版を変換保存・再生成しません。
APIの成功schema、章選択/順序/属性/コメント・履歴、認可と成功時のEXPORTEDは共通です。

ReportLab5.1.0＋BIZ UDGothic Regular/Bold、共通Markdown設定から安全な
Paragraph/Tableを作ります。利用者HTML/XML・式は実行せず、画像/URLは取得しません。
ReportLab5.1.0の補助面ToUnicodeを、新PDF専用のTTFont派生クラス内でUTF-16BEへ
補正します。global monkeypatchや依存本体の変更はせず、𠮟の抽出を2方式で検証します。
codeは通常段落の空白圧縮を避け、空白を別Unicode文字へ置換しません。
Mermaid/codeは文字、画像は代替文/参照先/題名、emは元の強調記号を保持します。
表はA4縦、余白50pt・10pt/行高14pt、4ptのセル余白。各列に全角4文字＋左右余白を
確保できない場合は生成停止します。自動縮小・横向き・列分割はしません。
ヘッダーは再掲、普通の行は行境界優先、1ページ超行は行内継続します。
1ページ内の短いREQだけをまとめ、長文は分割し、見出し/ラベルを後続と保持します。

両書体のcmapを描画前に確認し、欠字/未対応の結合文字・IVS・ZWJは生成停止します。
原文をNFKCで別字へ変えません。既存400のmessage/code形式で
`REQUIREMENT_PDF_UNSUPPORTED_CONTENT`とモデルID/フィールド/行/code point位置・
U+値を返し、Frontendはこのcodeだけの詳細を既存通知へ表示します。
本文をエラー/ログへ含めず、失敗時は成功履歴を作りません。旧PDFへ黙って戻しません。

font通知は[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)と同梱OFLに保持。
docsはdeploy除外ですが、通知/fontは除外しません。共有fontのsubset状態の競合を
避けるため同一プロセス内の生成をロックします。PDFが3,000,000 bytesを超えたら
生成停止し、base64/JSONの4.5 MB上限に余裕を残します。maxDuration=60は維持します。

出力回帰はPDF flagをfalseにして再起動/再deploy。依存/importによる起動障害は
直前の検証済みcommit/deploymentへ戻します。DB migration/downgradeは不要です。
有効化・merge・本番反映は別承認。HTML/PDFの字形/改ページの完全一致は保証しません。
