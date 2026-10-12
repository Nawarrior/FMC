from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


DEFAULT_DATABASE_PATH = Path(r"C:\Proyectos\sqlite-tools-win-x64-3540000\FMC.db")
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH") or DEFAULT_DATABASE_PATH)


TABLE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "users": {
        "columns": {
            "user_id": "TEXT",
            "name": "TEXT",
            "email": "TEXT",
            "password_hash": "TEXT",
            "provider": "TEXT",
            "role": "TEXT",
            "country": "TEXT",
            "picture": "TEXT",
        },
        "json_fields": set(),
    },
    "saved_cards": {
        "columns": {
            "card_id": "TEXT",
            "name": "TEXT",
            "edition": "TEXT",
            "edition_code": "TEXT",
            "rarity": "TEXT",
            "quantity": "INTEGER",
            "intent": "TEXT",
            "note": "TEXT",
            "user_id": "TEXT",
            "user_name": "TEXT",
            "user_email": "TEXT",
            "transaction_completed": "INTEGER",
        },
        "json_fields": set(),
    },
    "products": {
        "columns": {
            "name": "TEXT",
            "category": "TEXT",
            "brand": "TEXT",
            "description": "TEXT",
            "quantity": "INTEGER",
            "image": "TEXT",
            "price": "REAL",
        },
        "json_fields": set(),
    },
    "cart": {
        "columns": {
            "user_email": "TEXT",
            "user_name": "TEXT",
            "name": "TEXT",
            "brand": "TEXT",
            "price": "REAL",
            "image": "TEXT",
            "quantity": "INTEGER",
            "added_at": "TEXT",
        },
        "json_fields": set(),
    },
    "stores": {
        "columns": {
            "shop_id": "TEXT",
            "name": "TEXT",
            "country": "TEXT",
            "website": "TEXT",
            "address": "TEXT",
        },
        "json_fields": set(),
    },
    "user_references": {
        "columns": {
            "target_user_id": "TEXT",
            "target_email": "TEXT",
            "target_name": "TEXT",
            "author_user_id": "TEXT",
            "author_email": "TEXT",
            "author_name": "TEXT",
            "text": "TEXT",
            "rating": "INTEGER",
            "created_at": "TEXT",
        },
        "json_fields": set(),
    },
    "store_references": {
        "columns": {
            "shop_id": "TEXT",
            "shop_name": "TEXT",
            "author_user_id": "TEXT",
            "author_email": "TEXT",
            "author_name": "TEXT",
            "text": "TEXT",
            "rating": "INTEGER",
            "created_at": "TEXT",
        },
        "json_fields": set(),
    },
    "messages": {
        "columns": {
            "message_id": "TEXT",
            "sender_user_id": "TEXT",
            "sender_name": "TEXT",
            "receiver_user_id": "TEXT",
            "receiver_name": "TEXT",
            "card_id": "TEXT",
            "card_name": "TEXT",
            "text": "TEXT",
            "created_at": "TEXT",
            "read": "INTEGER",
        },
        "json_fields": set(),
    },
    "search_log": {
        "columns": {
            "search_term": "TEXT",
            "searched_at": "TEXT",
            "card_name": "TEXT",
            "image_url": "TEXT",
            "colors": "TEXT",
            "legalities": "TEXT",
        },
        "json_fields": {"colors", "legalities"},
    },
}

COLLECTION_TABLES = tuple(TABLE_DEFINITIONS.keys())
BOOLEAN_FIELDS = {"read", "transaction_completed"}


def get_connection() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database() -> None:
    with get_connection() as connection:
        for table_name, definition in TABLE_DEFINITIONS.items():
            columns_sql = [
                "id INTEGER PRIMARY KEY AUTOINCREMENT",
                "position INTEGER NOT NULL",
            ]
            columns_sql.extend(
                f"{column_name} {column_type}"
                for column_name, column_type in definition["columns"].items()
            )
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    {", ".join(columns_sql)}
                )
                """
            )
            connection.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{table_name}_position
                ON {table_name} (position)
                """
            )


def load_collection(table_name: str) -> list[dict[str, Any]]:
    _validate_table_name(table_name)
    columns = tuple(TABLE_DEFINITIONS[table_name]["columns"].keys())
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT {', '.join(columns)} FROM {table_name} ORDER BY position, id"
        ).fetchall()

    return [_row_to_item(table_name, row) for row in rows]


def save_collection(table_name: str, items: list[dict[str, Any]]) -> None:
    _validate_table_name(table_name)
    columns = tuple(TABLE_DEFINITIONS[table_name]["columns"].keys())
    insert_columns = ("position", *columns)
    placeholders = ", ".join("?" for _ in insert_columns)

    with get_connection() as connection:
        connection.execute(f"DELETE FROM {table_name}")
        connection.executemany(
            f"""
            INSERT INTO {table_name} ({", ".join(insert_columns)})
            VALUES ({placeholders})
            """,
            [
                (index, *(_serialize_value(table_name, column, item.get(column)) for column in columns))
                for index, item in enumerate(items)
                if isinstance(item, dict)
            ],
        )


def migrate_json_files_to_db(json_files: dict[str, Path]) -> None:
    initialize_database()
    for table_name, json_path in json_files.items():
        _validate_table_name(table_name)
        if not json_path.exists() or load_collection(table_name):
            continue

        try:
            items = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue

        if isinstance(items, list):
            save_collection(table_name, [item for item in items if isinstance(item, dict)])


def _row_to_item(table_name: str, row: sqlite3.Row) -> dict[str, Any]:
    item: dict[str, Any] = {}
    json_fields = TABLE_DEFINITIONS[table_name]["json_fields"]

    for column in TABLE_DEFINITIONS[table_name]["columns"]:
        value = row[column]
        if column in BOOLEAN_FIELDS:
            item[column] = bool(value)
        elif column in json_fields and value:
            try:
                item[column] = json.loads(value)
            except json.JSONDecodeError:
                item[column] = [] if column == "colors" else {}
        else:
            item[column] = value

    return {key: value for key, value in item.items() if value is not None}


def _serialize_value(table_name: str, column: str, value: Any) -> Any:
    if column in BOOLEAN_FIELDS:
        return 1 if value else 0
    if column in TABLE_DEFINITIONS[table_name]["json_fields"]:
        return json.dumps(value or ([] if column == "colors" else {}), ensure_ascii=True)
    return value


def _validate_table_name(table_name: str) -> None:
    if table_name not in TABLE_DEFINITIONS:
        raise ValueError(f"Unknown database collection: {table_name}")
