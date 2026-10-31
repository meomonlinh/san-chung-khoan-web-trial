from __future__ import annotations

import main


def test_render_environment_bootstrap_creates_single_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "trial.sqlite3")
    monkeypatch.setenv("INITIAL_OWNER_USERNAME", "trial_owner")
    monkeypatch.setenv("INITIAL_OWNER_PASSWORD", "TrialOwnerPass123!")

    main.init_db()
    assert main.bootstrap_owner_from_environment() is True
    assert main.bootstrap_owner_from_environment() is False

    with main.db() as conn:
        owner = conn.execute(
            "SELECT username, role, role_v2, password_hash, salt FROM users WHERE username=?",
            ("trial_owner",),
        ).fetchone()
    assert owner is not None
    assert owner["role"] == "owner"
    assert owner["role_v2"] == "owner"
    assert owner["password_hash"] != "TrialOwnerPass123!"
    assert len(owner["salt"]) >= 32


def test_render_environment_bootstrap_rejects_weak_password(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "trial.sqlite3")
    monkeypatch.setenv("INITIAL_OWNER_USERNAME", "trial_owner")
    monkeypatch.setenv("INITIAL_OWNER_PASSWORD", "short")
    main.init_db()
    try:
        main.bootstrap_owner_from_environment()
    except RuntimeError as exc:
        assert "12–128" in str(exc)
    else:
        raise AssertionError("Weak bootstrap password was accepted")
