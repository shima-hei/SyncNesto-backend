"""変更履歴APIレスポンスの共通整形処理を提供するモジュール。"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.schemas.change_log import ChangeLogUserRead

ChangeLogValueFormatter = Callable[[Any], Any]


@dataclass(frozen=True)
class ChangeLogUpdateEntry:
    """更新操作1回分として保存する変更履歴値を表す。"""

    action: str
    field_name: str | None
    old_value: dict[str, Any] | None
    new_value: dict[str, Any] | None


def build_changed_field_snapshots(
    before: dict[str, Any],
    after: dict[str, Any],
    candidate_fields: set[str],
) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    """変更されたフィールドだけの前後スナップショットを作成する。

    Args:
        before: 更新前のスナップショット。
        after: 更新後のスナップショット。
        candidate_fields: 差分検出対象のフィールド名。

    Returns:
        変更フィールド一覧、更新前スナップショット、更新後スナップショット。
    """
    changed_fields = sorted(
        field_name
        for field_name in candidate_fields
        if before.get(field_name) != after.get(field_name)
    )
    before_snapshot = {
        field_name: before.get(field_name) for field_name in changed_fields
    }
    after_snapshot = {
        field_name: after.get(field_name) for field_name in changed_fields
    }
    return changed_fields, before_snapshot, after_snapshot


def build_update_change_log_entry(
    *,
    updated_fields: list[str],
    old_values: dict[str, Any],
    new_values: dict[str, Any],
    default_action: str,
    field_action_map: dict[str, str] | None = None,
) -> ChangeLogUpdateEntry | None:
    """更新操作1回分の変更履歴保存値を作成する。

    Args:
        updated_fields: 実際に値が変わったフィールド名一覧。
        old_values: 変更前スナップショット。
        new_values: 変更後スナップショット。
        default_action: 汎用更新時に保存する操作種別。
        field_action_map: 単一項目更新時に専用actionへ変換するマップ。

    Returns:
        変更履歴保存値。変更がない場合はNone。
    """
    if not updated_fields:
        return None

    actions_by_field = field_action_map or {}
    if len(updated_fields) == 1 and updated_fields[0] in actions_by_field:
        field_name = updated_fields[0]
        return ChangeLogUpdateEntry(
            action=actions_by_field[field_name],
            field_name=field_name,
            old_value={field_name: old_values.get(field_name)},
            new_value={field_name: new_values.get(field_name)},
        )

    return ChangeLogUpdateEntry(
        action=default_action,
        field_name=None,
        old_value={"snapshot": old_values},
        new_value={
            "updated_fields": updated_fields,
            "snapshot": new_values,
        },
    )


@dataclass(frozen=True)
class ChangeLogFormatConfig:
    """変更履歴整形に必要なドメイン別設定。"""

    action_map: dict[str, str] = field(default_factory=dict)
    target_type_map: dict[str, str] = field(default_factory=dict)
    field_value_labels: dict[str, dict[str, str]] = field(default_factory=dict)
    field_names: set[str] = field(default_factory=set)
    user_id_fields: set[str] = field(default_factory=set)


class ChangeLogFormatter:
    """変更履歴APIレスポンス向けの共通整形処理を提供する。"""

    def __init__(self, config: ChangeLogFormatConfig) -> None:
        """ChangeLogFormatterを初期化する。

        Args:
            config: ドメイン別の変更履歴整形設定。
        """
        self.config = config

    def normalize_action(self, action: str) -> str:
        """DB保存済みの操作種別をAPI用の安定コードに変換する。

        Args:
            action: DBに保存されている操作種別。

        Returns:
            APIレスポンス用の操作種別コード。
        """
        return self.config.action_map.get(action, action.replace(".", "_"))

    def normalize_target_type(self, target_type: str) -> str:
        """DB保存済みの対象種別をAPI用の安定コードに変換する。

        Args:
            target_type: DBに保存されている対象種別。

        Returns:
            APIレスポンス用の対象種別コード。
        """
        return self.config.target_type_map.get(target_type, target_type)

    def normalize_field_name(self, field_name: str | None) -> str | None:
        """DB保存済みのフィールド名をAPI用の単一コードへ変換する。

        Args:
            field_name: DBに保存されているフィールド名。

        Returns:
            APIレスポンス用のフィールド名。複数フィールドや未知値はNone。
        """
        if field_name is None:
            return None
        if "," in field_name:
            return None
        if self.config.field_names and field_name not in self.config.field_names:
            return None
        return field_name

    def normalize_updated_fields(self, field_names: list[str]) -> list[str]:
        """更新フィールド一覧をAPI用のフィールドコード配列へ変換する。

        Args:
            field_names: DB保存値または入力値由来のフィールド名一覧。

        Returns:
            APIレスポンス用のフィールドコード配列。
        """
        normalized_fields: list[str] = []
        for field_name in field_names:
            normalized = self.normalize_field_name(field_name)
            if normalized is not None:
                normalized_fields.append(normalized)
        return normalized_fields

    def to_storage_action(self, action: str | None) -> str | None:
        """API用操作種別コードをDB保存値へ変換する。

        Args:
            action: APIリクエストで指定された操作種別コード。

        Returns:
            DB検索に使う操作種別。未指定の場合はNone。
        """
        if action is None:
            return None
        reverse_map = {value: key for key, value in self.config.action_map.items()}
        return reverse_map.get(action, action)

    def to_storage_target_type(self, target_type: str | None) -> str | None:
        """API用対象種別コードをDB保存値へ変換する。

        Args:
            target_type: APIリクエストで指定された対象種別コード。

        Returns:
            DB検索に使う対象種別。未指定の場合はNone。
        """
        if target_type is None:
            return None
        reverse_map = {
            value: key for key, value in self.config.target_type_map.items()
        }
        return reverse_map.get(target_type, target_type)

    def extract_change_value(
        self,
        value: dict[str, Any] | None,
        field_name: str | None,
        *,
        users_by_id: dict[int, ChangeLogUserRead],
        value_formatters: dict[str, ChangeLogValueFormatter] | None = None,
    ) -> Any:
        """変更履歴の値をフロントエンドが扱いやすい形へ変換する。

        Args:
            value: DBに保存されている変更前後の値。
            field_name: 変更対象フィールド名。
            users_by_id: 表示補助に使うユーザー情報。
            value_formatters: フィールド別の追加整形処理。

        Returns:
            APIレスポンス用に整形した変更値。
        """
        if value is None:
            return None
        if field_name is not None and field_name in value:
            return self.format_change_log_value(
                field_name,
                value[field_name],
                users_by_id=users_by_id,
                value_formatters=value_formatters,
            )
        return value

    def collect_user_ids(
        self,
        values: list[tuple[str | None, dict[str, Any] | None]],
    ) -> list[int | None]:
        """変更履歴値に含まれる表示補助用ユーザーIDを集める。

        Args:
            values: field_nameと変更履歴値の組み合わせ一覧。

        Returns:
            表示補助に必要なユーザーID一覧。
        """
        user_ids: list[int | None] = []
        for field_name, value in values:
            if value is None:
                continue
            if field_name in self.config.user_id_fields and field_name in value:
                user_id = value.get(field_name)
                user_ids.append(user_id if isinstance(user_id, int) else None)
            for user_field in self.config.user_id_fields:
                user_id = value.get(user_field)
                if isinstance(user_id, int):
                    user_ids.append(user_id)
        return user_ids

    def format_change_log_value(
        self,
        field_name: str,
        value: Any,
        *,
        users_by_id: dict[int, ChangeLogUserRead],
        value_formatters: dict[str, ChangeLogValueFormatter] | None = None,
    ) -> Any:
        """変更履歴の値に表示補助情報を付与する。

        Args:
            field_name: 変更対象フィールド名。
            value: 変更前後の値。
            users_by_id: 表示補助に使うユーザー情報。
            value_formatters: フィールド別の追加整形処理。

        Returns:
            APIレスポンス用に整形した変更値。
        """
        if value_formatters is not None and field_name in value_formatters:
            return value_formatters[field_name](value)
        labels = self.config.field_value_labels.get(field_name)
        if labels is not None:
            return self.format_code_label_value(value, labels)
        if field_name in self.config.user_id_fields:
            return self.format_user_id_label_value(value, users_by_id=users_by_id)
        return value

    def format_code_label_value(
        self,
        value: Any,
        labels: dict[str, str],
    ) -> dict[str, Any] | None:
        """コード値をcode/label形式に変換する。

        Args:
            value: 変更前後のコード値。
            labels: コード値に対応する表示補助ラベル。

        Returns:
            code/label形式の値。値がNoneの場合はNone。
        """
        if value is None:
            return None
        code = str(value)
        return {"code": code, "label": labels.get(code, code)}

    def format_user_id_label_value(
        self,
        value: Any,
        *,
        users_by_id: dict[int, ChangeLogUserRead],
    ) -> dict[str, Any] | None:
        """ユーザーID値をid/label形式に変換する。

        Args:
            value: 変更前後のユーザーID。
            users_by_id: 表示補助に使うユーザー情報。

        Returns:
            id/label形式の値。値がNoneの場合はNone。
        """
        if value is None:
            return None
        user = users_by_id.get(value) if isinstance(value, int) else None
        return {"id": value, "label": user.name if user is not None else str(value)}
