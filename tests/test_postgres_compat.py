from db_compat import PostgresCompatConnection


def test_postgres_sql_translation_handles_sqlite_features():
    sql, wants_id = PostgresCompatConnection._translate(
        "INSERT INTO users(username,password_hash) VALUES(?,?)"
    )
    assert sql.endswith("RETURNING id")
    assert "%s" in sql
    assert wants_id is True

    sql, wants_id = PostgresCompatConnection._translate(
        "SELECT id FROM users WHERE username=? COLLATE NOCASE"
    )
    assert "LOWER(username) = LOWER(%s)" in sql
    assert wants_id is False

    sql, wants_id = PostgresCompatConnection._translate(
        "INSERT OR IGNORE INTO role_limits(role,limit_count) VALUES(?,?)"
    )
    assert "INSERT INTO role_limits" in sql
    assert sql.endswith("ON CONFLICT DO NOTHING")
    assert wants_id is False

    sql, _ = PostgresCompatConnection._translate(
        "CREATE TABLE demo (id INTEGER PRIMARY KEY AUTOINCREMENT, amount REAL NOT NULL)"
    )
    assert "BIGSERIAL PRIMARY KEY" in sql
    assert "DOUBLE PRECISION" in sql


def test_postgres_pragma_table_info_translation():
    sql, table = PostgresCompatConnection._translate("PRAGMA table_info(users)")
    assert "information_schema.columns" in sql
    assert table == "users"
