"""認証済み本人に許可された公開DTOだけを返す専用取得。"""

import hashlib
import hmac

from sqlalchemy import text

from app.core.config import settings
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    FixedApprovalScopeConflictError,
    NotFoundError,
)
from app.repositories.artifact_publication_access import PublicationAccessRepository
from app.schemas.artifact_publication import (
    PublicationContentMetadata,
    PublicationNodeRead,
)
from app.schemas.artifact_publication_access import (
    SharedPublicationFiles,
    SharedPublicationList,
    SharedPublicationNodePage,
    SharedPublicationRead,
    SharedPublicationSummary,
)
from app.services.artifact_release_snapshot import MAX_PAGE_BYTES, canonical_bytes
from app.services.fixed_approval_cursor import decode_cursor, encode_cursor
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
    def listing(self, original, user, q, kind, cursor, page_size):
        """取得前に確定したProject集合だけで検索・件数を作る。"""
        with self.context.transaction(original, user) as (db, actor):
            p = self.repository.publications
            query = self.repository.shared_query(actor.id)
            projects = sorted({r["project_id"] for r in db.execute(query).mappings()})
            for project_id in projects:
                db.execute(
                    text("SELECT publication_lock(:project,true)"),
                    {"project": project_id},
                )
            self.context.verify(db, original, actor, internal=False)
            query = query.where(
                p.c.project_id.in_(projects), p.c.title.icontains(q, autoescape=True)
            )
            if kind is not None:
                query = query.where(p.c.source_kinds.contains([kind]))
            rows = (
                db.execute(query.order_by(p.c.captured_at.desc(), p.c.id))
                .mappings()
                .all()
            )
            scope = hmac.new(
                settings.secret_key.encode(),
                canonical_bytes(
                    [
                        actor.id,
                        str(db.info["auth_session"].id),
                        q,
                        kind,
                        [[str(r["id"]), self.revision(db, actor, r)] for r in rows],
                    ]
                ),
                hashlib.sha256,
            ).hexdigest()
            offset = self._cursor(cursor, "shared-publications", scope)
            if offset > len(rows):
                raise BadRequestError()
            end = offset + page_size
            return SharedPublicationList(
                items=[
                    SharedPublicationSummary(
                        id=r["id"],
                        title=r["title"],
                        source_kinds=r["source_kinds"],
                        captured_at=r["captured_at"],
                        expires_at=r["effective_expiry"],
                        authorization_revision=self.revision(db, actor, r),
                    )
                    for r in rows[offset:end]
                ],
                total=len(rows),
                next_cursor=encode_cursor(0, "shared-publications", scope, end)
                if end < len(rows)
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
                items=self._metadata(row).files, authorization_revision=revision
            )
