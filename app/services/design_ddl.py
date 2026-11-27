"""DB設計をPostgreSQLのレビュー用DDLへ整形する。"""

from app.schemas.design_content import DatabaseSpecification


def quote_identifier(value: str) -> str:
    """識別子を二重引用符で囲む。"""
    return '"' + value.replace('"', '""') + '"'


def quote_text(value: str) -> str:
    """コメントを単一引用符で囲む。"""
    return "'" + value.replace("'", "''") + "'"


def label(table: DatabaseSpecification) -> str:
    """スキーマ修飾済みテーブル名。"""
    return (
        f"{quote_identifier(table.namespace)}.{quote_identifier(table.physical_name)}"
    )


def valid_identifier(value: str) -> bool:
    """PostgreSQLによる識別子切り詰めを防止する。"""
    return bool(value.strip()) and "\x00" not in value and len(value.encode()) <= 63


def expression(value: str) -> str:
    """設計で指定した式を文として連結しない。"""
    if any(token in value for token in (";", "--", "/*", "*/", "\x00")):
        raise ValueError("式には文区切りやSQLコメントを含められません")
    return value


def generate_ddl(tables: dict[int, DatabaseSpecification]) -> tuple[str, list[str]]:
    """テーブルを先に作り、循環する外部キーも後から追加する。"""
    definitions: list[str] = []
    auxiliary: list[str] = []
    foreign_keys: list[str] = []
    warnings: list[str] = []
    namespaces: set[str] = set()
    relation_names: set[tuple[str, str]] = set()
    for identifier, table in tables.items():
        try:
            if not all(map(valid_identifier, (table.namespace, table.physical_name))):
                raise ValueError("物理テーブル名・スキーマ名を確定してください")
            name = (table.namespace, table.physical_name)
            if name in relation_names:
                raise ValueError("同じスキーマに同名のテーブルがあります")
            relation_names.add(name)
            if not table.columns:
                raise ValueError("カラムを追加してください")
            pieces = []
            columns = {}
            column_names = set()
            not_null = {
                column_id
                for constraint in table.constraints
                if constraint.kind == "not_null"
                for column_id in constraint.column_ids
            }
            comments = []
            for column in table.columns:
                name = column.physical_name
                if not valid_identifier(name) or name in column_names:
                    raise ValueError("空欄または重複した物理カラム名があります")
                column_names.add(name)
                quoted = quote_identifier(name)
                columns[column.id] = quoted
                data_type = column.data_type
                if column.length is not None and data_type == "varchar":
                    data_type = f"varchar({column.length})"
                if column.precision is not None and data_type == "numeric":
                    scale = f", {column.scale}" if column.scale is not None else ""
                    data_type = f"numeric({column.precision}{scale})"
                piece = f"  {quoted} {data_type}"
                if column.identity is not None:
                    if (
                        column.data_type not in {"smallint", "integer", "bigint"}
                        or column.default_value
                    ):
                        raise ValueError(
                            "IDENTITYは既定値なしの整数型に指定してください"
                        )
                    mode = "ALWAYS" if column.identity == "always" else "BY DEFAULT"
                    piece += f" GENERATED {mode} AS IDENTITY"
                elif column.default_value:
                    piece += f" DEFAULT {expression(column.default_value)}"
                if not column.nullable or column.id in not_null:
                    piece += " NOT NULL"
                pieces.append(piece)
                if column.description:
                    comments.append(
                        f"COMMENT ON COLUMN {label(table)}.{quoted} "
                        f"IS {quote_text(column.description)};"
                    )

            if (
                sum(
                    constraint.kind == "primary_key" for constraint in table.constraints
                )
                > 1
            ):
                raise ValueError("主キー制約はテーブルに一つだけ指定してください")
            for constraint in table.constraints:
                if constraint.kind in {"not_null", "foreign_key"}:
                    continue
                if not valid_identifier(constraint.name):
                    raise ValueError("制約名を確定してください")
                prefix = f"  CONSTRAINT {quote_identifier(constraint.name)} "
                if constraint.kind == "check":
                    if not constraint.expression.strip():
                        raise ValueError("CHECKの式を入力してください")
                    pieces.append(
                        f"{prefix}CHECK ({expression(constraint.expression)})"
                    )
                    continue
                if not constraint.column_ids:
                    raise ValueError("主キー・一意制約の対象カラムを選択してください")
                keyword = (
                    "PRIMARY KEY" if constraint.kind == "primary_key" else "UNIQUE"
                )
                pieces.append(
                    f"{prefix}{keyword} "
                    f"({', '.join(columns[item] for item in constraint.column_ids)})"
                )
            indexes = []
            for index in table.indexes:
                if not valid_identifier(index.name) or not index.column_ids:
                    raise ValueError("インデックス名と対象カラムを指定してください")
                if (index.unique or index.descending) and index.method != "btree":
                    raise ValueError("UNIQUE・降順指定にはbtreeを使用してください")
                order = [
                    columns[item] + (" DESC" if item in index.descending else "")
                    for item in index.column_ids
                ]
                predicate = (
                    f" WHERE {expression(index.predicate)}"
                    if index.predicate.strip()
                    else ""
                )
                keyword = "UNIQUE " if index.unique else ""
                indexes.append(
                    f"CREATE {keyword}INDEX {quote_identifier(index.name)} "
                    f"ON {label(table)} "
                    f"USING {index.method} ({', '.join(order)}){predicate};"
                )

            namespaces.add(table.namespace)
            definition = ",\n".join(pieces)
            definitions.append(f"CREATE TABLE {label(table)} (\n{definition}\n);")
            if table.notes:
                comments.append(
                    f"COMMENT ON TABLE {label(table)} IS {quote_text(table.notes)};"
                )
            auxiliary.extend([*indexes, *comments])
        except ValueError as error:
            warnings.append(f"DB-{identifier}: {error}")
    if warnings:
        return "", warnings

    for identifier, table in tables.items():
        columns = {
            column.id: quote_identifier(column.physical_name)
            for column in table.columns
        }
        for constraint in table.constraints:
            if constraint.kind != "foreign_key":
                continue
            target = tables.get(constraint.target_id or 0)
            if (
                target is None
                or not valid_identifier(constraint.name)
                or not constraint.column_ids
            ):
                warnings.append(
                    f"DB-{identifier}: 参照先・対象カラム・外部キー名を指定してください"
                )
                continue
            target_columns = {
                column.id: quote_identifier(column.physical_name)
                for column in target.columns
            }
            if (
                len(constraint.column_ids) != len(constraint.target_column_ids)
                or not set(constraint.target_column_ids) <= target_columns.keys()
            ):
                warnings.append(f"DB-{identifier}: 外部キーの参照カラムが一致しません")
                continue
            local = ", ".join(columns[item] for item in constraint.column_ids)
            remote = ", ".join(
                target_columns[item] for item in constraint.target_column_ids
            )
            foreign_keys.append(
                f"ALTER TABLE {label(table)} "
                f"ADD CONSTRAINT {quote_identifier(constraint.name)} "
                f"FOREIGN KEY ({local}) REFERENCES {label(target)} ({remote}) "
                f"ON DELETE {constraint.on_delete} ON UPDATE {constraint.on_update};"
            )

    if warnings:
        return "", warnings
    schemas = [
        f"CREATE SCHEMA IF NOT EXISTS {quote_identifier(name)};"
        for name in sorted(namespaces)
    ]
    return "\n\n".join([*schemas, *definitions, *foreign_keys, *auxiliary]), []
