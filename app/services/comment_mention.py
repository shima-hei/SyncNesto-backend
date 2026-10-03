"""本文とメンションの整合性を検証し、保存する関連を確定する。"""

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import BadRequestError
from app.models.comment_mention import CommentMention, MentionedComment
from app.models.user import User
from app.repositories.comment_mention import CommentMentionRepository
from app.schemas.comment_mention import CommentMentionOccurrence


class CommentMentionService:
    """将来の通知は確定済みmention_targetsを参照できる。"""

    def prepare(
        self,
        db: Session,
        *,
        project_id: int,
        permission: str,
        body: str,
        mentions: list[CommentMentionOccurrence],
        previous: list[dict] | None = None,
    ) -> dict[int, list[dict]]:
        """所属・位置・表示名を保存前に検証する。"""
        if not mentions:
            return {}
        boundaries = {0: 0}
        offset = 0
        for index, char in enumerate(body):
            offset += 2 if ord(char) > 0xFFFF else 1
            boundaries[offset] = index + 1
        sorted_mentions = sorted(mentions, key=lambda mention: mention.start)
        last_end = 0
        for mention in sorted_mentions:
            if (
                mention.start < last_end
                or mention.start not in boundaries
                or mention.end not in boundaries
                or mention.end <= mention.start
                or body[boundaries[mention.start] : boundaries[mention.end]]
                != f"@{mention.display_name}"
            ):
                raise BadRequestError(error_messages.COMMENT_MENTION_INVALID)
            last_end = mention.end
        user_ids = {mention.user_id for mention in mentions}
        users = CommentMentionRepository().eligible_users(db, project_id, permission)
        names = {user.id: user.name for user in users.filter(User.id.in_(user_ids))}
        if names.keys() != user_ids:
            raise BadRequestError(error_messages.COMMENT_MENTION_USER_UNAVAILABLE)
        old_labels = {
            (mention["user_id"], mention["display_name"]) for mention in previous or []
        }
        prepared: dict[int, list[dict]] = {}
        for mention in sorted_mentions:
            if (
                mention.display_name != names[mention.user_id]
                and (mention.user_id, mention.display_name) not in old_labels
            ):
                raise BadRequestError(error_messages.COMMENT_MENTION_INVALID)
            prepared.setdefault(mention.user_id, []).append(
                mention.model_dump(exclude={"user_id"})
            )
        return prepared

    def targets(self, prepared: dict[int, list[dict]]) -> list[CommentMention]:
        """ユーザーごとに一意の関連を生成する。"""
        return [
            CommentMention(user_id=user_id, occurrences=occurrences)
            for user_id, occurrences in prepared.items()
        ]

    def replace(
        self, comment: MentionedComment, prepared: dict[int, list[dict]]
    ) -> None:
        """既存関連を再利用し、消えたメンションを削除する。"""
        existing = {target.user_id: target for target in comment.mention_targets}
        targets = []
        for user_id, occurrences in prepared.items():
            target = existing.get(user_id) or CommentMention(user_id=user_id)
            target.occurrences = occurrences
            targets.append(target)
        comment.mention_targets = targets
