"""公開準備APIの成功・エラー応答だけを保存禁止にする。"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class PublicationCacheMiddleware:
    """既存APIのcache契約を変更せず新経路だけを保護する。"""

    def __init__(self, app: ASGIApp):
        """次のASGI appを保持する。"""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        """認可失敗やvalidationも含め、対象応答へno-storeを付ける。"""
        parts = scope.get("path", "").split("/")
        target = scope["type"] == "http" and (
            (
                len(parts) >= 4
                and parts[1] == "projects"
                and parts[3]
                in {
                    "artifact-publications",
                    "guest-participations",
                    "guest-invitations",
                    "guest-fixed-approvals",
                }
            )
            or (len(parts) >= 3 and parts[1:3] == ["shared", "publications"])
            or (len(parts) >= 3 and parts[1:3] == ["shared", "notifications"])
            or (
                len(parts) == 6
                and parts[1] == "projects"
                and parts[3] == "fixed-approvals"
                and parts[5] == "next-actions"
            )
            or (len(parts) >= 2 and parts[1] == "guest-invitations")
            or (len(parts) == 2 and parts[1] == "notification-preferences")
        )

        async def private_send(message: Message):
            if target and message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "private, no-store"
                headers["Referrer-Policy"] = "no-referrer"
            await send(message)

        await self.app(scope, receive, private_send)
