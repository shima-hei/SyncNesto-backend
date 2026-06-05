# 認証・認可設計チェックリスト

## 1. 認証（Authentication）

「誰なのか」を確認する仕組み。

### ログイン方式

- メールアドレス + パスワード
- OAuthログイン
  - Google
  - GitHub
  - Microsoft
- SAML
- OIDC
- MFA（二段階認証）
- Passkey / WebAuthn

---

## 2. パスワード管理

### ハッシュ化方式

- bcrypt
- Argon2
- scrypt

### パスワードポリシー

- 最低文字数
- 記号必須
- 数字必須
- 大文字小文字
- 辞書攻撃対策
- 漏洩済みパスワードチェック

### パスワードリセット

- メールリンク
- ワンタイムトークン
- 有効期限
- 再利用禁止
- トークン失効

---

## 3. 認可（Authorization）

「何ができるか」を管理する仕組み。

### 認可モデル

- RBAC
- ABAC
- ReBAC

### 権限スコープ

例:

- system
- project
- document
- task

### API保護

- 401 Unauthorized
- 403 Forbidden

### 所属チェック

例:

- project_members
- organization_members

---

## 4. セッション管理

### セッション方式

- JWT
- Session ID

### トークン設計

- Access Token
- Refresh Token
- Expiration
- Rotation

### Cookie設定

- HttpOnly
- Secure
- SameSite

### ログアウト

- Token失効
- 全端末ログアウト
- Refresh Token revoke

---

## 5. セキュリティ対策

### CSRF対策

- SameSite
- CSRF Token

### XSS対策

- Token保護
- HTMLエスケープ
- CSP

### ブルートフォース対策

- Rate Limit
- CAPTCHA
- IP Block

### Credential Stuffing対策

- 漏洩PWチェック
- ログイン試行制限

### その他

- Session Fixation対策
- Replay Attack対策
- MITM対策（HTTPS）
- Open Redirect対策

---

## 6. OAuth / OIDC

### OAuth2

- Authorization Code Flow
- PKCE
- state parameter

### OIDC

- ID Token
- nonce
- scopes

---

## 7. 運用・監査

### 監査ログ

記録対象:

- ログイン
- ログアウト
- パスワード変更
- MFA変更
- 権限変更

### 異常検知

- 不審IP
- 短時間大量ログイン
- 海外アクセス

### 管理機能

- アカウント凍結
- 強制ログアウト

---

## 8. UX（ユーザー体験）

### ログイン維持

- Refresh Token
- Remember Me

### MFAリカバリ

- Backup Codes
- 再設定導線

### エラーメッセージ

NG例:

text メールアドレスが存在しません 

推奨:

text 認証に失敗しました 

---

## 9. インフラ・ネットワーク

### 防御

- WAF
- CDN
- Rate Limit

### Secret管理

- AWS Secrets Manager
- SSM Parameter Store

---

## 10. DB設計

### 代表テーブル

- users
- sessions
- refresh_tokens
- password_reset_tokens
- email_verifications
- mfa_secrets
- login_histories

---

## 11. メール認証

### メール確認

- verify-email
- 有効期限
- 再送機能

---

## 12. MFA（二段階認証）

### 認証方式

- TOTP
- SMS
- Backup Codes

---

## 13. Passkey / WebAuthn

### 生体認証

- Face ID
- Touch ID
- Windows Hello

---

# Syncnesto向けMVP構成案

## 最初に実装するもの

- メールアドレス + パスワード
- JWT（Access / Refresh）
- HttpOnly Cookie
- RBAC
- bcrypt または Argon2
- Password Reset
- Rate Limit

---

# 設計で最も重要なこと

認証設計で本当に難しいのは実装ではなく、

text どこまでやるかを決めること 

である。

セキュリティ・UX・運用コストのバランスを取りながら、
システム特性に応じた適切な設計を行う必要がある。