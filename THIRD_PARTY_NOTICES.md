# PDF出力の第三者ライセンス

新PDF出力はReportLab 5.1.0、charset-normalizer、既存のPillowを使用します。
ReportLabのBSDの3条件、同梱Bitstream Vera、charset-normalizerのMIT、Pillowの
ライセンス/著作権/免責の原文は配布パッケージに保持します。deployから取り除かず、
配布資料とこの通知を保持してください。ReportLab原文は`reportlab/license.txt`と
配布metadataの`licenses/LICENSE`、Veraは`reportlab/fonts/bitstream-vera-license.txt`です。

deploy時にも原文を確認できるよう、以下にも未改変の通知を同梱します。

- [ReportLab本体](app/assets/licenses/reportlab.txt) / [配布版](app/assets/licenses/reportlab-distribution.txt)
- [Bitstream Vera](app/assets/licenses/bitstream-vera.txt)
- [charset-normalizer](app/assets/licenses/charset-normalizer.txt)
- [Pillow](app/assets/licenses/pillow.txt)

PDFの日本語フォントはBIZ UDGothic Regular/Bold（400/700）です。
原文の著作権表示とSIL Open Font License 1.1全文は
[同梱OFL.txt](app/assets/fonts/biz-ud-gothic/OFL.txt)で確認できます。
PDFへの埋め込みは文書自体のライセンスをOFLへ変更しません。

- 取得元: [google/fonts固定版](https://github.com/google/fonts/tree/6ce172f74aa355ea43eb964fa4a91570a4d3064d/ofl/bizudgothic)
- Regular SHA256: `709fcd41e3209fb765da750472f55ccdf925653e9fa7e1eb007cb65c8f749c75`
- Bold SHA256: `98a528b6b638463041968783cc0f63adaf4cdc26f5398afed68bab712d1113f3`
- 合計: 9,305,504 bytes。実行時ダウンロードやOSフォントは使用しません。

Copyright表記には取得元OFLとmetadataで異なるプロジェクトURLがあります。
取得したOFL原文を保持し、独自の表記へ置き換えません。
