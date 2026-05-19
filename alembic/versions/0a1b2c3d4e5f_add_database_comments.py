"""add database comments

Revision ID: 0a1b2c3d4e5f
Revises: f4a5b6c7d8e9
Create Date: 2026-05-19 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0a1b2c3d4e5f"
down_revision: Union[str, Sequence[str], None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_COMMENTS = {
    "users": "ユーザー: ユーザー情報を管理するテーブル",
    "projects": "プロジェクト: プロジェクト情報を管理するテーブル",
    "project_members": "プロジェクトメンバー: プロジェクト所属とプロジェクト内ロールを管理するテーブル",
    "roles": "ロール: システムロールとプロジェクトロールを管理するテーブル",
    "permissions": "権限: 操作単位の権限を管理するテーブル",
    "role_permissions": "ロール権限: ロールと権限の対応を管理するテーブル",
    "user_roles": "ユーザーロール: ユーザーに付与されたシステムロールを管理するテーブル",
}

COLUMN_COMMENTS = {
    "users": {
        "id": "ユーザーID: ユーザーを一意に識別するID",
        "email": "メールアドレス: ログインと通知に使用するメールアドレス",
        "name": "ユーザー名: 画面に表示するユーザー名",
        "hashed_password": "ハッシュ化パスワード: Argon2idでハッシュ化したパスワード",
        "department": "部署: ユーザーが所属する部署",
        "position": "役職: ユーザーの役職",
        "avatar_key": "アバターキー: S3に保存したユーザーアイコン画像のオブジェクトキー",
        "is_active": "有効フラグ: ログイン可能な有効ユーザーかを示すフラグ",
        "last_login_at": "最終ログイン日時: ユーザーが最後にログインした日時",
        "created_by": "作成者ID: このユーザーを作成したユーザーID",
        "updated_by": "更新者ID: このユーザーを最後に更新したユーザーID",
        "version": "バージョン: 楽観的排他制御に使用するバージョン番号",
        "created_at": "作成日時: レコードが作成された日時",
        "updated_at": "更新日時: レコードが最後に更新された日時",
        "deleted_at": "削除日時: 論理削除された日時",
    },
    "projects": {
        "id": "プロジェクトID: プロジェクトを一意に識別するID",
        "project_code": "プロジェクトコード: 画面表示や外部連携で使用する一意なプロジェクト識別子",
        "name": "プロジェクト名: 画面に表示するプロジェクト名",
        "description": "説明: プロジェクトの説明",
        "status": "ステータス: プロジェクトの状態",
        "start_date": "開始日: プロジェクト開始日",
        "end_date": "終了日: プロジェクト終了予定日または終了日",
        "version": "バージョン: 楽観的排他制御に使用するバージョン番号",
        "created_by": "作成者ID: このプロジェクトを作成したユーザーID",
        "updated_by": "更新者ID: このプロジェクトを最後に更新したユーザーID",
        "created_at": "作成日時: レコードが作成された日時",
        "updated_at": "更新日時: レコードが最後に更新された日時",
        "deleted_at": "削除日時: 論理削除された日時",
    },
    "project_members": {
        "id": "プロジェクトメンバーID: プロジェクトメンバーを一意に識別するID",
        "project_id": "プロジェクトID: 所属先プロジェクトID",
        "user_id": "ユーザーID: 所属するユーザーID",
        "role_id": "ロールID: プロジェクト内で付与されたロールID",
        "version": "バージョン: 楽観的排他制御に使用するバージョン番号",
        "created_at": "作成日時: レコードが作成された日時",
        "updated_at": "更新日時: レコードが最後に更新された日時",
        "deleted_at": "削除日時: 論理削除された日時",
    },
    "roles": {
        "id": "ロールID: ロールを一意に識別するID",
        "key": "ロールキー: APIと画面制御で使用する安定したロール識別子",
        "name": "ロール名: 画面表示用のロール名",
        "scope": "スコープ: ロールの適用範囲。systemまたはproject",
        "description": "説明: ロールの説明",
        "created_at": "作成日時: レコードが作成された日時",
        "updated_at": "更新日時: レコードが最後に更新された日時",
    },
    "permissions": {
        "id": "権限ID: 権限を一意に識別するID",
        "code": "権限コード: API認可判定で使用する権限コード",
        "description": "説明: 権限の説明",
        "created_at": "作成日時: レコードが作成された日時",
        "updated_at": "更新日時: レコードが最後に更新された日時",
    },
    "role_permissions": {
        "id": "ロール権限ID: ロール権限を一意に識別するID",
        "role_id": "ロールID: 権限を付与するロールID",
        "permission_id": "権限ID: ロールに付与する権限ID",
        "created_at": "作成日時: レコードが作成された日時",
    },
    "user_roles": {
        "id": "ユーザーロールID: ユーザーロールを一意に識別するID",
        "user_id": "ユーザーID: システムロールを付与するユーザーID",
        "role_id": "ロールID: ユーザーに付与するシステムロールID",
        "created_at": "作成日時: レコードが作成された日時",
    },
}


def _escape_comment(comment: str) -> str:
    """SQLコメント用にシングルクォートをエスケープする。"""
    return comment.replace("'", "''")


def _set_table_comment(table_name: str, comment: str | None) -> None:
    """テーブルコメントを設定する。"""
    if comment is None:
        op.execute(f"COMMENT ON TABLE {table_name} IS NULL")
        return

    op.execute(f"COMMENT ON TABLE {table_name} IS '{_escape_comment(comment)}'")


def _set_column_comment(
    table_name: str,
    column_name: str,
    comment: str | None,
) -> None:
    """カラムコメントを設定する。"""
    if comment is None:
        op.execute(f"COMMENT ON COLUMN {table_name}.{column_name} IS NULL")
        return

    op.execute(
        f"COMMENT ON COLUMN {table_name}.{column_name} "
        f"IS '{_escape_comment(comment)}'"
    )


def upgrade() -> None:
    """Upgrade schema."""
    for table_name, comment in TABLE_COMMENTS.items():
        _set_table_comment(table_name, comment)

    for table_name, column_comments in COLUMN_COMMENTS.items():
        for column_name, comment in column_comments.items():
            _set_column_comment(table_name, column_name, comment)


def downgrade() -> None:
    """Downgrade schema."""
    for table_name, column_comments in COLUMN_COMMENTS.items():
        for column_name in column_comments:
            _set_column_comment(table_name, column_name, None)

    for table_name in TABLE_COMMENTS:
        _set_table_comment(table_name, None)
