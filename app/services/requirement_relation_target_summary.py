"""要件関連の関連先表示情報を解決するService。"""

from sqlalchemy.orm import Session

from app.models.requirement import RequirementRelation
from app.repositories.requirement_document import RequirementDocumentRepository
from app.repositories.requirement_item import RequirementRepository
from app.repositories.requirement_open_issue import RequirementOpenIssueRepository
from app.repositories.requirement_section import RequirementSectionRepository
from app.schemas.requirement import RequirementRelationTargetSummaryRead

RelationTargetKey = tuple[str, str]


class RequirementRelationTargetSummaryService:
    """要件関連の関連先サマリを組み立てる。"""

    def __init__(
        self,
        *,
        requirement_repository: RequirementRepository | None = None,
        section_repository: RequirementSectionRepository | None = None,
        open_issue_repository: RequirementOpenIssueRepository | None = None,
        document_repository: RequirementDocumentRepository | None = None,
    ) -> None:
        """Serviceを初期化する。

        Args:
            requirement_repository: 要件Repository。
            section_repository: セクションRepository。
            open_issue_repository: 未決事項Repository。
            document_repository: 要件定義書Repository。
        """
        self.requirement_repository = requirement_repository or RequirementRepository()
        self.section_repository = section_repository or RequirementSectionRepository()
        self.open_issue_repository = (
            open_issue_repository or RequirementOpenIssueRepository()
        )
        self.document_repository = (
            document_repository or RequirementDocumentRepository()
        )

    def resolve(
        self,
        db: Session,
        relations: list[RequirementRelation],
    ) -> dict[RelationTargetKey, RequirementRelationTargetSummaryRead]:
        """関連一覧に含まれる関連先サマリを取得する。

        Args:
            db: DBセッション。
            relations: 要件関連一覧。

        Returns:
            `(target_type, target_id)` をkeyにした関連先サマリ。
        """
        ids_by_type = self._collect_target_ids_by_type(relations)
        summaries: dict[RelationTargetKey, RequirementRelationTargetSummaryRead] = {}

        for requirement in self.requirement_repository.list_by_ids(
            db,
            ids_by_type["requirement_item"],
        ):
            summaries[("requirement_item", str(requirement.id))] = (
                RequirementRelationTargetSummaryRead(
                    id=str(requirement.id),
                    code=requirement.requirement_code,
                    title=requirement.title,
                )
            )

        for section in self.section_repository.list_by_ids(
            db,
            ids_by_type["section"],
        ):
            summaries[("section", str(section.id))] = (
                RequirementRelationTargetSummaryRead(
                    id=str(section.id),
                    code=section.section_type,
                    title=section.title,
                )
            )

        for issue in self.open_issue_repository.list_by_ids(
            db,
            ids_by_type["open_issue"],
        ):
            summaries[("open_issue", str(issue.id))] = (
                RequirementRelationTargetSummaryRead(
                    id=str(issue.id),
                    code=issue.issue_code,
                    title=issue.title,
                )
            )

        for document in self.document_repository.list_by_ids(
            db,
            ids_by_type["document"],
        ):
            summaries[("document", str(document.id))] = (
                RequirementRelationTargetSummaryRead(
                    id=str(document.id),
                    code=document.document_code,
                    title=document.title,
                )
            )

        return summaries

    def _collect_target_ids_by_type(
        self,
        relations: list[RequirementRelation],
    ) -> dict[str, list[int]]:
        """関連先種別ごとのID一覧を集める。"""
        ids_by_type: dict[str, list[int]] = {
            "requirement_item": [],
            "section": [],
            "open_issue": [],
            "document": [],
        }

        for relation in relations:
            target_ids = ids_by_type.get(relation.target_type)
            if target_ids is None:
                continue

            try:
                target_id = int(relation.target_id)
            except ValueError:
                continue

            target_ids.append(target_id)

        return {
            target_type: list(dict.fromkeys(target_ids))
            for target_type, target_ids in ids_by_type.items()
        }
