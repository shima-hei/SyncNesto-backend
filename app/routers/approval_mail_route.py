"""承認の保存成功後に、既存の耐久メールキューを処理する。"""

from fastapi import Request
from fastapi.routing import APIRoute
from starlette.background import BackgroundTasks

from app.core.config import settings
from app.services.approval_mail_dispatch import dispatch_pending_mail


class ApprovalMailRoute(APIRoute):
    """業務トランザクションが成功した応答に配送処理を付ける。"""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def with_delivery(request: Request):
            response = await handler(request)
            if (
                settings.is_public_environment
                and request.method in {"POST", "PATCH", "DELETE"}
                and 200 <= response.status_code < 300
                and not request.url.path.endswith(("/preview", "/decision-preview"))
            ):
                tasks = BackgroundTasks()
                if response.background is not None:
                    tasks.add_task(response.background)
                tasks.add_task(dispatch_pending_mail)
                response.background = tasks
            return response

        return with_delivery
