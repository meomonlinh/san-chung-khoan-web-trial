"""Small SQLite-compatible adapter for PostgreSQL (psycopg 3).

The web application keeps one SQL code path for local SQLite development and uses
PostgreSQL in cloud deployments when DATABASE_URL is configured.
"""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping, Iterator
from typing import Any


_AUTO_ID_TABLES = {
    "users", "trades", "inbox", "transfers", "loans", "penalties",
    "penalty_requests", "chat_messages", "audit_events", "admin_requests",
    "moderation_reports", "report_messages", "emerald_lots",
    "owner_support_sessions", "premium_requests",
}


class CompatRow(Mapping[str, Any]):
    """Row supporting both row['column'] and row[0], matching sqlite3.Row usage."""
    def __init__(self, names: list[str], values: tuple[Any, ...]):
        self._names = names
        self._values = tuple(values)
        self._map = dict(zip(names, values))

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._map[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._names)

    def __len__(self) -> int:
        return len(self._names)


class CompatCursor:
    def __init__(self, cursor: Any = None, lastrowid: int | None = None,
                 names: list[str] | None = None, buffered: list[tuple[Any, ...]] | None = None):
        self._cursor = cursor
        self.lastrowid = lastrowid
        self._names = names or []
        self._buffered = buffered

    def _convert(self, row: Any) -> CompatRow | None:
        if row is None:
            return None
        return CompatRow(self._names, tuple(row))

    def fetchone(self) -> CompatRow | None:
        if self._buffered is not None:
            if not self._buffered:
                return None
            return self._convert(self._buffered.pop(0))
        return self._convert(self._cursor.fetchone())

    def fetchall(self) -> list[CompatRow]:
        if self._buffered is not None:
            rows, self._buffered = self._buffered, []
            return [self._convert(row) for row in rows]  # type: ignore[list-item]
        return [self._convert(row) for row in self._cursor.fetchall()]  # type: ignore[list-item]

    def __iter__(self) -> Iterator[CompatRow]:
        while True:
            row = self.fetchone()
            if row is None:
                break
            yield row

    @property
    def rowcount(self) -> int:
        if self._buffered is not None:
            return len(self._buffered)
        return int(getattr(self._cursor, "rowcount", -1))


class PostgresCompatConnection:
    def __init__(self, raw_connection: Any, psycopg_module: Any):
        self._raw = raw_connection
        self._psycopg = psycopg_module

    @staticmethod
    def _description_names(cursor: Any) -> list[str]:
        if not cursor.description:
            return []
        return [getattr(col, "name", col[0]) for col in cursor.description]

    @staticmethod
    def _translate(sql: str) -> tuple[str, bool]:
        statement = sql.strip()
        if not statement:
            return statement, False

        if re.match(r"^BEGIN\s+IMMEDIATE\s*$", statement, flags=re.I):
            return "BEGIN", False

        # SQLite schema introspection used by the feature migration.
        pragma = re.match(r"^PRAGMA\s+table_info\((\w+)\)\s*$", statement, flags=re.I)
        if pragma:
            table = pragma.group(1).replace("'", "")
            return (
                "SELECT ordinal_position - 1 AS cid, column_name AS name "
                "FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = %s "
                "ORDER BY ordinal_position",
                table,
            )  # type: ignore[return-value]

        # Case-insensitive SQLite username comparisons.
        statement = re.sub(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\?\s+COLLATE\s+NOCASE",
            r"LOWER(\1) = LOWER(?)", statement, flags=re.I,
        )
        # COLLATE NOCASE is not a PostgreSQL column constraint; a functional unique
        # index is created separately for the users table.
        statement = re.sub(r"\s+COLLATE\s+NOCASE\b", "", statement, flags=re.I)

        ignored_insert = bool(re.match(r"^INSERT\s+OR\s+IGNORE\s+INTO\b", statement, flags=re.I))
        if ignored_insert:
            statement = re.sub(r"^INSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", statement, flags=re.I)
            statement = statement.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

        statement = re.sub(
            r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
            "BIGSERIAL PRIMARY KEY", statement, flags=re.I,
        )
        statement = re.sub(r"\bINTEGER\b", "BIGINT", statement, flags=re.I)
        statement = re.sub(r"\bREAL\b", "DOUBLE PRECISION", statement, flags=re.I)
        statement = statement.replace("?", "%s")

        insert = re.match(r"^INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)\b", statement, flags=re.I)
        wants_id = bool(insert and insert.group(1).lower() in _AUTO_ID_TABLES and not re.search(r"\bRETURNING\b", statement, flags=re.I))
        if wants_id:
            statement = statement.rstrip().rstrip(";") + " RETURNING id"
        return statement, wants_id

    def execute(self, sql: str, params: Any = ()) -> CompatCursor:
        statement = sql.strip()
        if re.match(r"^PRAGMA\s+(foreign_keys|journal_mode)\s*=", statement, flags=re.I):
            return CompatCursor(names=[], buffered=[])

        translated, metadata = self._translate(sql)
        if isinstance(metadata, str):
            cursor = self._raw.cursor()
            try:
                cursor.execute(translated, (metadata,))
                return CompatCursor(cursor, names=self._description_names(cursor))
            except self._psycopg.Error as exc:
                raise sqlite3.DatabaseError(str(exc)) from exc

        cursor = self._raw.cursor()
        try:
            cursor.execute(translated, params or ())
            lastrowid = None
            if metadata:
                inserted = cursor.fetchone()
                lastrowid = int(inserted[0]) if inserted else None
                # INSERT callers in this project use lastrowid, not returned rows.
                return CompatCursor(cursor=None, lastrowid=lastrowid, names=[], buffered=[])
            return CompatCursor(cursor, names=self._description_names(cursor))
        except self._psycopg.IntegrityError as exc:
            raise sqlite3.IntegrityError(str(exc)) from exc
        except self._psycopg.Error as exc:
            raise sqlite3.DatabaseError(str(exc)) from exc

    def executemany(self, sql: str, seq_of_params: Any) -> CompatCursor:
        translated, _ = self._translate(sql)
        cursor = self._raw.cursor()
        try:
            cursor.executemany(translated, seq_of_params)
            return CompatCursor(cursor, names=self._description_names(cursor))
        except self._psycopg.IntegrityError as exc:
            raise sqlite3.IntegrityError(str(exc)) from exc
        except self._psycopg.Error as exc:
            raise sqlite3.DatabaseError(str(exc)) from exc

    def executescript(self, script: str) -> CompatCursor:
        result = CompatCursor(names=[], buffered=[])
        # Current project schema consists of ordinary DDL/DML statements without
        # semicolons inside SQL literals, so this compact split is sufficient.
        for statement in script.split(";"):
            if statement.strip():
                result = self.execute(statement)
        return result

    def close(self) -> None:
        self._raw.close()


def connect_postgres(database_url: str) -> PostgresCompatConnection:
    try:
        import psycopg
        from psycopg.rows import tuple_row
    except ImportError as exc:
        raise RuntimeError("Thiếu psycopg. Hãy cài các thư viện trong requirements.txt.") from exc
    try:
        raw = psycopg.connect(database_url, autocommit=True, row_factory=tuple_row, connect_timeout=15)
    except psycopg.Error as exc:
        raise RuntimeError(f"Không kết nối được PostgreSQL: {exc}") from exc
    return PostgresCompatConnection(raw, psycopg)
