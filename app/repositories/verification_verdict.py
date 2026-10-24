"""選択した根拠と現在の対象集合を、件数に比例するSQLなしで取得する。"""

import json

from sqlalchemy import Text, cast, func, select, text

from app.core.exceptions import ArtifactReleaseTooLargeError, NotFoundError
from app.models.verification_verdict import VerificationRequest as Request
from app.services.artifact_release_snapshot import canonical_bytes


class VerdictRepository:
    def get(self, db, project_id, identity, *, lock=False):
        query = select(Request).where(
            Request.project_id == project_id, Request.id == identity
        )
        row = db.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise NotFoundError()
        return row

    def selected(self, db, project_id, data):
        if not data.entries:
            return []
        # 巨大なset本文全体を取得せず、指定entryとそのfile_slotsだけ投影する。
        query = text("""
        WITH wanted AS (SELECT * FROM jsonb_to_recordset(CAST(:wanted AS jsonb)) AS
        w(set_id uuid,entry_id uuid)),
        chosen AS (
          SELECT s.id AS set_id,s.status,s.plan_id,s.plan_version,s.subject_digest,
          s.checklist_digest,s.manifest_digest,
                 s.actor_name,s.created_at,e.entry,
                 coalesce((SELECT jsonb_agg(f || jsonb_build_object('preserved_key',
                 o.storage_key)) FROM jsonb_array_elements(s.manifest->'files') f
                   JOIN requirement_verification_evidence_objects o ON
                   o.id=(f->>'object_id')::uuid AND o.set_id=s.id AND o.selected AND
                   o.state='verified'
                   WHERE (f->>'slot')::int IN (SELECT value::int FROM
                   jsonb_array_elements_text(e.entry->'file_slots'))),'[]'::jsonb)
                   AS files
          FROM wanted w JOIN requirement_verification_evidence_sets s ON s.id=w.set_id
          CROSS JOIN LATERAL (SELECT jsonb_path_query_first(s.selection,
          '$.entries[*] ? (@.id == $id)',jsonb_build_object('id',
          w.entry_id::text)::jsonb) AS entry) e
          WHERE s.project_id=:project AND e.entry IS NOT NULL
        ), budget AS (SELECT *,
        sum(octet_length(entry::text)+octet_length(files::text)) OVER() AS bytes
        FROM chosen)
        SELECT set_id,status,plan_id,plan_version,subject_digest,checklist_digest,
        manifest_digest,actor_name,created_at,bytes,
               CASE WHEN bytes<=1572864 THEN entry END AS entry,CASE WHEN
               bytes<=1572864 THEN files END AS files FROM budget
        """)
        rows = (
            db.execute(
                query,
                {
                    "project": project_id,
                    "wanted": json.dumps(
                        [e.model_dump(mode="json") for e in data.entries]
                    ),
                },
            )
            .mappings()
            .all()
        )
        if len(rows) != len(data.entries):
            raise NotFoundError()
        if any(r["bytes"] > 1572864 for r in rows):
            raise ArtifactReleaseTooLargeError()
        return rows

    def inventory(self, db, project_id, scopes):
        """rowごとの対象を投影。無効な選択patternも消さず状態として残す。"""
        targets = [
            dict(scope=str(scope), requirement_id=req, row_id=r["id"], **t)
            for scope, req, rows in scopes
            for r in rows
            for t in r.get("targets", [])
        ]
        if not targets:
            return []
        query = text("""
        WITH targets AS (SELECT * FROM jsonb_to_recordset(CAST(:targets AS jsonb))
        AS t(scope text,requirement_id int,row_id text,item_id uuid,pattern_scope
        text,pattern_ids jsonb)),
        items AS (SELECT t.*,i.design_id,i.pattern_table_id FROM targets t JOIN
        test_items i ON i.id=t.item_id
          JOIN test_designs d ON d.id=i.design_id AND d.project_id=:project AND
          d.deleted_at IS NULL
          JOIN requirement_test_items rt ON rt.item_id=i.id AND
          rt.requirement_id=t.requirement_id
          WHERE i.deleted_at IS NULL AND NOT i.is_spacer),
        pairs AS (
          SELECT i.scope,i.row_id,i.item_id,i.design_id,p.id AS pattern_id,p.enabled
          AS active,i.item_id::text||':'||p.id::text AS source_key
          FROM items i JOIN test_patterns p ON p.table_id=i.pattern_table_id AND
          p.design_id=i.design_id AND p.deleted_at IS NULL
          WHERE (i.pattern_scope='all_enabled' AND p.enabled) OR
          (i.pattern_scope='selected' AND i.pattern_ids ? p.id::text)
          UNION ALL
          SELECT i.scope,i.row_id,i.item_id,i.design_id,p.id,p.enabled,l.id::text
          FROM items i JOIN test_item_patterns l ON l.item_id=i.item_id JOIN
          test_patterns p ON p.id=l.pattern_id AND p.deleted_at IS NULL
          WHERE i.pattern_table_id IS NULL AND ((i.pattern_scope='all_enabled' AND
          p.enabled) OR (i.pattern_scope='selected' AND i.pattern_ids ? p.id::text))
          UNION ALL
          SELECT i.scope,i.row_id,i.item_id,i.design_id,NULL::uuid,true,i.item_id::text
          FROM items i WHERE i.pattern_table_id IS NULL AND
          i.pattern_scope='all_enabled' AND NOT EXISTS(SELECT FROM
          test_item_patterns l WHERE l.item_id=i.item_id)
        ) SELECT * FROM pairs ORDER BY scope,row_id,item_id,source_key LIMIT 1001
        """)
        rows = (
            db.execute(query, {"project": project_id, "targets": json.dumps(targets)})
            .mappings()
            .all()
        )
        if len(rows) > 1000:
            raise ArtifactReleaseTooLargeError()
        return [dict(r) for r in rows]

    def runs(self, db, pairs, byte_budget=8 * 1048576):
        """直近runと全比較素材の予算を1 statementで判定する。"""
        keys = list({(p["design_id"], p["source_key"]) for p in pairs})
        if not keys:
            return []
        query = text("""
        WITH wanted AS (SELECT * FROM jsonb_to_recordset(CAST(:keys AS jsonb)) AS
        w(design_id int,source_key text)),
        rows AS (SELECT c.id AS case_id,w.design_id,w.source_key,e.id AS
        execution_id,e.run_number,e.status,e.notes,
           e.source,octet_length(e.source::text)+octet_length(e.notes) AS bytes
           FROM wanted w JOIN test_cases c ON c.design_id=w.design_id AND
           coalesce(c.source_key,c.item_pattern_id::text)=w.source_key
           LEFT JOIN LATERAL (SELECT * FROM test_executions WHERE case_id=c.id ORDER
           BY run_number DESC LIMIT 1) e ON true),
        budget AS (SELECT *,sum(bytes) OVER() AS total FROM rows)
        SELECT case_id,design_id,source_key,execution_id,run_number,status,total,
          CASE WHEN total<=:budget AND bytes<=1048576 THEN source END AS source,
          CASE WHEN total<=:budget AND bytes<=1048576 THEN notes END AS notes FROM
          budget
        """)
        rows = (
            db.execute(
                query,
                {
                    "keys": json.dumps(
                        [dict(design_id=d, source_key=k) for d, k in keys]
                    ),
                    "budget": byte_budget,
                },
            )
            .mappings()
            .all()
        )
        if any(
            r["total"] and (r["total"] > byte_budget or r["source"] is None)
            for r in rows
        ):
            raise ArtifactReleaseTooLargeError()
        return [dict(r) for r in rows]

    def adverse(self, db, watermarks, byte_budget=8 * 1048576):
        """最新OKで隠れるNG/保留も観測点以降から拾う。容量超過は未集計。"""
        if not watermarks:
            return []
        query = text("""
        WITH marks AS (SELECT * FROM jsonb_to_recordset(CAST(:marks AS jsonb)) AS
        m(case_id uuid,run_number int,scope text)),
        bounded AS (SELECT m.scope,e.case_id,e.run_number,e.status,e.source,
        octet_length(e.source::text) AS bytes
          FROM marks m JOIN test_executions e ON e.case_id=m.case_id AND
          e.run_number>m.run_number
          ORDER BY m.scope,e.case_id,e.run_number LIMIT 5001),
        budget AS (SELECT *,count(*) OVER() AS count,sum(bytes) OVER() AS total FROM
        bounded)
        SELECT scope,case_id,run_number,status,count,total,
          CASE WHEN count<=5000 AND total<=:budget AND bytes<=1048576 THEN source
          END AS source FROM budget
        """)
        rows = (
            db.execute(query, {"marks": json.dumps(watermarks), "budget": byte_budget})
            .mappings()
            .all()
        )
        if any(
            r["count"] > 5000 or r["total"] > byte_budget or r["source"] is None
            for r in rows
        ):
            raise ArtifactReleaseTooLargeError()
        return rows

    def pages(self, db, project_id, requirement_id, offset, limit):
        query = select(Request).where(Request.project_id == project_id)
        if requirement_id is not None:
            query = query.where(Request.source_requirement_id == requirement_id)
        size = func.octet_length(cast(Request.basis, Text)) + func.coalesce(
            func.octet_length(cast(Request.decision_snapshot, Text)), 0
        )
        # ページ本文の合計にも上限を設ける。単体detailの上限と別。
        sizes = (
            db.execute(
                query.with_only_columns(size)
                .order_by(Request.created_at, Request.id)
                .offset(offset)
                .limit(limit + 1)
            )
            .scalars()
            .all()
        )
        if sum(sizes) > 8 * 1048576:
            raise ArtifactReleaseTooLargeError()
        return list(
            db.scalars(
                query.order_by(Request.created_at, Request.id)
                .offset(offset)
                .limit(limit + 1)
            )
        )

    def source_files(self, db, project_id, files):
        """保全コピーと元ファイルの現在状態は別に検査する。"""
        if not files:
            return set()
        query = text("""
          WITH wanted AS (
            SELECT * FROM jsonb_to_recordset(CAST(:files AS jsonb))
              AS w(kind text,id uuid)
          )
          SELECT w.kind,w.id FROM wanted w
          JOIN document_attachments a ON a.id=w.id
          JOIN project_documents d ON d.id=a.document_id
          WHERE w.kind='document_attachment' AND a.deleted_at IS NULL
            AND d.deleted_at IS NULL AND d.project_id=:project
          UNION
          SELECT w.kind,w.id FROM wanted w
          JOIN test_evidence e ON e.id=w.id
          JOIN test_executions x ON x.id=e.execution_id
          JOIN test_cases c ON c.id=x.case_id
          JOIN test_designs d ON d.id=c.design_id
          WHERE w.kind='test_evidence' AND e.deleted_at IS NULL
            AND d.deleted_at IS NULL AND d.project_id=:project
        """)
        return {
            (r.kind, str(r.id))
            for r in db.execute(
                query,
                {
                    "project": project_id,
                    "files": json.dumps(
                        [dict(kind=f["kind"], id=f["id"]) for f in files]
                    ),
                },
            )
        }

    @staticmethod
    def bounded(value, maximum=2097152):
        if len(canonical_bytes(value)) > maximum:
            raise ArtifactReleaseTooLargeError()
        return value
