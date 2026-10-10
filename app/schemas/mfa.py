"""追加認証APIの秘密を再表示しない入出力。"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

NextStep = Literal[
    "authenticated",
    "mfa_setup",
    "mfa_verify",
    "recovery_codes",
    "mfa_recovery",
    "initial_password",
]


class PasskeyDescriptor(BaseModel):
    """本人の登録済みcredentialだけを提示する。"""

    id: str
    type: Literal["public-key"]


class PasskeyAlgorithm(BaseModel):
    """WebAuthn標準の公開鍵アルゴリズム。"""

    type: Literal["public-key"]
    alg: int


class PasskeySelection(BaseModel):
    """端末内の本人確認とdiscoverable credentialを必須にする。"""

    residentKey: Literal["required"] = "required"
    userVerification: Literal["required"] = "required"
    requireResidentKey: bool = True


class MfaRegistrationOptions(BaseModel):
    """ブラウザへ渡すWebAuthn登録契約。"""

    challenge: str
    rp: "PasskeyRp"
    user: "PasskeyUser"
    pubKeyCredParams: list[PasskeyAlgorithm]
    timeout: int = 60000
    attestation: Literal["none"] = "none"
    authenticatorSelection: PasskeySelection
    excludeCredentials: list[PasskeyDescriptor] = []


class PasskeyRp(BaseModel):
    """固定のRelying Party。"""

    id: str
    name: str


class PasskeyUser(BaseModel):
    """本人の不透明識別子と表示名。"""

    id: str
    name: str
    displayName: str


class MfaAuthenticationOptions(BaseModel):
    """ブラウザへ渡すWebAuthn追加認証契約。"""

    challenge: str
    rpId: str
    timeout: int = 60000
    allowCredentials: list[PasskeyDescriptor]
    userVerification: Literal["required"] = "required"


class MfaFlowRead(BaseModel):
    """限定状態に必要な次手順だけを返す。"""

    next_step: NextStep
    methods: list[str] = []
    expires_at: datetime | None = None
    recovery_codes: list[str] | None = None


class MfaName(BaseModel):
    """本人が認証器を区別する表示名。"""

    name: str = Field(min_length=1, max_length=100)
    model_config = {"extra": "forbid", "str_strip_whitespace": True}


class MfaCode(BaseModel):
    """通常のコードまたは一度限りの復旧コード。"""

    code: str = Field(min_length=1, max_length=100)
    recovery: bool = False
    model_config = {"extra": "forbid"}


class MfaPasskeyResponse(BaseModel):
    """ブラウザからのWebAuthn結果。Backendが署名を検証する。"""

    credential: dict[str, Any]


class MfaTotpSetup(BaseModel):
    """初回だけ表示する登録情報。URLはログやストレージに保存しない。"""

    provisioning_uri: str
    qr_data_url: str


class MfaFactorRead(BaseModel):
    """公開鍵やTOTP秘密を含めない認証器一覧。"""

    id: UUID
    kind: str
    name: str
    verified_at: datetime
    last_used_at: datetime | None


class MfaStatus(BaseModel):
    """本人の登録済み認証器と復旧コードの残数。"""

    factors: list[MfaFactorRead]
    recovery_codes_remaining: int
    recent: bool


class MfaPassword(BaseModel):
    """機微な操作のためにパスワードを再確認する。"""

    password: str = Field(min_length=1, max_length=128)
    model_config = {"extra": "forbid"}


class MfaRecover(MfaPassword):
    """運営者が発行した再登録要求。"""

    token: str = Field(min_length=32, max_length=200)


class MfaRecoveryApproval(BaseModel):
    """身分証や秘密は受け付けず、本人確認方法の分類を監査する。"""

    verification_method: Literal["in_person", "previously_verified_channel"]
    identity_confirmed: Literal[True]
    reason: str = Field(min_length=10, max_length=500)
    model_config = {"extra": "forbid", "str_strip_whitespace": True}
