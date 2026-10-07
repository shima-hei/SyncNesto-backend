"""利用者・用途に紐付く直接アップロードの許可を管理する。"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from pydantic import ValidationError

from app.core import error_messages
from app.core.config import settings
from app.core.exceptions import BadRequestError
from app.schemas.file_upload import FileUploadPlan, FileUploadRequest
from app.services.storage import StorageService

logger = logging.getLogger(__name__)
UPLOAD_AUDIENCE = "file-upload"


class FileUploadService:
    """保存先に依存せず、署名付きURLと登録用トークンを扱う。"""

    def __init__(self, storage: StorageService) -> None:
        """既存のS3操作を利用する。"""
        self.storage = storage

    def plan(
        self, data: FileUploadRequest, *, user_id: int, scope: str
    ) -> FileUploadPlan:
        """サーバー設定に対応する送信方式を返す。"""
        if settings.file_upload_mode == "server":
            return FileUploadPlan(mode="server")
        expires_in = settings.file_upload_url_expires_seconds
        if not 1 <= expires_in <= 3600:
            raise RuntimeError("FILE_UPLOAD_URL_EXPIRES_SECONDS must be 1..3600")
        upload_id = uuid4()
        key = self.storage.object_key(f"pending-uploads/{user_id}/{upload_id}")
        self.storage.reserve(key, data.byte_size, expires_in, upload_id)
        token = jwt.encode(
            {
                "aud": UPLOAD_AUDIENCE,
                "sub": str(user_id),
                "scope": scope,
                "upload_id": str(upload_id),
                "file": data.model_dump(),
                "exp": datetime.now(UTC) + timedelta(seconds=expires_in),
            },
            settings.secret_key,
            algorithm=settings.algorithm,
        )
        return FileUploadPlan(
            mode="presigned",
            url=self.storage.presigned_upload_url(
                key=key,
                content_type=data.content_type,
                byte_size=data.byte_size,
                expires_in=expires_in,
            ),
            headers={"Content-Type": data.content_type},
            upload_token=token,
            expires_in=expires_in,
        )

    def verify(
        self, token: str, *, user_id: int, scope: str
    ) -> tuple[UUID, FileUploadRequest, str]:
        """有効期限・利用者・用途を検証して一時保存先を復元する。"""
        if settings.file_upload_mode != "presigned":
            raise BadRequestError(error_messages.FILE_UPLOAD_UNAVAILABLE)
        try:
            payload = jwt.decode(
                token,
                settings.secret_key,
                algorithms=[settings.algorithm],
                audience=UPLOAD_AUDIENCE,
                options={"require": ["exp", "sub", "aud"]},
            )
            if payload["sub"] != str(user_id) or payload["scope"] != scope:
                raise ValueError("upload scope mismatch")
            upload_id = UUID(payload["upload_id"])
            data = FileUploadRequest.model_validate(payload["file"])
        except (
            jwt.InvalidTokenError,
            ValueError,
            KeyError,
            TypeError,
            ValidationError,
        ):
            raise BadRequestError(error_messages.FILE_UPLOAD_INVALID) from None
        if self.storage.demo_db is not None:
            self.storage.demo_db.info["demo_upload_reservation"] = upload_id
        return (
            upload_id,
            data,
            self.storage.object_key(f"pending-uploads/{user_id}/{upload_id}"),
        )

    def discard(self, key: str) -> None:
        """一時ファイルの削除失敗で登録結果を上書きしない。"""
        try:
            self.storage.delete_object(key)
        except Exception:
            logger.warning(error_messages.FILE_UPLOAD_STORAGE_ERROR)
