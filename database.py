from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", BASE_DIR / "instance" / "fmc.db"))

COLLECTION_TABLES = (
    "saved_cards",
    "search_log",
    "products",
    "cart",
    "user_references",
    "stores",
    "store_references",
    "messages",
    "users",
)


def get_connection() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database() -> None:
    with get_connection() as connection:
        for table_name in COLLECTION_TABLES:
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    position INTEGER NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT data FROM {table_name} ORDER BY position, id"
        ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            item = json.loads(row["data"])
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items


def save_collection(table_name: str, items: list[dict[str, Any]]) -> None:
    _validate_table_name(table_name)
    with get_connection() as connection:
        connection.execute(f"DELETE FROM {table_name}")
        connection.executemany(
            f"INSERT INTO {table_name} (position, data) VALUES (?, ?)",
            [
                (index, json.dumps(item, ensure_ascii=True))
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


def _validate_table_name(table_name: str) -> None:
    if table_name not in COLLECTION_TABLES:
        raise ValueError(f"Unknown database collection: {table_name}")
