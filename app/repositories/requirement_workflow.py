"""要件定義の依頼を、同一案件・固定版に限定して読み書きする。"""

from collections import defaultdict
from uuid import UUID

from sqlalchemy import Select, and_, case, exists, func, or_, select, tuple_
from sqlalchemy.orm import Session

from app.models.artifact_release import ArtifactRelease, ArtifactReleaseNode
from app.models.project import Project, ProjectMember
from app.models.requirement import RequirementOpenIssue
from app.models.requirement_workflow import (
    RequirementReviewThread,
    RequirementWorkflowParticipant,
    RequirementWorkflowRequest,
)
from app.models.user import User, UserType


def includes_requirement_node():
    """固定した文書・章・個別要件の範囲に要件ノードが含まれる条件。"""
    request, node = RequirementWorkflowRequest, ArtifactReleaseNode
    return and_(
        node.release_id == request.release_id,
        node.node_kind == "requirement",
        or_(
            request.scope_kind == "requirement_document",
            and_(
                request.scope_kind == "section",
                request.target_node_id == node.parent_id,
            ),
            and_(
                request.scope_kind == "requirement",
                request.target_node_id == node.id,
            ),
        ),
    )


def release_descendants(release_id: UUID, roots: Select[tuple[UUID]], name: str):
    """指定した固定版内の根と、その子孫を一つのCTEで得る。"""
    columns = ArtifactReleaseNode.__table__.c
    tree = roots.cte(name, recursive=True)
    return tree.union_all(
        select(columns.id)
        .join(tree, columns.parent_id == tree.c.id)
        .where(columns.release_id == release_id)
    )


class RequirementWorkflowRepository:
    """依頼の連番・明細をまとめて取得する。"""

    def by_key(self, db: Session, project_id: int, actor_id: int, request_key: UUID):
        """同じ案件・発行者の再送を取得する。"""
        return db.scalar(
            select(RequirementWorkflowRequest).where(
                RequirementWorkflowRequest.project_id == project_id,
                RequirementWorkflowRequest.requester_id == actor_id,
                RequirementWorkflowRequest.request_key == request_key,
            )
        )

    def get(
        self, db: Session, project_id: int, request_id: UUID, *, lock: bool = False
    ):
        """指定案件の依頼を取得する。"""
        query = select(RequirementWorkflowRequest).where(
            RequirementWorkflowRequest.project_id == project_id,
            RequirementWorkflowRequest.id == request_id,
        )
        return db.scalar(query.with_for_update() if lock else query)

    def is_assigned(self, db: Session, request_id: UUID, user_id: int) -> bool:
        """現在の利用者が依頼に指定されているかを確認する。"""
        return bool(
            db.scalar(
                select(
                    exists().where(
                        RequirementWorkflowParticipant.request_id == request_id,
                        RequirementWorkflowParticipant.user_id == user_id,
                    )
                )
            )
        )

    def approval_for_document(
        self, db: Session, project_id: int, document_id: int, status: str
    ) -> RequirementWorkflowRequest | None:
        """指定状態の正式承認を、文書の最新のものから取得する。"""
        row = RequirementWorkflowRequest
        return db.scalar(
            select(row)
            .where(
                row.project_id == project_id,
                row.document_id == document_id,
                row.kind == "formal_approval",
                row.status == status,
            )
            .order_by(row.finalized_at.desc(), row.created_at.desc(), row.id.desc())
            .limit(1)
        )

    def review_required(self, db: Session, project_id: int) -> bool:
        """案件で承認前レビューを必須にしているか。"""
        return db.execute(
            select(Project.requirement_review_required).where(Project.id == project_id)
        ).scalar_one()

    def completed_document_reviews(
        self, db: Session, project_id: int, document_id: int, content_digest: str
    ):
        """内容が一致し、全担当者が確認を提出した文書全体のレビューを取得する。"""
        row = RequirementWorkflowRequest
        return db.execute(
            select(row.id, row.release_id, ArtifactRelease.source_refs)
            .join(ArtifactRelease, ArtifactRelease.id == row.release_id)
            .where(
                row.project_id == project_id,
                row.document_id == document_id,
                row.kind == "review",
                row.scope_kind == "requirement_document",
                row.status == "completed",
                row.content_digest == content_digest,
            )
            .order_by(row.finalized_at.desc(), row.id.desc())
        )

    def completed_scoped_reviews(
        self,
        db: Session,
        project_id: int,
        document_id: int,
        digests: list[tuple[str, int, str]],
    ):
        """各対象の同内容で全員が確認を終えた最新のレビューを取得する。"""
        request = RequirementWorkflowRequest
        digest_key = (request.scope_kind, request.scope_id, request.content_digest)
        return db.execute(
            select(*digest_key, request.id)
            .distinct(*digest_key)
            .where(
                request.project_id == project_id,
                request.document_id == document_id,
                request.kind == "review",
                request.status == "completed",
                tuple_(*digest_key).in_(digests),
            )
            .order_by(*digest_key, request.finalized_at.desc(), request.id.desc())
        )

    def approval_blockers(self, db: Session, project_id: int, document_id: int):
        """現在未解決で、正式承認までの対応が必要なものを取得する。"""
        request = RequirementWorkflowRequest
        thread = RequirementReviewThread
        issue = RequirementOpenIssue
        corrections = db.execute(
            select(thread.id, thread.body, thread.version)
            .join(request, request.id == thread.request_id)
            .where(
                request.project_id == project_id,
                request.document_id == document_id,
                request.kind == "review",
                request.status != "cancelled",
                thread.kind == "correction",
                thread.status != "resolved",
            )
            .order_by(thread.id)
        )
        issues = db.execute(
            select(issue.id, issue.title, issue.version)
            .where(
                issue.document_id == document_id,
                issue.deleted_at.is_(None),
                issue.blocks_approval.is_(True),
                issue.status != "resolved",
            )
            .order_by(issue.id)
        )
        return [
            *(
                ("correction", str(row.id), row.body.splitlines()[0][:160], row.version)
                for row in corrections
            ),
            *(("open_issue", str(row.id), row.title, row.version) for row in issues),
        ]

    def management_requests(
        self,
        db: Session,
        project_id: int,
        document_id: int,
        readable_requests: Select,
    ):
        """明細を閲覧できる依頼から、進行中と直近の状況を取得する。"""
        row = RequirementWorkflowRequest
        query = select(row).where(
            row.project_id == project_id,
            row.document_id == document_id,
            row.id.in_(readable_requests),
            row.status != "cancelled",
        )
        latest = (
            case((row.status == "requested", 0), else_=1),
            row.display_number.desc(),
        )
        active = (
            db.scalar(
                select(func.count()).select_from(
                    query.where(
                        row.kind == "review", row.status == "requested"
                    ).subquery()
                )
            )
            or 0
        )
        review = db.scalar(query.where(row.kind == "review").order_by(*latest).limit(1))
        formal = db.scalar(
            query.where(row.kind == "formal_approval").order_by(*latest).limit(1)
        )
        return review, active, formal

    def management_corrections(
        self,
        db: Session,
        project_id: int,
        document_id: int,
        readable_requests: Select,
        resolved: bool | None,
        page: int,
        page_size: int,
    ):
        """同じ閲覧権限で指摘の集計と指定ページを取得する。"""
        row = RequirementWorkflowRequest
        thread = RequirementReviewThread
        scope = (
            row.project_id == project_id,
            row.document_id == document_id,
            row.kind == "review",
            row.status != "cancelled",
            row.id.in_(readable_requests),
            thread.kind == "correction",
        )
        done = thread.status == "resolved"
        counts = db.execute(
            select(func.count().filter(~done), func.count().filter(done))
            .select_from(thread)
            .join(row, row.id == thread.request_id)
            .where(*scope)
        ).one()
        query = (
            select(
                thread.id,
                thread.request_id,
                thread.node_id,
                thread.status,
                func.left(func.split_part(thread.body, "\n", 1), 160).label("title"),
                User.name.label("assignee_name"),
                row.display_number,
            )
            .select_from(thread)
            .join(row, row.id == thread.request_id)
            .outerjoin(User, User.id == thread.assignee_id)
            .where(*scope)
        )
        total = sum(counts)
        if resolved is not None:
            query = query.where(done if resolved else ~done)
            total = counts[1] if resolved else counts[0]
        result = db.execute(
            query.order_by(thread.created_at.desc(), thread.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).mappings()
        return list(result), total, counts

    def management_issue_counts(self, db: Session, document_id: int):
        """対象文書の未決事項を状態別に集計する。"""
        issue = RequirementOpenIssue
        unresolved = issue.status != "resolved"
        return db.execute(
            select(
                func.count().filter(unresolved),
                func.count().filter(~unresolved),
                func.count().filter(unresolved, issue.blocks_approval.is_(True)),
            ).where(issue.document_id == document_id, issue.deleted_at.is_(None))
        ).one()

    @staticmethod
    def scoped_nodes(row: RequirementWorkflowRequest):
        """固定版の選択範囲と明示した添付の子だけを取得する。"""
        table = ArtifactReleaseNode.__table__
        columns = table.c
        roots = select(columns.id).where(
            columns.release_id == row.release_id,
            or_(
                columns.id == row.target_node_id,
                and_(
                    columns.parent_id.is_(None),
                    columns.node_kind.in_(("specification", "test_design")),
                ),
            ),
        )
        tree = release_descendants(row.release_id, roots, "requirement_workflow_nodes")
        return select(ArtifactReleaseNode).join(
            tree, ArtifactReleaseNode.id == tree.c.id
        )

    def recipients(self, db: Session, project_id: int, ids: list[int] | None = None):
        """指定した現在有効な社内メンバーだけを取得する。"""
        query = (
            select(User)
            .join(ProjectMember, ProjectMember.user_id == User.id)
            .where(
                ProjectMember.project_id == project_id,
                User.deleted_at.is_(None),
                User.is_active.is_(True),
                User.user_type == UserType.INTERNAL.value,
                User.password_change_required.is_(False),
            )
            .order_by(User.name, User.id)
        )
        if ids is not None:
            query = query.where(User.id.in_(ids))
        return list(db.scalars(query))

    def create(
        self, db: Session, row: RequirementWorkflowRequest, participant_ids: list[int]
    ):
        """依頼と全担当者を同じトランザクションで保存する。"""
        last_number = db.scalar(
            select(func.max(RequirementWorkflowRequest.display_number)).where(
                RequirementWorkflowRequest.project_id == row.project_id,
                RequirementWorkflowRequest.document_id == row.document_id,
            )
        )
        row.display_number = (last_number or 0) + 1
        db.add(row)
        db.flush()
        db.add_all(
            RequirementWorkflowParticipant(request_id=row.id, user_id=user_id)
            for user_id in participant_ids
        )
        db.flush()

    def page(
        self,
        db: Session,
        project_id: int,
        document_id: int | None,
        actor_id: int,
        view: str,
        page: int,
        page_size: int,
        *,
        kind: str | None = None,
        status: str | None = None,
        search: str | None = None,
        requirement_id: int | None = None,
        visible_releases: Select | None = None,
        can_read_releases: bool = False,
    ):
        """閲覧条件を件数とページに共通して適用する。"""
        row = RequirementWorkflowRequest
        query = select(row).where(row.project_id == project_id)
        assigned = exists().where(
            RequirementWorkflowParticipant.request_id == row.id,
            RequirementWorkflowParticipant.user_id == actor_id,
        )
        if document_id is not None:
            query = query.where(row.document_id == document_id)
        if requirement_id is not None:
            assert visible_releases is not None
            query = query.where(
                row.release_id.in_(visible_releases),
                exists().where(
                    includes_requirement_node(),
                    ArtifactReleaseNode.source_id == requirement_id,
                ),
            )
            if not can_read_releases:
                query = query.where(or_(row.requester_id == actor_id, assigned))
        if kind is not None:
            query = query.where(row.kind == kind)
        if status is not None:
            query = query.where(row.status == status)
        if search:
            release = ArtifactRelease
            node = ArtifactReleaseNode
            matches = exists().where(
                release.id == row.release_id,
                or_(
                    release.title.icontains(search, autoescape=True),
                    exists()
                    .where(
                        node.id == row.target_node_id,
                        node.release_id == release.id,
                        node.payload["title"].astext.icontains(search, autoescape=True),
                    )
                    .correlate(row, release),
                ),
            )
            prefix, separator, number = search.lower().partition("-")
            if (
                separator
                and prefix in {"r", "a"}
                and number.isascii()
                and number.isdecimal()
                and len(number) <= 9
            ):
                matches = or_(
                    matches,
                    and_(
                        row.display_number == int(number),
                        row.kind == ("review" if prefix == "r" else "formal_approval"),
                    ),
                )
            query = query.where(matches)
        if view == "requested":
            query = query.where(row.requester_id == actor_id)
        if view == "assigned":
            query = query.where(assigned)
        count = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        entries = list(
            db.scalars(
                query.order_by(row.created_at.desc(), row.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return entries, count

    def display_data(self, db: Session, entries: list[RequirementWorkflowRequest]):
        """一覧の表示に必要な版・対象・担当者名を一括取得する。"""
        if not entries:
            return {}, {}, {}
        request_ids = [entry.id for entry in entries]
        versions = {
            row_id: (title, source_version, payload.get("title") or title)
            for row_id, title, source_version, payload in db.execute(
                select(
                    RequirementWorkflowRequest.id,
                    ArtifactRelease.title,
                    ArtifactRelease.source_version,
                    ArtifactReleaseNode.payload,
                )
                .join(
                    ArtifactRelease,
                    ArtifactRelease.id == RequirementWorkflowRequest.release_id,
                )
                .join(
                    ArtifactReleaseNode,
                    and_(
                        ArtifactReleaseNode.release_id
                        == RequirementWorkflowRequest.release_id,
                        ArtifactReleaseNode.id
                        == RequirementWorkflowRequest.target_node_id,
                    ),
                )
                .where(
                    RequirementWorkflowRequest.id.in_(request_ids),
                    ArtifactRelease.sealed.is_(True),
                )
            )
        }
        people: dict[UUID, list[RequirementWorkflowParticipant]] = defaultdict(list)
        user_ids = {entry.requester_id for entry in entries}
        for person in db.scalars(
            select(RequirementWorkflowParticipant)
            .where(RequirementWorkflowParticipant.request_id.in_(request_ids))
            .order_by(RequirementWorkflowParticipant.user_id)
        ):
            people[person.request_id].append(person)
            user_ids.add(person.user_id)
        names = dict(
            db.execute(select(User.id, User.name).where(User.id.in_(user_ids)))
            .tuples()
            .all()
        )
        return versions, people, names
