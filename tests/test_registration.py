from fastapi.testclient import TestClient

import main


def test_public_registration_creates_member_and_logs_in(client: TestClient):
    response = client.post("/api/register", json={
        "username": "new_player_01",
        "password": "PlayerPassword123!",
        "display_name": "Người chơi mới",
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["user"]["role"] == "member"
    assert len(payload["user"]["account_id"]) == 8
    assert response.cookies.get(main.COOKIE_NAME)

    me = client.get("/api/v2/me")
    assert me.status_code == 200, me.text
    assert me.json()["username"] == "new_player_01"
    assert me.json()["role"] == "member"
    portfolio = client.get("/api/portfolio")
    assert portfolio.status_code == 200
    assert portfolio.json()["balance"] == 500_000.0

    with main.db() as conn:
        row = conn.execute(
            "SELECT password_hash,salt,role FROM users WHERE username=?",
            ("new_player_01",),
        ).fetchone()
    assert row["role"] == "member"
    assert row["password_hash"] != "PlayerPassword123!"
    assert len(row["salt"]) >= 32


def test_public_registration_never_grants_privileged_role(client: TestClient):
    response = client.post("/api/register", json={
        "username": "member_cannot_choose_role",
        "password": "MemberPassword123!",
        "display_name": "Thành viên",
        "role": "owner",
    })
    assert response.status_code == 200, response.text
    assert response.json()["user"]["role"] == "member"


def test_public_registration_rejects_duplicate_username_case_insensitively(client: TestClient):
    first = client.post("/api/register", json={
        "username": "MinhPlayer",
        "password": "PlayerPassword123!",
    })
    assert first.status_code == 200, first.text
    client.post("/api/logout", json={})
    duplicate = client.post("/api/register", json={
        "username": "minhplayer",
        "password": "AnotherPassword123!",
    })
    assert duplicate.status_code == 409


def test_public_registration_validates_username_and_password(client: TestClient):
    short_user = client.post("/api/register", json={
        "username": "ab",
        "password": "PlayerPassword123!",
    })
    assert short_user.status_code == 422
    short_password = client.post("/api/register", json={
        "username": "valid_user",
        "password": "short",
    })
    assert short_password.status_code == 422
