"""認証済み本人に許可された公開DTOだけを返す専用取得。"""

import hashlib
import hmac

from pydantic import ValidationError
from sqlalchemy import func, literal, select, text
from sqlalchemy.dialects.postgresql import aggregate_order_by

from app.core.config import settings
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    FixedApprovalScopeConflictError,
    NotFoundError,
)
from app.models.artifact_publication import ArtifactPublicationNode
from app.repositories.artifact_publication_access import PublicationAccessRepository
from app.repositories.shared_decision_status import shared_decision_status
from app.schemas.artifact_publication import (
    PublicationContentMetadata,
    PublicationNodeRead,
)
from app.schemas.artifact_publication_access import (
    SharedPublicationFiles,
    SharedPublicationLinkTitle,
    SharedPublicationList,
    SharedPublicationNodePage,
    SharedPublicationRead,
    SharedPublicationSummary,
)
from app.schemas.design_content import HtmlMockup
from app.schemas.design_workspace import MockupPreviewRead
from app.services.artifact_release_snapshot import MAX_PAGE_BYTES, canonical_bytes
from app.services.body_image import stored_mockup_preview
from app.services.fixed_approval_cursor import decode_cursor, encode_cursor
from app.services.guest_invitation import GuestInvitationService
from app.services.publication_access_context import (
    PublicationAccessContext,
    retry_publication_operation,
)


class SharedPublicationService:
    """組織選択や通常Project権限を外部へ付与せずに固定表現を取得する。"""

    def __init__(self):
        """現在の本人認証と明示JOINだけを再利用する。"""
        self.context = PublicationAccessContext()
        self.repository = PublicationAccessRepository()

    @staticmethod
    def revision(db, actor, row):
        """内部IDやsession IDを可逆なcursorへ埋め込まない。"""
        value = [
            actor.id,
            str(db.info["auth_session"].id),
            str(row["id"]),
            row["publication_version"],
            str(row["participant_id"]),
            row["participant_version"],
            str(row["grant_id"]),
            row["grant_version"],
            row["effective_expiry"].isoformat(),
        ]
        return hmac.new(
            settings.secret_key.encode(), canonical_bytes(value), hashlib.sha256
        ).hexdigest()

    def _get(self, db, original, actor, identity):
        """許可された候補のProject境界を取り、待機後の新statementで再確認する。"""
        p = self.repository.publications
        query = self.repository.shared_query(actor.id).where(p.c.id == identity)
        candidate = db.execute(query).mappings().first()
        if candidate is None:
            raise NotFoundError()
        db.execute(
            text("SELECT publication_lock(:project,true)"),
            {"project": candidate["project_id"]},
        )
        self.context.verify(db, original, actor, internal=False)
        row = db.execute(query).mappings().first()
        if row is None:
            raise NotFoundError()
        return row, self.revision(db, actor, row)

    @staticmethod
    def _metadata(row):
        """社内正本・Storage情報を公開DTOへ入れない。"""
        return PublicationContentMetadata(
            **{
                k: row[k]
                for k in PublicationContentMetadata.model_fields
                if k != "files"
            },
            files=row["file_manifest"],
        )

    @staticmethod
    def _cursor(value, purpose, revision):
        """他本人・別session・失効前の集合cursorを再利用させない。"""
        try:
            return decode_cursor(value, 0, purpose, revision)
        except FixedApprovalScopeConflictError:
            raise ConflictError() from None

    @retry_publication_operation
    def metadata(self, original, user, identity):
        """公開中かつ本人に現在許可された内容だけを返す。"""
        with self.context.transaction(original, user) as (db, actor):
            row, revision = self._get(db, original, actor, identity)
            return SharedPublicationRead(
                content=self._metadata(row),
                expires_at=row["effective_expiry"],
                authorization_revision=revision,
            )

    @retry_publication_operation
    def link_titles(self, original, user, ids):
        """リンク先ごとに公開資格を評価し、授権後の表示名だけを取得する。"""
        with self.context.transaction(original, user) as (db, actor):
            publication = self.repository.publications
            query = self.repository.shared_query(actor.id).where(
                publication.c.id.in_(ids)
            )
            query = self.context.read_projects(
                db, original, actor, query, publication.c.project_id
            )
            rows = db.execute(
                query.with_only_columns(
                    publication.c.id,
                    publication.c.title,
                    query.selected_columns.effective_expiry.label("expires_at"),
                )
            ).mappings()
            return [SharedPublicationLinkTitle.model_validate(row) for row in rows]

    @retry_publication_operation
    def listing(
        self, original, user, q, kind, cursor, page_size, participation_id=None
    ):
        """取得前に確定したProject集合だけで検索・件数を作る。"""
        with self.context.transaction(original, user) as (db, actor):
            p = self.repository.publications
            query = self.repository.shared_query(actor.id)
            if participation_id is not None:
                query = query.where(
                    query.selected_columns.participant_id == participation_id
                )
            query = self.context.read_projects(
                db, original, actor, query, p.c.project_id
            )
            query = query.where(p.c.title.icontains(q, autoescape=True))
            if kind is not None:
                query = query.where(p.c.source_kinds.contains([kind]))
            columns = query.selected_columns
            query = query.with_only_columns(
                p.c.id,
                p.c.title,
                p.c.source_kinds,
                p.c.captured_at,
                p.c.publication_version,
                columns.participant_id,
                columns.participant_version,
                columns.grant_id,
                columns.grant_version,
                columns.effective_expiry,
                columns.public_project_name,
                columns.shared_at,
            )
            allowed = query.subquery()
            latest = allowed.c
            order = (latest.shared_at.desc(), latest.id)
            entry = func.concat_ws(
                ":",
                latest.id,
                latest.publication_version,
                latest.participant_id,
                latest.participant_version,
                latest.grant_id,
                latest.grant_version,
                latest.effective_expiry,
            )
            revision = func.encode(
                func.sha256(
                    func.convert_to(
                        func.string_agg(
                            entry, aggregate_order_by(literal(","), *order)
                        ),
                        "UTF8",
                    )
                ),
                "hex",
            )
            total, collection_revision = db.execute(
                select(func.count(), revision).select_from(allowed)
            ).one()
            scope = hmac.new(
                settings.secret_key.encode(),
                canonical_bytes(
                    [
                        actor.id,
                        str(db.info["auth_session"].id),
                        q,
                        kind,
                        str(participation_id) if participation_id is not None else None,
                        collection_revision,
                    ]
                ),
                hashlib.sha256,
            ).hexdigest()
            offset = self._cursor(cursor, "shared-publications", scope)
            if offset > total:
                raise BadRequestError()
            end = offset + page_size
            rows = (
                db.execute(
                    select(allowed).order_by(*order).offset(offset).limit(page_size)
                )
                .mappings()
                .all()
            )
            verified = bool(rows) and GuestInvitationService.mfa_verified(db, actor)
            statuses = (
                shared_decision_status(
                    db, self.repository, actor.id, [row["id"] for row in rows]
                )
                if verified
                else {}
            )
            items = []
            for row in rows:
                summary = SharedPublicationSummary(
                    id=row["id"],
                    title=row["title"],
                    source_kinds=row["source_kinds"],
                    captured_at=row["captured_at"],
                    expires_at=row["effective_expiry"],
                    authorization_revision=self.revision(db, actor, row),
                    public_project_name=row["public_project_name"],
                    shared_at=row["shared_at"],
                )
                if verified:
                    status = statuses.get(row["id"], {})
                    summary.own_decision_status = (
                        "pending"
                        if status.get("waiting")
                        else "responded"
                        if status.get("responded")
                        else "none"
                    )
                    summary.decision_refresh_at = status.get("refresh_at")
                items.append(summary)
            return SharedPublicationList(
                items=items,
                total=total,
                next_cursor=encode_cursor(0, "shared-publications", scope, end)
                if end < total
                else None,
            )

    @retry_publication_operation
    def nodes(self, original, user, identity, cursor, page_size):
        """本文を省略せず、1MiB以下の順序付きページへ分割する。"""
        with self.context.transaction(original, user) as (db, actor):
            row, revision = self._get(db, original, actor, identity)
            purpose = f"shared-nodes:{identity}"
            offset = self._cursor(cursor, purpose, revision)
            if offset >= row["node_count"]:
                raise BadRequestError()
            result = SharedPublicationNodePage(
                publication_id=identity,
                projection_version=row["projection_version"],
                items=[],
                total=row["node_count"],
                next_cursor=None,
                authorization_revision=revision,
            )
            for n in self.repository.shared_nodes(db, identity, offset, page_size):
                next_offset = n["ordinal"] + 1
                proposed = result.model_copy(
                    update={
                        "items": [*result.items, PublicationNodeRead.model_validate(n)],
                        "next_cursor": encode_cursor(0, purpose, revision, next_offset)
                        if next_offset < row["node_count"]
                        else None,
                    }
                )
                if len(proposed.model_dump_json().encode()) > MAX_PAGE_BYTES:
                    if not result.items:
                        raise ConflictError()
                    break
                result = proposed
            return result

    @retry_publication_operation
    def files(self, original, user, identity):
        """採用添付の一覧のみ。ダウンロードURLを発行しない。"""
        with self.context.transaction(original, user) as (db, actor):
            row, revision = self._get(db, original, actor, identity)
            return SharedPublicationFiles(
                items=self._metadata(row).files,
                authorization_revision=revision,
                download_allowed=row["download_allowed"] is not False,
            )

    @retry_publication_operation
    def mockup_preview(self, original, user, identity, node_id, mockup_id, revision):
        """共有中の固定本文に含まれるHTMLだけを隔離表示用に変換する。"""
        with self.context.transaction(original, user) as (db, actor):
            row, current = self._get(db, original, actor, identity)
            if not hmac.compare_digest(current, revision):
                raise ConflictError()
            nodes = ArtifactPublicationNode.__table__
            payload = db.scalar(
                select(nodes.c.payload).where(
                    nodes.c.publication_id == identity,
                    nodes.c.id == node_id,
                    nodes.c.node_kind == "specification",
                )
            )
            if payload is None or payload.get("kind") != "screen":
                raise NotFoundError()
            selected = next(
                (
                    item
                    for item in payload["content"].get("mockups", [])
                    if item.get("id") == str(mockup_id)
                ),
                None,
            )
            if selected is None:
                raise NotFoundError()
            try:
                mockup = HtmlMockup.model_validate(selected)
            except ValidationError:
                raise NotFoundError() from None
            document, css_blocked = stored_mockup_preview(
                db, row["project_id"], None, mockup
            )
            return MockupPreviewRead(document=document, css_blocked=css_blocked)

    @retry_publication_operation
    def body_image(self, original, user, identity, node_id, image_id):
        """現在の共有許可と固定本文の両方が一致する画像だけを返す。"""
        from app.models.comment_image import CommentImage
        from app.services.body_image import snapshot_references
        from app.services.storage import StorageService

        with self.context.transaction(original, user) as (db, actor):
            row, _ = self._get(db, original, actor, identity)
            nodes = ArtifactPublicationNode.__table__
            node = db.execute(
                select(nodes.c.node_kind, nodes.c.payload).where(
                    nodes.c.publication_id == identity, nodes.c.id == node_id
                )
            ).first()
            if node is None:
                raise NotFoundError()
            kind, references = snapshot_references(node.node_kind, node.payload)
            if image_id not in references:
                raise NotFoundError()
            images = CommentImage.__table__
            image = db.execute(
                select(
                    images.c.storage_key, images.c.content_type, images.c.byte_size
                ).where(
                    images.c.project_id == row["project_id"],
                    images.c.id == image_id,
                    images.c.kind == f"body_{kind}",
                    images.c.retained_at.is_not(None),
                )
            ).first()
            if image is None:
                raise NotFoundError()
            return StorageService().for_demo(db).read_uploaded_object(
                key=image.storage_key,
                content_type=image.content_type,
                byte_size=image.byte_size,
            ), image.content_type
