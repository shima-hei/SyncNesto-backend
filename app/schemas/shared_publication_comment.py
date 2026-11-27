"""固定公開版に対する共有スレッド。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.comment_image import MAX_COMMENT_IMAGES
from app.schemas.comment_mention import CommentMentionText
from app.schemas.requirement_workflow import RequirementReviewAnchor


class SharedCommentMentionOccurrence(CommentMentionText):
    model_config = ConfigDict(extra="forbid")

    recipient_id: UUID


class SharedCommentMentionCandidate(BaseModel):
    id: UUID
    name: str


class SharedPublicationCommentCreate(BaseModel):
    """本文、返信先、固定本文内の位置と共有投稿の付随情報。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    body: str = Field(min_length=1, max_length=4000)
    parent_id: UUID | None = None
    node_id: UUID | None = None
    anchor: RequirementReviewAnchor | None = None
    attachment_ids: list[UUID] = Field(
        default_factory=list, max_length=MAX_COMMENT_IMAGES
    )
    mentions: list[SharedCommentMentionOccurrence] = Field(
        default_factory=list, max_length=100
    )

    @model_validator(mode="after")
    def inherits_anchor(self):
        """返信のアンカーは親スレッドから引き継ぐ。"""
        if self.parent_id is not None and (
            self.node_id is not None or self.anchor is not None
        ):
            raise ValueError("返信にアンカーを指定できません")
        if self.anchor is not None and self.node_id is None:
            raise ValueError("引用する固定本文を指定してください")
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise ValueError("同じ添付ファイルを複数指定できません")
        return self


class SharedCommentFileRead(BaseModel):
    """内部IDや保存先を含めない共有コメントのファイル情報。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    filename: str
    content_type: str
    byte_size: int
    content_digest: str


class SharedPublicationCommentRead(BaseModel):
    """作成者の内部識別子を含めない共有本文。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    parent_id: UUID | None
    node_id: UUID | None
    anchor: RequirementReviewAnchor | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    body: str
    author_label: str
    created_at: datetime
    attachments: list[SharedCommentFileRead] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    mentions: list[CommentMentionText] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )


class SharedPublicationCommentList(BaseModel):
    """閲覧可能な固定版のスレッドまたは返信ページ。"""

    items: list[SharedPublicationCommentRead]
    total: int
    can_post: bool
    download_allowed: bool = True
    expires_at: datetime | None = None


class GuestCommentEntry(SharedPublicationCommentRead):
    """本人に公開中の固定版と、そこに記録された共有スレッド。"""

    publication_id: UUID
    publication_title: str
    expires_at: datetime


class GuestCommentList(BaseModel):
    items: list[GuestCommentEntry]
    total: int
