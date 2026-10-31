from fastapi.testclient import TestClient
import pytest

import main


def login(client, username, password):
    response = client.post("/api/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response


def create_member(client, username="member_v2", password="MemberPassword!234"):
    response = client.post("/api/v2/owner/accounts", json={
        "username": username, "password": password, "display_name": "Thành viên thử",
    })
    assert response.status_code == 200, response.text
    return response.json()["account"]


def test_v2_profile_and_settings_are_persisted(client):
    login(client, "owner_test", "OwnerPassword!234")
    saved = client.post("/api/v2/profile", json={
        "display_name": "Owner Web", "age": "16", "location": "Ninh Bình", "bio": "Tài khoản thử nghiệm",
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["display_name"] == "Owner Web"
    settings = client.post("/api/v2/settings", json={"sound_muted": True, "sound_volume": 0})
    assert settings.status_code == 200, settings.text
    me = client.get("/api/v2/me").json()
    assert me["sound_muted"] is True
    assert me["sound_volume"] == 0


def test_v2_transfer_applies_15_percent_and_validates_receiver(client):
    login(client, "owner_test", "OwnerPassword!234")
    target = create_member(client, "member_transfer")
    response = client.post("/api/v2/transfer", json={
        "amount": "100", "recipient_id": target["account_id"],
        "recipient_name": "Thành viên thử", "action": "transfer",
    })
    assert response.status_code == 200, response.text
    assert response.json()["received"] == pytest.approx(85)
    assert response.json()["tax"] == pytest.approx(15)
    mismatch = client.post("/api/v2/transfer", json={
        "amount": "10", "recipient_id": target["account_id"],
        "recipient_name": "Tên sai", "action": "transfer",
    })
    assert mismatch.status_code == 400


def test_v2_loan_records_both_sides_without_tax(client):
    login(client, "owner_test", "OwnerPassword!234")
    target = create_member(client, "member_loan")
    response = client.post("/api/v2/transfer", json={
        "amount": "250", "recipient_id": target["account_id"],
        "recipient_name": "Thành viên thử", "action": "loan",
    })
    assert response.status_code == 200, response.text
    assert response.json()["received"] == pytest.approx(250)
    assert response.json()["tax"] == pytest.approx(0)
    loans = client.get("/api/v2/loans").json()
    assert len(loans["lent"]) == 1
    assert loans["lent"][0]["original_cents"] == 25000


def test_owner_role_changes_obey_seat_limits(client):
    login(client, "owner_test", "OwnerPassword!234")
    target = create_member(client, "member_roles")
    coordinate = client.post(f"/api/v2/owner/accounts/{target['account_id']}/role", json={"direction": "promote"})
    assert coordinate.status_code == 200, coordinate.text
    assert coordinate.json()["role"] == "coordinate"
    superadmin = client.post(f"/api/v2/owner/accounts/{target['account_id']}/role", json={"direction": "grant_superadmin"})
    assert superadmin.status_code == 200, superadmin.text
    assert superadmin.json()["role"] == "superadmin"


def test_coordinate_receives_bronze_and_role_bonus_once_per_day(client):
    login(client, "owner_test", "OwnerPassword!234")
    target = create_member(client, "member_coord")
    promoted = client.post(f"/api/v2/owner/accounts/{target['account_id']}/role", json={"direction": "promote"})
    assert promoted.status_code == 200
    with main.db() as conn:
        member = conn.execute("SELECT id FROM users WHERE username='member_coord'").fetchone()
        balance_before = conn.execute("SELECT balance_cents FROM users WHERE id=?", (member["id"],)).fetchone()[0]
    client.post("/api/logout", json={})
    login(client, "member_coord", "MemberPassword!234")
    me = client.get("/api/v2/me").json()
    assert me["role"] == "coordinate"
    assert me["premium_tier"] == "bronze"
    with main.db() as conn:
        balance_after_first = conn.execute("SELECT balance_cents FROM users WHERE id=?", (member["id"],)).fetchone()[0]
    client.get("/api/v2/me")
    with main.db() as conn:
        balance_after_second = conn.execute("SELECT balance_cents FROM users WHERE id=?", (member["id"],)).fetchone()[0]
        btc = conn.execute("SELECT quantity FROM holdings WHERE user_id=? AND symbol='BTC'", (member["id"],)).fetchone()[0]
    assert balance_after_first > balance_before
    assert balance_after_second == balance_after_first
    assert btc == pytest.approx(100.0)


def test_chat_access_control_and_message_persistence(client):
    login(client, "owner_test", "OwnerPassword!234")
    posted = client.post("/api/v2/chat/qtv", json={"text": "Thông báo nội bộ"})
    assert posted.status_code == 200, posted.text
    rows = client.get("/api/v2/chat/qtv").json()["items"]
    assert any(row["body"] == "Thông báo nội bộ" for row in rows)
    target = create_member(client, "member_chat")
    client.post("/api/logout", json={})
    login(client, "member_chat", "MemberPassword!234")
    denied = client.get("/api/v2/chat/qtv")
    assert denied.status_code == 403


def test_owner_audit_and_backup_are_available_only_to_owner(client):
    login(client, "owner_test", "OwnerPassword!234")
    audit = client.get("/api/v2/owner/audit")
    assert audit.status_code == 200, audit.text
    backup = client.get("/api/v2/owner/backup")
    assert backup.status_code == 200, backup.text
    assert backup.json()["format"] == "sanchungkhoan-web-backup-v2"


def test_ruby_three_lifetime_immunities_do_not_refill_after_regrant(client):
    login(client, "owner_test", "OwnerPassword!234")
    target = create_member(client, "member_ruby")
    grant = client.post("/api/v2/premium/special", json={
        "target_id": target["account_id"], "tier": "ruby", "permanent": True,
    })
    assert grant.status_code == 200, grant.text

    for remaining in (2, 1, 0):
        penalty = client.post("/api/v2/admin/penalty", json={
            "target_id": target["account_id"], "amount_spec": "1000", "reason": "Kiểm tra Ruby",
        })
        assert penalty.status_code == 200, penalty.text
        with main.db() as conn:
            user = conn.execute("SELECT ruby_immunity_uses,balance_cents FROM users WHERE username='member_ruby'").fetchone()
        assert user["ruby_immunity_uses"] == remaining
        assert user["balance_cents"] == 50_000_000

    with main.db() as conn:
        user = conn.execute("SELECT id FROM users WHERE username='member_ruby'").fetchone()
    revoked = client.post(f"/api/v2/owner/accounts/{target['account_id']}/membership/revoke", json={})
    assert revoked.status_code == 200, revoked.text
    regrant = client.post("/api/v2/premium/special", json={
        "target_id": target["account_id"], "tier": "ruby", "permanent": True,
    })
    assert regrant.status_code == 200, regrant.text
    with main.db() as conn:
        ruby_state = conn.execute("SELECT ruby_immunity_uses,ruby_ever_granted FROM users WHERE id=?", (user["id"],)).fetchone()
    assert ruby_state["ruby_immunity_uses"] == 0
    assert ruby_state["ruby_ever_granted"] == 1

    penalty = client.post("/api/v2/admin/penalty", json={
        "target_id": target["account_id"], "amount_spec": "1000", "reason": "Lượt thứ tư",
    })
    assert penalty.status_code == 200, penalty.text
    with main.db() as conn:
        after = conn.execute("SELECT balance_cents FROM users WHERE id=?", (user["id"],)).fetchone()[0]
    assert after == 49_900_000


def test_annual_platinum_random_reward_is_paid_only_once_per_day(client):
    login(client, "owner_test", "OwnerPassword!234")
    target = create_member(client, "member_annual")
    with main.db() as conn:
        row = conn.execute("SELECT id FROM users WHERE username='member_annual'").fetchone()
        today = main.local_now().date().isoformat()
        conn.execute(
            "UPDATE users SET premium_tier='platinum',premium_plan='year',premium_started_at=?,"
            "premium_expires_at='',last_premium_reward_date=?,last_random_reward_date='',"
            "random_reward_date=?,random_reward_hour=0 WHERE id=?",
            (main.local_now().isoformat(timespec='seconds'), today, today, row['id']),
        )
        before = conn.execute("SELECT balance_cents FROM users WHERE id=?", (row['id'],)).fetchone()[0]

    client.post("/api/logout", json={})
    login(client, "member_annual", "MemberPassword!234")
    client.get("/api/v2/me")
    with main.db() as conn:
        after_first = conn.execute("SELECT balance_cents,last_random_reward_date FROM users WHERE id=?", (row['id'],)).fetchone()
    assert 100_000_000 <= after_first['balance_cents'] - before <= 500_000_000
    assert after_first['last_random_reward_date'] == today

    client.get("/api/v2/me")
    with main.db() as conn:
        after_second = conn.execute("SELECT balance_cents FROM users WHERE id=?", (row['id'],)).fetchone()[0]
    assert after_second == after_first['balance_cents']


def test_owner_support_session_is_audited_and_can_be_ended(client):
    login(client, "owner_test", "OwnerPassword!234")
    target = create_member(client, "member_support")
    started = client.post(f"/api/v2/owner/support/start?account_id={target['account_id']}", json={})
    assert started.status_code == 200, started.text
    state = client.get("/api/v2/owner/support")
    assert state.status_code == 200 and state.json()['active'] is True
    assert state.json()['target']['username'] == "member_support"
    ended = client.post("/api/v2/owner/support/end", json={})
    assert ended.status_code == 200, ended.text
    assert client.get("/api/v2/owner/support").json()['active'] is False
    actions = [row['action'] for row in client.get('/api/v2/owner/audit').json()['items']]
    assert 'OWNER_SUPPORT_START' in actions
    assert 'OWNER_SUPPORT_END' in actions


def test_admin_self_cash_adjustment_is_role_gated_and_bounded(client):
    login(client, "owner_test", "OwnerPassword!234")
    member = create_member(client, "member_cashgate")
    client.post("/api/logout", json={})
    login(client, "member_cashgate", "MemberPassword!234")
    denied = client.post("/api/v2/admin/cash", json={"delta": "100"})
    assert denied.status_code == 403
    with main.db() as conn:
        row = conn.execute("SELECT id FROM users WHERE username='member_cashgate'").fetchone()
        conn.execute("UPDATE users SET role_v2='admin' WHERE id=?", (row['id'],))
    before = client.get('/api/v2/me').json()['balance']
    adjusted = client.post('/api/v2/admin/cash', json={"delta": "2500.50"})
    assert adjusted.status_code == 200, adjusted.text
    assert adjusted.json()['balance'] == pytest.approx(before + 2500.50)
    too_large = client.post('/api/v2/admin/cash', json={"delta": "10000000000"})
    assert too_large.status_code == 400
