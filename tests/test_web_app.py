import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main


def login(client, username, password):
    response = client.post("/api/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response


def test_owner_login_and_private_forecast(client):
    login(client, "owner_test", "OwnerPassword!234")
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["role"] == "owner"
    forecast = client.get("/api/admin/forecast")
    assert forecast.status_code == 200
    assert len(forecast.json()["items"]) == 60


def test_owner_can_create_member_but_not_second_owner(client):
    login(client, "owner_test", "OwnerPassword!234")
    created = client.post("/api/owner/accounts", json={
        "username": "member_one", "password": "MemberPassword!234",
        "display_name": "Bạn chơi", "role": "member",
    })
    assert created.status_code == 200, created.text
    assert len(created.json()["account"]["account_id"]) == 8
    denied = client.post("/api/owner/accounts", json={
        "username": "another_owner", "password": "AnotherPassword!234", "role": "owner",
    })
    assert denied.status_code == 400


def test_member_shares_market_but_cannot_pause(client):
    login(client, "owner_test", "OwnerPassword!234")
    client.post("/api/owner/accounts", json={
        "username": "member_two", "password": "MemberPassword!234", "role": "member",
    })
    client.post("/api/logout", json={})
    login(client, "member_two", "MemberPassword!234")
    owner_market = client.get("/api/market").json()["assets"]["BTC"]["price"]
    member_market = client.get("/api/market").json()["assets"]["BTC"]["price"]
    assert owner_market == member_market
    denied = client.post("/api/admin/market/pause", json={"paused": True})
    assert denied.status_code == 403


def test_owner_pause_is_global_and_persists_in_state(client):
    login(client, "owner_test", "OwnerPassword!234")
    paused = client.post("/api/admin/market/pause", json={"paused": True})
    assert paused.status_code == 200
    assert client.get("/api/market").json()["paused"] is True
    main.persist_market_state()
    with main.db() as conn:
        value = conn.execute("SELECT value FROM global_state WHERE key='market_paused'").fetchone()[0]
    assert value == "true"


def test_crypto_trade_updates_only_current_user_wallet(client):
    login(client, "owner_test", "OwnerPassword!234")
    before = client.get("/api/portfolio").json()
    result = client.post("/api/trade", json={"side": "buy", "symbol": "BTC", "quantity": 0.1})
    assert result.status_code == 200, result.text
    after = client.get("/api/portfolio").json()
    assert after["holdings"]["BTC"] == pytest.approx(0.1)
    assert after["balance_cents"] < before["balance_cents"]


def test_unauthenticated_market_is_not_public(client):
    response = client.get("/api/market")
    assert response.status_code == 401


def test_password_is_not_stored_as_plaintext(client):
    login(client, "owner_test", "OwnerPassword!234")
    with main.db() as conn:
        row = conn.execute("SELECT password_hash,salt FROM users WHERE username='owner_test'").fetchone()
    assert row["password_hash"] != "OwnerPassword!234"
    assert len(row["salt"]) >= 32


def test_login_rate_limit_after_repeated_bad_passwords(client):
    for _ in range(4):
        response = client.post("/api/login", json={"username": "owner_test", "password": "wrong-password"})
        assert response.status_code == 401
    response = client.post("/api/login", json={"username": "owner_test", "password": "wrong-password"})
    assert response.status_code == 429


def test_static_page_and_health_endpoint(client):
    assert client.get("/api/health").json()["status"] == "ok"
    page = client.get("/")
    assert page.status_code == 200
    assert "SÀN CHỨNG KHOÁN" in page.text


def test_owner_forecast_is_the_same_precomputed_path_as_market(client):
    login(client, "owner_test", "OwnerPassword!234")
    response = client.get("/api/admin/forecast")
    btc_five = next(x for x in response.json()["items"] if x["symbol"] == "BTC" and x["seconds"] == 5)
    with main.market_lock:
        expected = float(main.market_plan["BTC"][main.market_tick + 5])
    assert btc_five["price"] == expected


def test_engine_does_not_advance_while_globally_paused(client):
    login(client, "owner_test", "OwnerPassword!234")
    client.post("/api/admin/market/pause", json={"paused": True})
    with main.market_lock:
        before = dict(main.market_prices)
        tick = main.market_tick
    main.engine_step()
    with main.market_lock:
        assert main.market_prices == before
        assert main.market_tick == tick


def test_one_day_forecast_remains_available_after_market_has_run_one_day(client):
    login(client, "owner_test", "OwnerPassword!234")
    with main.market_lock:
        main.market_tick = main.MARKET_PLAN_HORIZON_SECONDS
        for sym in main.ASSETS:
            main.market_prices[sym] = float(main.market_plan[sym][main.market_tick])
        main.engine_step()  # RLock keeps the broadcaster from racing this deterministic check.
        target_index = main.market_tick + main.MARKET_PLAN_HORIZON_SECONDS
        expected = float(main.market_plan["BTC"][target_index])
        forecasts = main.admin_forecasts()
    btc_day = next(x for x in forecasts if x["symbol"] == "BTC" and x["seconds"] == 86400)
    assert btc_day["price"] == expected
    assert btc_day.get("error") is None


def test_authenticated_websocket_receives_shared_market(client):
    login(client, "owner_test", "OwnerPassword!234")
    with client.websocket_connect("/ws") as websocket:
        message = websocket.receive_json()
        assert message["type"] == "market"
        assert "BTC" in message["data"]["assets"]
