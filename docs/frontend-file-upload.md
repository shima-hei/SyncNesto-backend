# ファイル送信方式の切り替え

`FILE_UPLOAD_MODE=server`（既定値）は従来のmultipart API、`presigned` はブラウザからS3互換ストレージへの直接PUTを利用する。設定はバックエンドに集約する。アイコンは2MiB、エビデンスは20MiB・実行あたり20件の既存制限を維持する。

## API

用途ごとに以下のAPIを呼ぶ。

| 用途 | 計画POST | 完了POST | 従来API |
| --- | --- | --- | --- |
| アイコン | `/auth/me/avatar/upload-plan` | `/auth/me/avatar/upload-complete` | `PUT /auth/me/avatar` |
| エビデンス | `/projects/{project_id}/test-designs/{design_id}/cases/{case_id}/executions/{execution_id}/evidence/upload-plan` | 同じevidence配下の`/upload-complete` | 同じevidenceへの`POST` |

計画のJSONは `{ "filename": "example.txt", "content_type": "text/plain", "byte_size": 123 }`。`mode=server` の場合は既存APIを呼ぶ。`mode=presigned` の場合は応答の `url` に `headers` とファイル本体をPUTし、成功後に完了APIへ `{ "upload_token": "..." }` を送る。

計画・完了・従来APIはNext.js BFF経由でCookie認証とCSRF保護を利用する。エビデンスは両APIで `test_case:execute` とプロジェクト・設計書・ケース・実行の所属を確認する。ストレージへのPUTには認証Cookie、Authorization、CSRFヘッダーを送らない。

完了APIはアップロード許可の署名・利用者・用途・期限、保存された実容量・Content-Type、既存の内容検証を確認する。署名付きPUTにはContent-TypeとContent-Lengthを含める。クライアントはContent-Lengthを手動設定せず、ブラウザがBlobから生成する値を利用する。

直接送信先は `pending-uploads/{user_id}/{upload_id}`。確定時は検証したバイト列を別キーへ保存し、一時キーを削除する。登録済みキーを署名付きPUTで上書きできない。エビデンスは完了要求の再送で二重登録せず、削除済みの復活も拒否する。件数確認と登録は実行単位のDBロックで直列化する。

アイコン完了応答は既存のCurrentUserRead、エビデンス完了応答は既存のTestEvidenceRead。既存multipart APIも互換のため維持する。ファイル本体をJSON APIやNext.js BFFへ再送しない。

## 環境設定

```env
FILE_UPLOAD_MODE=presigned
FILE_UPLOAD_URL_EXPIRES_SECONDS=600
AWS_S3_ENDPOINT_URL=http://localhost:4566
AWS_S3_BUCKET_NAME=syncnesto-local-app-bucket
AWS_REGION=ap-northeast-1
```

URL・登録用トークンの有効期間は1〜3600秒。AWS S3では `AWS_S3_ENDPOINT_URL` と `AWS_ENDPOINT_URL` を未設定にする。他のS3互換サービスは接続先・リージョン・バケット・サーバー側認証情報を設定する。保存データモデルの変更はない。

ストレージ側には公開フロントエンドのOriginを許可するCORSが必要。AWS S3用の最小例:

```json
[
  {
    "AllowedOrigins": ["http://localhost:3000", "https://your-app.vercel.app"],
    "AllowedMethods": ["PUT"],
    "AllowedHeaders": ["content-type"],
    "MaxAgeSeconds": 600
  }
]
```

実際に利用するOriginだけを指定し、ブラウザから到達できるエンドポイントを使う。CORSはAWS S3または各互換サービスの管理画面・APIで設定する。S3互換サービスの署名・ヘッダー対応は実環境で確認する。

中断や失敗により残る一時ファイルには `pending-uploads/` のみを対象とした1日後のライフサイクル削除を設定する。バージョニング有効時は、このprefixの非現行バージョンと削除マーカーも清掃対象にする。登録済みファイルを対象にしない。

署名URL・upload_tokenはログや永続ストレージに保存しない。無効・期限切れ・内容不一致は400、権限なしは403、所属対象なしは404。アップロードを再試行する際は新しい計画を取得する。

## LocalStackでの実通信テスト

LocalStackが起動済みの状態で実行する。

```bash
RUN_LOCALSTACK_UPLOAD_TESTS=1 uv run pytest tests/integration/test_file_upload_localstack.py -q
```

テスト用PostgreSQLと一時バケットを使い、ログインCookie・CSRF、送信計画、CORS preflight、実HTTPでの署名付きPUT、完了登録、ダウンロードまで検証する。5MiB・20MiBの添付、PNGでない内容の拒否、アイコンの両方式、完了再送の重複防止を確認する。バケットは終了時にバージョン・削除マーカーを含めて削除する。

既定の接続先は `http://localhost:4566`。`LOCALSTACK_ENDPOINT_URL` で変更できるが、誤って外部環境へ接続しないようloopbackのみ許可する。通常のpytest実行では実通信テストをskipする。
