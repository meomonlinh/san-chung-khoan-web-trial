"""Feature expansion for the SÀN CHỨNG KHOÁN web edition.

The desktop source is a Pygame application. This module ports its account,
market, membership, transfer, moderation, chat and Owner workflows to HTTP
endpoints while keeping all balances and authorization on the server.
"""
from __future__ import annotations

import json
import math
import random
import re
import secrets
import sqlite3
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import main as m
from fastapi import HTTPException, Request, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

ROLE_LABELS = {
    "owner": "OWNER", "superadmin": "SPADMIN", "admin": "ADMIN",
    "coordinate": "TỌA ĐỘ", "member": "THÀNH VIÊN",
}
ROLE_LIMIT_DEFAULTS = {"superadmin": 5, "admin": 10, "coordinate": 20}
ROLE_LIMIT_MAX = 1000
ROLE_LADDER = ["member", "coordinate", "admin", "superadmin"]

PREMIUM = {
    "bronze": {"label": "ĐỒNG", "prices": {"three_day": 700_000, "month": 1_000_000, "year": 5_000_000}, "daily": 100_000, "forecast": 1},
    "silver": {"label": "BẠC", "prices": {"three_day": 2_100_000, "month": 3_000_000, "year": 15_000_000}, "daily": 350_000, "forecast": 2},
    "gold": {"label": "VÀNG", "prices": {"three_day": 7_000_000, "month": 10_000_000, "year": 50_000_000}, "daily": 1_200_000, "forecast": 3},
    "platinum": {"label": "BẠCH KIM", "prices": {"three_day": 35_000_000, "month": 50_000_000, "year": 100_000_000}, "daily": 5_000_000, "forecast": 4},
    "diamond": {"label": "KIM CƯƠNG", "prices": {"three_day": 700_000_000, "month": 1_000_000_000, "year": 5_000_000_000}, "daily": 100_000_000, "forecast": 5},
    "ruby": {"label": "RUBY", "prices": {}, "daily": 100_000_000, "forecast": 3},
    "emerald": {"label": "LỤC BẢO", "prices": {}, "daily": 1_000_000_000, "forecast": 5},
}
PLAN_DAYS = {"three_day": 3, "month": 30, "year": 365}
PREMIUM_RANK = {"bronze": 0, "silver": 1, "gold": 2, "platinum": 3, "diamond": 4, "ruby": 5, "emerald": 6}


def _schema_migration() -> None:
    with m.db() as conn:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        columns = {
            "role_v2": "TEXT NOT NULL DEFAULT ''",
            "profile_age": "TEXT NOT NULL DEFAULT ''",
            "profile_location": "TEXT NOT NULL DEFAULT ''",
            "profile_bio": "TEXT NOT NULL DEFAULT ''",
            "premium_tier": "TEXT NOT NULL DEFAULT ''",
            "premium_plan": "TEXT NOT NULL DEFAULT ''",
            "premium_started_at": "TEXT NOT NULL DEFAULT ''",
            "premium_expires_at": "TEXT NOT NULL DEFAULT ''",
            "last_premium_reward_date": "TEXT NOT NULL DEFAULT ''",
            "last_random_reward_date": "TEXT NOT NULL DEFAULT ''",
            "random_reward_date": "TEXT NOT NULL DEFAULT ''",
            "random_reward_hour": "INTEGER NOT NULL DEFAULT -1",
            "ruby_forecast_seconds": "INTEGER NOT NULL DEFAULT 3",
            "ruby_immunity_uses": "INTEGER NOT NULL DEFAULT 0",
            "ruby_ever_granted": "INTEGER NOT NULL DEFAULT 0",
            "emerald_previous_json": "TEXT NOT NULL DEFAULT ''",
            "is_banned": "INTEGER NOT NULL DEFAULT 0",
            "ban_reason": "TEXT NOT NULL DEFAULT ''",
            "exchange_debt_cents": "INTEGER NOT NULL DEFAULT 0",
            "admin_request_status": "TEXT NOT NULL DEFAULT ''",
            "coordinate_promoted_at": "TEXT NOT NULL DEFAULT ''",
            "last_coordinate_bonus_date": "TEXT NOT NULL DEFAULT ''",
            "sound_muted": "INTEGER NOT NULL DEFAULT 0",
            "sound_volume": "INTEGER NOT NULL DEFAULT 25",
        }
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS role_limits(role TEXT PRIMARY KEY, limit_count INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS transfers(
            id INTEGER PRIMARY KEY AUTOINCREMENT, sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            receiver_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            amount_cents INTEGER NOT NULL, received_cents INTEGER NOT NULL, tax_cents INTEGER NOT NULL,
            kind TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS loans(
            id INTEGER PRIMARY KEY AUTOINCREMENT, lender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            borrower_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            original_cents INTEGER NOT NULL, remaining_cents INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS penalties(
            id INTEGER PRIMARY KEY AUTOINCREMENT, target_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            actor_id INTEGER NOT NULL REFERENCES users(id), amount_cents INTEGER NOT NULL,
            entered_spec TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS penalty_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT, target_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            actor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            amount_spec TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending_spadmin',
            reviewed_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL, reviewed_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS chat_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            display_name TEXT NOT NULL, role_label TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            actor_username TEXT NOT NULL, actor_role TEXT NOT NULL, action TEXT NOT NULL,
            target_username TEXT NOT NULL DEFAULT '', details TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT 'OK', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS admin_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending', note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
            reviewed_by INTEGER REFERENCES users(id), reviewed_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS moderation_reports(
            id INTEGER PRIMARY KEY AUTOINCREMENT, author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            target_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, report_type TEXT NOT NULL,
            reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS report_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT, report_id INTEGER NOT NULL REFERENCES moderation_reports(id) ON DELETE CASCADE,
            sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, body TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS emerald_lots(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            symbol TEXT NOT NULL, qty_remaining REAL NOT NULL, buy_price REAL NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS owner_support_sessions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            target_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            started_at TEXT NOT NULL, ended_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS premium_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT, requester_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            target_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, request_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, reviewed_by INTEGER REFERENCES users(id), reviewed_at TEXT NOT NULL DEFAULT ''
        );
        """)
        if m.DATABASE_URL:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower_idx ON users (LOWER(username))")
        for role, limit_count in ROLE_LIMIT_DEFAULTS.items():
            conn.execute("INSERT OR IGNORE INTO role_limits(role,limit_count) VALUES(?,?)", (role, limit_count))
        conn.execute("UPDATE users SET ruby_ever_granted=1 WHERE premium_tier='ruby' AND ruby_ever_granted=0")


_original_init_db = m.init_db

def _init_db_with_features() -> None:
    _original_init_db()
    _schema_migration()

m.init_db = _init_db_with_features


def role_of(user: sqlite3.Row | dict[str, Any]) -> str:
    try:
        value = user["role_v2"]
        if value:
            return str(value)
    except (KeyError, IndexError, TypeError):
        pass
    try:
        value = user["role"]
        return "member" if value == "member" else str(value)
    except (KeyError, IndexError, TypeError):
        return "member"


def role_label(user: sqlite3.Row | dict[str, Any]) -> str:
    return ROLE_LABELS.get(role_of(user), role_of(user).upper())


def require_user(request: Request) -> sqlite3.Row:
    user = m.get_session_user(request)
    if int(user["is_banned"] or 0):
        raise HTTPException(403, "Tài khoản đang bị khóa.")
    return user


def require_roles(request: Request, roles: set[str]) -> sqlite3.Row:
    user = require_user(request)
    if role_of(user) not in roles:
        raise HTTPException(403, "Vai trò hiện tại không có quyền sử dụng chức năng này.")
    return user


def _now() -> str:
    return m.local_now().isoformat(timespec="seconds")


def _money_cents(value: Any) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, "Số tiền không hợp lệ.")
    if not amount.is_finite() or amount <= 0 or amount > Decimal("999999999999999"):
        raise HTTPException(400, "Số tiền phải hữu hạn và lớn hơn 0.")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _norm_name(value: str) -> str:
    import unicodedata
    raw = unicodedata.normalize("NFKD", str(value or "").casefold())
    return " ".join("".join(c for c in raw if not unicodedata.combining(c)).split())


def _audit(conn: sqlite3.Connection, actor: sqlite3.Row, action: str,
           target: str = "", details: str = "", result: str = "OK") -> None:
    conn.execute(
        "INSERT INTO audit_events(actor_id,actor_username,actor_role,action,target_username,details,result,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (actor["id"], actor["username"], role_label(actor), action[:100], target[:80], details[:1000], result[:40], _now()),
    )


def _notify(conn: sqlite3.Connection, user_id: int, title: str, body: str) -> None:
    conn.execute("INSERT INTO inbox(user_id,title,body,created_at,is_read) VALUES(?,?,?,?,0)",
                 (user_id, title[:120], body[:1500], m.utc_now().isoformat()))


def _get_by_account_id(conn: sqlite3.Connection, account_id: str) -> sqlite3.Row | None:
    if not re.fullmatch(r"\d{8}", str(account_id or "")):
        return None
    return conn.execute("SELECT * FROM users WHERE account_id=?", (str(account_id),)).fetchone()


def _premium_tier(conn: sqlite3.Connection, user: sqlite3.Row) -> str | None:
    tier = str(user["premium_tier"] or "")
    if not tier:
        # Desktop rule: TỌA ĐỘ receives Bronze at no cost while holding the role.
        return "bronze" if role_of(user) == "coordinate" else None
    expiry = str(user["premium_expires_at"] or "")
    now = m.local_now()
    if expiry:
        try:
            exp = m.datetime.fromisoformat(expiry)
        except ValueError:
            exp = None
        if exp and now >= exp:
            if tier == "emerald" and user["emerald_previous_json"]:
                try:
                    previous = json.loads(user["emerald_previous_json"])
                except Exception:
                    previous = {}
                prev_tier = previous.get("premium_tier", previous.get("tier", ""))
                prev_expiry = previous.get("premium_expires_at", previous.get("expires_at", ""))
                active_prev = bool(prev_tier)
                if prev_expiry:
                    try:
                        active_prev = m.datetime.fromisoformat(prev_expiry) > now
                    except ValueError:
                        active_prev = False
                if active_prev:
                    conn.execute("UPDATE users SET premium_tier=?,premium_plan=?,premium_started_at=?,premium_expires_at=?,emerald_previous_json='' WHERE id=?",
                                 (prev_tier, previous.get("premium_plan", previous.get("plan", "")), previous.get("premium_started_at", previous.get("started_at", "")), prev_expiry, user["id"]))
                else:
                    conn.execute("UPDATE users SET premium_tier='',premium_plan='',premium_started_at='',premium_expires_at='',emerald_previous_json='' WHERE id=?", (user["id"],))
                conn.execute("DELETE FROM emerald_lots WHERE user_id=?", (user["id"],))
                return prev_tier if active_prev else None
            conn.execute("UPDATE users SET premium_tier='',premium_plan='',premium_started_at='',premium_expires_at='' WHERE id=?", (user["id"],))
            return None
    return tier if tier in PREMIUM else None


def _award_daily_rewards(conn: sqlite3.Connection, user_id: int) -> int:
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user or int(user["disabled"] or 0) or int(user["is_banned"] or 0):
        return 0
    today = m.local_now().date()
    role = role_of(user)
    tier = _premium_tier(conn, user)
    total = 0
    if tier:
        last = str(user["last_premium_reward_date"] or "")
        try:
            last_date = m.datetime.strptime(last, "%Y-%m-%d").date() if last else today - timedelta(days=1)
        except ValueError:
            last_date = today - timedelta(days=1)
        start = str(user["premium_started_at"] or "")
        if start:
            try:
                last_date = max(last_date, m.datetime.fromisoformat(start).date() - timedelta(days=1))
            except ValueError:
                pass
        days_due = max(0, min(365, (today - last_date).days))
        if days_due:
            base = int(PREMIUM[tier]["daily"])
            if user["premium_plan"] == "year" and tier in ("bronze", "silver", "gold", "platinum", "diamond"):
                base = int(round(base * 1.20))
            # All balances are stored in cents; Premium catalog values are USD.
            bonus_cents = base * days_due * 100
            current_balance = int(user["balance_cents"])
            conn.execute("UPDATE users SET balance_cents=?,last_premium_reward_date=? WHERE id=?",
                         (current_balance + bonus_cents, today.isoformat(), user_id))
            conn.execute("INSERT INTO trades(user_id,side,symbol,quantity,price,total_cents,created_at) VALUES(?,?,?,?,?,?,?)",
                         (user_id, "reward", "USD", base * days_due, 1.0, bonus_cents, m.utc_now().isoformat()))
            _notify(conn, user_id, "Thưởng Premium", f"Đã cộng ${base * days_due:,.0f} USD ảo cho {days_due} ngày Premium.")
            total += bonus_cents
    if role == "coordinate":
        last = str(user["last_coordinate_bonus_date"] or "")
        try:
            last_date = m.datetime.strptime(last, "%Y-%m-%d").date() if last else today - timedelta(days=1)
        except ValueError:
            last_date = today - timedelta(days=1)
        promoted = str(user["coordinate_promoted_at"] or "")
        if promoted:
            try:
                last_date = max(last_date, m.datetime.fromisoformat(promoted).date() - timedelta(days=1))
            except ValueError:
                pass
        days_due = max(0, min(365, (today - last_date).days))
        if days_due:
            bonus_cents = 100_000 * days_due * 100
            current = conn.execute("SELECT balance_cents FROM users WHERE id=?", (user_id,)).fetchone()[0]
            conn.execute("UPDATE users SET balance_cents=?,last_coordinate_bonus_date=? WHERE id=?",
                         (int(current) + bonus_cents, today.isoformat(), user_id))
            btc = conn.execute("SELECT quantity FROM holdings WHERE user_id=? AND symbol='BTC'", (user_id,)).fetchone()
            new_btc = round(float(btc[0] if btc else 0) + 100.0 * days_due, 6)
            conn.execute("UPDATE holdings SET quantity=? WHERE user_id=? AND symbol='BTC'", (new_btc, user_id))
            _notify(conn, user_id, "Thưởng TỌA ĐỘ", f"Đã cộng ${100_000 * days_due:,.0f} USD và {100 * days_due:,.0f} BTC ảo.")
            total += bonus_cents
    # Platinum/Diamond annual plans receive one random 1–5M reward per day after a scheduled hour.
    if tier in ("platinum", "diamond") and user["premium_plan"] == "year":
        # Persist the schedule separately from the claim marker so repeated /me
        # requests cannot pay the random daily reward more than once.
        if str(user["last_random_reward_date"] or "") != today.isoformat():
            schedule_date = str(user["random_reward_date"] or "")
            hour = int(user["random_reward_hour"])
            if schedule_date != today.isoformat() or hour < 0 or hour > 23:
                hour = random.randint(0, 23)
                conn.execute("UPDATE users SET random_reward_date=?,random_reward_hour=? WHERE id=?",
                             (today.isoformat(), hour, user_id))
            if m.local_now().hour >= hour:
                bonus = random.randint(1, 5) * 1_000_000
                bonus_cents = bonus * 100
                current = conn.execute("SELECT balance_cents FROM users WHERE id=?", (user_id,)).fetchone()[0]
                conn.execute("UPDATE users SET balance_cents=?,last_random_reward_date=? WHERE id=?",
                             (int(current) + bonus_cents, today.isoformat(), user_id))
                _notify(conn, user_id, "Thưởng Premium ngẫu nhiên", f"Bạn nhận thêm ${bonus:,.0f} USD ảo.")
                total += bonus_cents
    return total


def _full_user(conn: sqlite3.Connection, user: sqlite3.Row) -> dict[str, Any]:
    _award_daily_rewards(conn, user["id"])
    refreshed = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    tier = _premium_tier(conn, refreshed)
    role = role_of(refreshed)
    return {
        "username": refreshed["username"], "display_name": refreshed["display_name"],
        "account_id": refreshed["account_id"], "role": role, "role_label": ROLE_LABELS.get(role, role.upper()),
        "balance": int(refreshed["balance_cents"]) / 100, "balance_cents": int(refreshed["balance_cents"]),
        "profile_age": refreshed["profile_age"], "profile_location": refreshed["profile_location"],
        "profile_bio": refreshed["profile_bio"], "premium_tier": tier,
        "premium_label": PREMIUM[tier]["label"] if tier else "Không có thành viên",
        "premium_plan": refreshed["premium_plan"], "premium_expires_at": refreshed["premium_expires_at"],
        "exchange_debt": int(refreshed["exchange_debt_cents"] or 0) / 100,
        "is_banned": bool(refreshed["is_banned"]), "sound_muted": bool(refreshed["sound_muted"]),
        "sound_volume": int(refreshed["sound_volume"]),
        "ruby_immunity_uses": int(refreshed["ruby_immunity_uses"] or 0),
    }


# Replace public_user so the existing login endpoint also exposes the richer role fields.
def _public_user_extended(user: sqlite3.Row) -> dict[str, Any]:
    with m.db() as conn:
        return _full_user(conn, user)

m.public_user = _public_user_extended


class TransferBody(BaseModel):
    amount: Decimal
    recipient_id: str = Field(min_length=8, max_length=8)
    recipient_name: str = Field(min_length=1, max_length=80)
    action: str = "transfer"

class PremiumPurchaseBody(BaseModel):
    tier: str
    plan: str

class PremiumGiftBody(BaseModel):
    recipient_id: str = Field(min_length=8, max_length=8)
    recipient_name: str = Field(min_length=1, max_length=80)
    tier: str
    plan: str = "month"

class OwnerCreateBody(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(default="", max_length=50)

class RoleActionBody(BaseModel):
    direction: str

class RoleLimitsBody(BaseModel):
    limits: dict[str, int]

class PenaltyBody(BaseModel):
    target_id: str = Field(min_length=8, max_length=8)
    amount_spec: str = Field(min_length=1, max_length=110)
    reason: str = Field(default="Vi phạm quy định sàn giả lập", max_length=500)

class CashAdjustBody(BaseModel):
    delta: Decimal

class BanBody(BaseModel):
    target_id: str = Field(min_length=8, max_length=8)
    target_name: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=8, max_length=500)
    banned: bool = True

class ProfileBody(BaseModel):
    display_name: str = Field(min_length=1, max_length=50)
    age: str = Field(default="", max_length=3)
    location: str = Field(default="", max_length=100)
    bio: str = Field(default="", max_length=160)

class SettingsBody(BaseModel):
    sound_muted: bool
    sound_volume: int = Field(ge=0, le=100)

class ChatBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)

class ReportBody(BaseModel):
    target_id: str = Field(min_length=8, max_length=8)
    target_name: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=8, max_length=500)
    request_ban: bool = False

class ReportMessageBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)

class ReviewBody(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=500)

class GrantMembershipBody(BaseModel):
    target_id: str = Field(min_length=8, max_length=8)
    tier: str
    permanent: bool = False

class ReviewPremiumBody(BaseModel):
    approve: bool


@m.app.get("/api/v2/meta")
async def v2_meta(request: Request) -> dict[str, Any]:
    user = require_user(request)
    with m.db() as conn:
        caps = {r["role"]: int(r["limit_count"]) for r in conn.execute("SELECT role,limit_count FROM role_limits")}
    role = role_of(user)
    return {"app_name": "SÀN CHỨNG KHOÁN", "version": "0.2.2", "role": role,
            "premium": PREMIUM, "plans": {"three_day": 3, "month": 30, "year": 365},
            "role_limits": caps, "can_admin": role in {"owner", "superadmin", "admin"},
            "can_moderate": role in {"owner", "superadmin", "admin", "coordinate"}}


@m.app.get("/api/v2/me")
async def v2_me(request: Request) -> dict[str, Any]:
    user = require_user(request)
    with m.db() as conn:
        result = _full_user(conn, user)
    return result


@m.app.post("/api/v2/profile")
async def v2_profile(body: ProfileBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    user = require_user(request)
    age = body.age.strip()
    if age and (not age.isdigit() or not 1 <= int(age) <= 120):
        raise HTTPException(400, "Tuổi phải là số từ 1 đến 120.")
    with m.db() as conn:
        conn.execute("UPDATE users SET display_name=?,profile_age=?,profile_location=?,profile_bio=? WHERE id=?",
                     (body.display_name.strip(), age, body.location.strip(), body.bio.strip(), user["id"]))
        _audit(conn, user, "UPDATE_PROFILE", details="Cập nhật hồ sơ cá nhân")
        updated = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        return _full_user(conn, updated)


@m.app.post("/api/v2/settings")
async def v2_settings(body: SettingsBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    user = require_user(request)
    with m.db() as conn:
        conn.execute("UPDATE users SET sound_muted=?,sound_volume=? WHERE id=?",
                     (int(body.sound_muted), body.sound_volume, user["id"]))
    return {"ok": True, "sound_muted": body.sound_muted, "sound_volume": body.sound_volume}


@m.app.get("/api/v2/forecast")
async def v2_forecast(request: Request, seconds: int = Query(default=3)) -> dict[str, Any]:
    user = require_user(request)
    role = role_of(user)
    tier = _premium_tier_for_request(user)
    if tier == "ruby":
        if seconds not in (1, 3, 5):
            raise HTTPException(400, "RUBY chỉ chọn dự báo 1, 3 hoặc 5 giây.")
        allowed_seconds = seconds
    else:
        allowed_seconds = 5 if role in ("admin", "superadmin") else 5 if tier == "emerald" else PREMIUM[tier]["forecast"] if tier else 0
    if not allowed_seconds:
        raise HTTPException(403, "Gói thành viên hiện tại chưa mở dự báo.")
    with m.market_lock:
        rows = []
        for sym in m.ASSETS:
            arr = m.market_plan.get(sym)
            if m.market_paused:
                rows.append({"symbol": sym, "seconds": allowed_seconds, "paused": True})
            elif arr is None or m.market_tick + allowed_seconds >= len(arr):
                rows.append({"symbol": sym, "seconds": allowed_seconds, "error": "Đang cập nhật lịch giá."})
            else:
                current = m.market_prices[sym]
                future = float(arr[m.market_tick + allowed_seconds])
                rows.append({"symbol": sym, "seconds": allowed_seconds, "price": future,
                             "change_pct": ((future / current) - 1) * 100 if current else 0})
    return {"seconds": allowed_seconds, "items": rows}


def _premium_tier_for_request(user: sqlite3.Row) -> str | None:
    with m.db() as conn:
        refreshed = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        return _premium_tier(conn, refreshed)


@m.app.post("/api/v2/trade")
async def v2_trade(body: m.TradeBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    user = require_user(request)
    if body.side not in {"buy", "sell"} or body.symbol not in m.ASSETS or not math.isfinite(body.quantity):
        raise HTTPException(400, "Lệnh giao dịch không hợp lệ.")
    qty = round(float(body.quantity), 6)
    if qty <= 0:
        raise HTTPException(400, "Khối lượng phải lớn hơn 0.")
    with m.market_lock:
        price = m.market_prices[body.symbol]
        opened = m.is_market_open(body.symbol, m.local_now())
        paused = m.market_paused
    if paused:
        raise HTTPException(409, "Toàn sàn đang tạm dừng.")
    if not opened:
        raise HTTPException(409, "Tài sản truyền thống đang nghỉ giao dịch.")
    total_cents = int((Decimal(str(price)) * Decimal(str(qty)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if total_cents <= 0:
        raise HTTPException(400, "Giá trị lệnh quá nhỏ.")
    now = m.utc_now().isoformat()
    with m.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        refreshed = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        if int(refreshed["is_banned"] or 0):
            raise HTTPException(403, "Tài khoản đang bị khóa.")
        holding = conn.execute("SELECT quantity FROM holdings WHERE user_id=? AND symbol=?", (user["id"], body.symbol)).fetchone()
        current_qty = float(holding[0]) if holding else 0.0
        if body.side == "buy":
            if int(refreshed["balance_cents"]) < total_cents:
                raise HTTPException(409, "Số dư USD ảo không đủ.")
            conn.execute("UPDATE users SET balance_cents=balance_cents-? WHERE id=?", (total_cents, user["id"]))
            new_qty = round(current_qty + qty, 6)
            if _premium_tier(conn, refreshed) == "emerald":
                conn.execute("INSERT INTO emerald_lots(user_id,symbol,qty_remaining,buy_price,created_at) VALUES(?,?,?,?,?)",
                             (user["id"], body.symbol, qty, price, now))
        else:
            if current_qty + 1e-9 < qty:
                raise HTTPException(409, "Không đủ tài sản để bán.")
            revenue = total_cents
            refund_cents = 0
            if _premium_tier(conn, refreshed) == "emerald":
                uninsured = current_qty - sum(float(r[0]) for r in conn.execute("SELECT qty_remaining FROM emerald_lots WHERE user_id=? AND symbol=?", (user["id"], body.symbol)))
                remain = max(0.0, qty - max(0.0, uninsured))
                lots = conn.execute("SELECT id,qty_remaining,buy_price FROM emerald_lots WHERE user_id=? AND symbol=? ORDER BY id", (user["id"], body.symbol)).fetchall()
                for lot in lots:
                    if remain <= 1e-9:
                        break
                    take = min(remain, float(lot["qty_remaining"]))
                    if price < float(lot["buy_price"]):
                        loss = (float(lot["buy_price"]) - price) * take
                        refund_cents += int((Decimal(str(loss * 0.8)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                    left = float(lot["qty_remaining"]) - take
                    if left <= 1e-9:
                        conn.execute("DELETE FROM emerald_lots WHERE id=?", (lot["id"],))
                    else:
                        conn.execute("UPDATE emerald_lots SET qty_remaining=? WHERE id=?", (left, lot["id"]))
                    remain -= take
            conn.execute("UPDATE users SET balance_cents=balance_cents+? WHERE id=?", (revenue + refund_cents, user["id"]))
            new_qty = round(max(0.0, current_qty - qty), 6)
            total_cents = revenue + refund_cents
        conn.execute("UPDATE holdings SET quantity=? WHERE user_id=? AND symbol=?", (new_qty, user["id"], body.symbol))
        conn.execute("INSERT INTO trades(user_id,side,symbol,quantity,price,total_cents,created_at) VALUES(?,?,?,?,?,?,?)",
                     (user["id"], body.side, body.symbol, qty, price, total_cents, now))
        if body.side == "sell" and 'refund_cents' in locals() and refund_cents:
            _notify(conn, user["id"], "Bồi hoàn LỤC BẢO", f"Đã bồi hoàn ${refund_cents/100:,.2f} USD, tương đương 80% khoản lỗ thực hiện trên phần tài sản được Lục Bảo bảo vệ.")
            _audit(conn, user, "EMERALD_LOSS_REFUND", details=f"{body.symbol} bán {qty}; hoàn ${refund_cents/100:,.2f}")
        conn.execute("COMMIT")
    return {"ok": True, "side": body.side, "symbol": body.symbol, "quantity": qty, "price": price,
            "total": total_cents / 100, "message": "Mua thành công." if body.side == "buy" else "Bán thành công."}


@m.app.get("/api/v2/loans")
async def v2_loans(request: Request) -> dict[str, Any]:
    user = require_user(request)
    with m.db() as conn:
        borrowed = conn.execute("SELECT l.*,u.username AS lender_username,u.display_name AS lender_name,u.account_id AS lender_account_id FROM loans l JOIN users u ON u.id=l.lender_id WHERE l.borrower_id=? ORDER BY l.id DESC", (user["id"],)).fetchall()
        lent = conn.execute("SELECT l.*,u.username AS borrower_username,u.display_name AS borrower_name,u.account_id AS borrower_account_id FROM loans l JOIN users u ON u.id=l.borrower_id WHERE l.lender_id=? ORDER BY l.id DESC", (user["id"],)).fetchall()
    return {"borrowed": [dict(x) for x in borrowed], "lent": [dict(x) for x in lent]}


@m.app.post("/api/v2/transfer")
async def v2_transfer(body: TransferBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    sender = require_user(request)
    if body.action not in {"transfer", "loan"}:
        raise HTTPException(400, "Chọn Chuyển tiền hoặc Cho nợ.")
    amount_cents = _money_cents(body.amount)
    with m.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        src = conn.execute("SELECT * FROM users WHERE id=?", (sender["id"],)).fetchone()
        target = _get_by_account_id(conn, body.recipient_id)
        if not target or target["id"] == src["id"]:
            raise HTTPException(400, "Không tìm thấy người nhận hoặc không thể tự chuyển cho mình.")
        if int(target["is_banned"] or 0) or int(target["disabled"] or 0):
            raise HTTPException(409, "Không thể chuyển tiền cho tài khoản đang bị khóa.")
        if _norm_name(body.recipient_name) != _norm_name(target["display_name"]):
            raise HTTPException(400, "Tên hiển thị không khớp ID người nhận.")
        if int(src["balance_cents"]) < amount_cents:
            raise HTTPException(409, "Số dư không đủ.")
        if body.action == "loan":
            received = amount_cents
            tax = 0
        else:
            received = int((Decimal(amount_cents) * Decimal("0.85")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            tax = amount_cents - received
        now = m.utc_now().isoformat()
        conn.execute("UPDATE users SET balance_cents=balance_cents-? WHERE id=?", (amount_cents, src["id"]))
        conn.execute("UPDATE users SET balance_cents=balance_cents+? WHERE id=?", (received, target["id"]))
        conn.execute("INSERT INTO transfers(sender_id,receiver_id,amount_cents,received_cents,tax_cents,kind,created_at) VALUES(?,?,?,?,?,?,?)",
                     (src["id"], target["id"], amount_cents, received, tax, body.action, now))
        if body.action == "loan":
            conn.execute("INSERT INTO loans(lender_id,borrower_id,original_cents,remaining_cents,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                         (src["id"], target["id"], amount_cents, amount_cents, "open", now, now))
        conn.execute("INSERT INTO trades(user_id,side,symbol,quantity,price,total_cents,created_at) VALUES(?,?,?,?,?,?,?)",
                     (src["id"], body.action, "USD", amount_cents/100, 1, amount_cents, now))
        conn.execute("INSERT INTO trades(user_id,side,symbol,quantity,price,total_cents,created_at) VALUES(?,?,?,?,?,?,?)",
                     (target["id"], "received", "USD", received/100, 1, received, now))
        _notify(conn, target["id"], "Bạn nhận được khoản cho nợ" if body.action == "loan" else "Bạn nhận được tiền",
                f"Người gửi: {src['display_name']} · ID {src['account_id']}\nGửi: ${amount_cents/100:,.2f}\nThực nhận: ${received/100:,.2f}\nPhí bị xóa: ${tax/100:,.2f} USD.")
        _notify(conn, src["id"], "Đã cho nợ thành công" if body.action == "loan" else "Đã chuyển tiền thành công",
                f"Người nhận: {target['display_name']} · ID {target['account_id']}\nĐã gửi: ${amount_cents/100:,.2f}\nNgười nhận thực nhận: ${received/100:,.2f}\nPhí bị xóa: ${tax/100:,.2f} USD.")
        _audit(conn, src, "LOAN_RECORDED" if body.action == "loan" else "TRANSFER", target["username"], f"Gửi ${amount_cents/100:,.2f}; nhận ${received/100:,.2f}; phí ${tax/100:,.2f}")
        conn.execute("COMMIT")
    return {"ok": True, "amount": amount_cents/100, "received": received/100, "tax": tax/100, "action": body.action}


def _premium_cost(tier: str, plan: str, role: str) -> int:
    if tier not in ("bronze", "silver", "gold", "platinum", "diamond") or plan not in PLAN_DAYS:
        raise HTTPException(400, "Gói Premium hoặc thời hạn không hợp lệ.")
    if role == "admin" or (role == "coordinate" and tier == "bronze"):
        return 0
    return int(PREMIUM[tier]["prices"][plan] * 100)


@m.app.get("/api/v2/premium")
async def v2_premium(request: Request) -> dict[str, Any]:
    user = require_user(request)
    with m.db() as conn:
        fresh = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        tier = _premium_tier(conn, fresh)
        active = {"tier": tier, "label": PREMIUM[tier]["label"] if tier else "Không có", "plan": fresh["premium_plan"], "expires_at": fresh["premium_expires_at"]}
    return {"catalog": PREMIUM, "plans": PLAN_DAYS, "active": active,
            "ruby_can_request": role_of(user) == "superadmin", "emerald_visible": role_of(user) == "owner"}


@m.app.post("/api/v2/premium/purchase")
async def v2_premium_purchase(body: PremiumPurchaseBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    user = require_user(request)
    if body.tier not in PREMIUM or body.tier in {"ruby", "emerald"} or body.plan not in PLAN_DAYS:
        raise HTTPException(400, "RUBY/LỤC BẢO là gói đặc biệt, không thể tự mua.")
    with m.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        fresh = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        current = _premium_tier(conn, fresh)
        if current == "ruby":
            raise HTTPException(409, "RUBY là gói vĩnh viễn; chỉ Owner có thể thu hồi.")
        if current and PREMIUM_RANK.get(body.tier, -1) < PREMIUM_RANK.get(current, -1):
            raise HTTPException(409, "Không thể mua cấp thấp hơn gói hiện tại.")
        cost = _premium_cost(body.tier, body.plan, role_of(fresh))
        if int(fresh["balance_cents"]) < cost:
            raise HTTPException(409, f"Chưa đủ USD. Cần ${cost/100:,.2f}.")
        now = m.local_now()
        expiry = now + timedelta(days=PLAN_DAYS[body.plan])
        if current == body.tier and fresh["premium_plan"] == body.plan and fresh["premium_expires_at"]:
            try:
                existing_exp = m.datetime.fromisoformat(fresh["premium_expires_at"])
                if existing_exp > now:
                    expiry = existing_exp + timedelta(days=PLAN_DAYS[body.plan])
            except ValueError:
                pass
        conn.execute("UPDATE users SET balance_cents=balance_cents-?,premium_tier=?,premium_plan=?,premium_started_at=?,premium_expires_at=?,last_premium_reward_date=? WHERE id=?",
                     (cost, body.tier, body.plan, now.isoformat(timespec="seconds"), expiry.isoformat(timespec="seconds"), (now.date()-timedelta(days=1)).isoformat() if not current else fresh["last_premium_reward_date"], user["id"]))
        conn.execute("INSERT INTO trades(user_id,side,symbol,quantity,price,total_cents,created_at) VALUES(?,?,?,?,?,?,?)",
                     (user["id"], "premium", "USD", cost/100, 1, cost, m.utc_now().isoformat()))
        _notify(conn, user["id"], "Kích hoạt Premium", f"Đã kích hoạt {PREMIUM[body.tier]['label']} · {body.plan} trong {PLAN_DAYS[body.plan]} ngày. Giá: ${cost/100:,.2f}.")
        _audit(conn, user, "PREMIUM_PURCHASE", details=f"{body.tier}/{body.plan}; ${cost/100:,.2f}")
        conn.execute("COMMIT")
    with m.db() as conn:
        fresh = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        _award_daily_rewards(conn, user["id"])
        return {"ok": True, "active": _full_user(conn, fresh)}


@m.app.post("/api/v2/premium/gift")
async def v2_premium_gift(body: PremiumGiftBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    sender = require_user(request)
    if body.tier in {"ruby", "emerald"}:
        raise HTTPException(403, "RUBY/LỤC BẢO dùng quy trình cấp đặc biệt của Owner/SPADMIN.")
    if body.tier not in PREMIUM or body.plan not in PLAN_DAYS:
        raise HTTPException(400, "Gói tặng không hợp lệ.")
    with m.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        target = _get_by_account_id(conn, body.recipient_id)
        if not target or target["id"] == sender["id"] or int(target["is_banned"] or 0):
            raise HTTPException(400, "Người nhận không hợp lệ.")
        if _norm_name(body.recipient_name) != _norm_name(target["display_name"]):
            raise HTTPException(400, "Tên hiển thị không khớp ID.")
        current_tier = _premium_tier(conn, target)
        if current_tier == "ruby":
            raise HTTPException(409, "RUBY là gói vĩnh viễn; không thể ghi đè bằng gói thường.")
        if current_tier and PREMIUM_RANK.get(body.tier, -1) < PREMIUM_RANK.get(current_tier, -1):
            raise HTTPException(409, "Không thể tặng cấp thấp hơn gói người nhận đang có.")
        cost = int(PREMIUM[body.tier]["prices"][body.plan] * 100)
        surcharge = int((Decimal(cost) * Decimal("0.15")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        total = cost + surcharge
        if int(sender["balance_cents"]) < total:
            raise HTTPException(409, f"Cần ${total/100:,.2f} (đã gồm phụ phí 15%).")
        now = m.local_now()
        expiry = now + timedelta(days=PLAN_DAYS[body.plan])
        if current_tier == body.tier and target["premium_plan"] == body.plan and target["premium_expires_at"]:
            try:
                current_expiry = m.datetime.fromisoformat(target["premium_expires_at"])
                if current_expiry > now:
                    expiry = current_expiry + timedelta(days=PLAN_DAYS[body.plan])
            except ValueError:
                pass
        conn.execute("UPDATE users SET balance_cents=balance_cents-? WHERE id=?", (total, sender["id"]))
        conn.execute("UPDATE users SET premium_tier=?,premium_plan=?,premium_started_at=?,premium_expires_at=?,last_premium_reward_date=? WHERE id=?",
                     (body.tier, body.plan, now.isoformat(timespec="seconds"), expiry.isoformat(timespec="seconds"), (now.date()-timedelta(days=1)).isoformat(), target["id"]))
        _notify(conn, target["id"], "Bạn được tặng gói Premium", f"Người tặng: {sender['display_name']} · ID {sender['account_id']}\nGói: {PREMIUM[body.tier]['label']} · {body.plan}. Người nhận không bị trừ tiền.")
        _notify(conn, sender["id"], "Đã tặng Premium", f"Người nhận: {target['display_name']} · ID {target['account_id']}\nGiá gói ${cost/100:,.2f} + phụ phí 15% ${surcharge/100:,.2f} = ${total/100:,.2f}.")
        _audit(conn, sender, "PREMIUM_GIFT", target["username"], f"{body.tier}/{body.plan}; total ${total/100:,.2f}")
        conn.execute("COMMIT")
    return {"ok": True, "cost": cost/100, "surcharge": surcharge/100, "total": total/100}


@m.app.post("/api/v2/premium/special")
async def v2_premium_special(body: GrantMembershipBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    actor = require_roles(request, {"owner", "superadmin"})
    if body.tier not in {"ruby", "emerald"}:
        raise HTTPException(400, "Chỉ hỗ trợ RUBY hoặc LỤC BẢO ở đây.")
    with m.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        target = _get_by_account_id(conn, body.target_id)
        if not target or int(target["is_banned"] or 0):
            raise HTTPException(404, "Không tìm thấy tài khoản nhận hợp lệ.")
        if body.tier == "emerald" and role_of(actor) != "owner":
            raise HTTPException(403, "Chỉ Owner được cấp LỤC BẢO.")
        if body.tier == "emerald":
            if body.permanent and target["id"] != actor["id"]:
                raise HTTPException(403, "LỤC BẢO vĩnh viễn chỉ dành cho Owner.")
            previous = {k: target[k] for k in ("premium_tier", "premium_plan", "premium_started_at", "premium_expires_at")}
            previous_json = json.dumps(previous) if not body.permanent else ""
            expiry = "" if body.permanent else (m.local_now()+timedelta(days=1)).isoformat(timespec="seconds")
            conn.execute("UPDATE users SET premium_tier='emerald',premium_plan=?,premium_started_at=?,premium_expires_at=?,emerald_previous_json=?,last_premium_reward_date=?,balance_cents=balance_cents+100000000000 WHERE id=?",
                         ("permanent" if body.permanent else "one_day", _now(), expiry, previous_json, m.local_now().date().isoformat(), target["id"]))
            conn.execute("DELETE FROM emerald_lots WHERE user_id=?", (target["id"],))
            _notify(conn, target["id"], "Được cấp TV LỤC BẢO", "Thưởng ngày đầu +1.000.000.000 USD. Lệnh mua khi gói còn hiệu lực được bảo vệ 80% phần lỗ đã thực hiện khi bán.")
        else:
            if role_of(actor) != "owner":
                raise HTTPException(403, "SPADMIN không thể tự cấp RUBY; hãy gửi yêu cầu Owner duyệt.")
            if not int(target["ruby_ever_granted"] or 0):
                conn.execute("UPDATE users SET ruby_immunity_uses=3,ruby_ever_granted=1 WHERE id=?", (target["id"],))
            conn.execute("UPDATE users SET premium_tier='ruby',premium_plan='permanent',premium_started_at=?,premium_expires_at='',ruby_forecast_seconds=3 WHERE id=?",
                         (_now(), target["id"]))
            remaining = int(conn.execute("SELECT ruby_immunity_uses FROM users WHERE id=?", (target["id"],)).fetchone()[0])
            _notify(conn, target["id"], "Được cấp TV RUBY", f"RUBY vĩnh viễn: dự báo có thể chọn 1/3/5 giây và còn {remaining}/3 lượt miễn phạt trọn đời.")
        _audit(conn, actor, "GRANT_SPECIAL_MEMBERSHIP", target["username"], body.tier)
        conn.execute("COMMIT")
    return {"ok": True, "message": f"Đã cấp {body.tier.upper()} cho tài khoản đích."}


@m.app.post("/api/v2/premium/ruby-request")
async def v2_ruby_request(body: GrantMembershipBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    actor = require_roles(request, {"superadmin"})
    if body.tier != "ruby":
        raise HTTPException(400, "Yêu cầu này chỉ dùng cho RUBY.")
    with m.db() as conn:
        target = _get_by_account_id(conn, body.target_id)
        if not target or target["id"] == actor["id"]:
            raise HTTPException(400, "Tài khoản nhận không hợp lệ.")
        cur = conn.execute("INSERT INTO premium_requests(requester_id,target_id,request_type,status,created_at) VALUES(?,?,?,?,?)",
                           (actor["id"], target["id"], "ruby_gift", "pending", _now()))
        _notify(conn, target["id"], "Yêu cầu RUBY đang chờ duyệt", "SPADMIN đã gửi yêu cầu tặng RUBY; Owner cần phê duyệt.")
        _audit(conn, actor, "REQUEST_RUBY_GIFT", target["username"], f"request_id={cur.lastrowid}")
    return {"ok": True, "request_id": cur.lastrowid}


@m.app.get("/api/v2/owner/premium-requests")
async def v2_premium_requests(request: Request) -> dict[str, Any]:
    owner = require_roles(request, {"owner"})
    with m.db() as conn:
        rows = conn.execute("SELECT p.*,a.username AS requester_username,a.display_name AS requester_name,t.username AS target_username,t.display_name AS target_name,t.account_id AS target_id FROM premium_requests p JOIN users a ON a.id=p.requester_id JOIN users t ON t.id=p.target_id WHERE p.status='pending' ORDER BY p.id DESC").fetchall()
    return {"items": [dict(x) for x in rows]}


@m.app.post("/api/v2/owner/premium-requests/{request_id}/review")
async def v2_review_premium_request(request_id: int, body: ReviewPremiumBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    owner = require_roles(request, {"owner"})
    with m.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        req = conn.execute("SELECT * FROM premium_requests WHERE id=? AND status='pending'", (request_id,)).fetchone()
        if not req:
            raise HTTPException(404, "Yêu cầu không còn chờ duyệt.")
        target = conn.execute("SELECT * FROM users WHERE id=?", (req["target_id"],)).fetchone()
        if body.approve and req["request_type"] == "ruby_gift":
            if not int(target["ruby_ever_granted"] or 0):
                conn.execute("UPDATE users SET ruby_immunity_uses=3,ruby_ever_granted=1 WHERE id=?", (target["id"],))
            conn.execute("UPDATE users SET premium_tier='ruby',premium_plan='permanent',premium_started_at=?,premium_expires_at='',ruby_forecast_seconds=3 WHERE id=?",
                         (_now(), target["id"]))
            _notify(conn, target["id"], "Được SPADMIN tặng RUBY", "Owner đã duyệt yêu cầu RUBY. Gói có hiệu lực vĩnh viễn.")
        conn.execute("UPDATE premium_requests SET status=?,reviewed_by=?,reviewed_at=? WHERE id=?",
                     ("approved" if body.approve else "rejected", owner["id"], _now(), request_id))
        _audit(conn, owner, "REVIEW_RUBY_REQUEST", target["username"], "approved" if body.approve else "rejected")
        conn.execute("COMMIT")
    return {"ok": True}


@m.app.post("/api/v2/admin/request")
async def v2_admin_request(request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    user = require_roles(request, {"member"})
    with m.db() as conn:
        pending = conn.execute("SELECT id FROM admin_requests WHERE user_id=? AND status='pending'", (user["id"],)).fetchone()
        if pending:
            raise HTTPException(409, "Yêu cầu Admin của bạn đang chờ Owner xét duyệt.")
        cur = conn.execute("INSERT INTO admin_requests(user_id,status,created_at) VALUES(?,?,?)", (user["id"], "pending", _now()))
        conn.execute("UPDATE users SET admin_request_status='pending' WHERE id=?", (user["id"],))
        _notify(conn, user["id"], "Đã gửi yêu cầu Admin", "Yêu cầu của bạn đang chờ Owner xét duyệt.")
    return {"ok": True, "request_id": cur.lastrowid}


@m.app.get("/api/v2/owner/admin-requests")
async def v2_admin_requests(request: Request) -> dict[str, Any]:
    require_roles(request, {"owner"})
    with m.db() as conn:
        rows = conn.execute("SELECT r.id,r.status,r.created_at,u.username,u.display_name,u.account_id,u.profile_age,u.profile_location FROM admin_requests r JOIN users u ON u.id=r.user_id WHERE r.status='pending' ORDER BY r.id DESC").fetchall()
    return {"items": [dict(x) for x in rows]}


@m.app.post("/api/v2/owner/admin-requests/{request_id}/review")
async def v2_review_admin_request(request_id: int, body: ReviewBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    owner = require_roles(request, {"owner"})
    with m.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        req = conn.execute("SELECT * FROM admin_requests WHERE id=? AND status='pending'", (request_id,)).fetchone()
        if not req:
            raise HTTPException(404, "Yêu cầu không còn chờ duyệt.")
        target = conn.execute("SELECT * FROM users WHERE id=?", (req["user_id"],)).fetchone()
        cap = int(conn.execute("SELECT limit_count FROM role_limits WHERE role='admin'").fetchone()[0])
        count = sum(1 for r in conn.execute("SELECT * FROM users") if role_of(r) == "admin")
        if body.approve and count >= cap:
            raise HTTPException(409, f"Đã dùng đủ {cap} suất Admin.")
        if body.approve:
            conn.execute("UPDATE users SET role='admin',role_v2='',admin_request_status='approved' WHERE id=?", (target["id"],))
        else:
            conn.execute("UPDATE users SET admin_request_status='rejected' WHERE id=?", (target["id"],))
        conn.execute("UPDATE admin_requests SET status=?,note=?,reviewed_by=?,reviewed_at=? WHERE id=?",
                     ("approved" if body.approve else "rejected", body.note, owner["id"], _now(), request_id))
        _notify(conn, target["id"], "Kết quả yêu cầu Admin", "Đã được duyệt." if body.approve else f"Chưa được duyệt. {body.note}")
        _audit(conn, owner, "REVIEW_ADMIN_REQUEST", target["username"], "approved" if body.approve else "rejected")
        conn.execute("COMMIT")
    return {"ok": True}


def _role_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {r: 0 for r in ROLE_LIMIT_DEFAULTS}
    for row in conn.execute("SELECT * FROM users"):
        role = role_of(row)
        if role in counts:
            counts[role] += 1
    return counts


@m.app.get("/api/v2/owner/accounts")
async def v2_owner_accounts(request: Request) -> dict[str, Any]:
    require_roles(request, {"owner"})
    with m.db() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        caps = {r["role"]: int(r["limit_count"]) for r in conn.execute("SELECT * FROM role_limits")}
        counts = _role_counts(conn)
        items = []
        for row in rows:
            tier = _premium_tier(conn, row)
            items.append({"username": row["username"], "display_name": row["display_name"], "account_id": row["account_id"],
                          "role": role_of(row), "balance": int(row["balance_cents"])/100, "disabled": bool(row["disabled"]),
                          "banned": bool(row["is_banned"]), "ban_reason": row["ban_reason"], "premium_tier": tier,
                          "premium_expires_at": row["premium_expires_at"], "created_at": row["created_at"]})
    return {"items": items, "limits": caps, "counts": counts}


@m.app.post("/api/v2/owner/accounts")
async def v2_owner_create_account(body: OwnerCreateBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    owner = require_roles(request, {"owner"})
    username = body.username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        raise HTTPException(400, "Tên đăng nhập cần 3–32 ký tự, chữ/số/._-.")
    try:
        pwd_hash, salt = m.hash_password(body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    with m.db() as conn:
        used = {r[0] for r in conn.execute("SELECT account_id FROM users")}
        account_id = None
        for _ in range(1000):
            candidate = f"{secrets.randbelow(100_000_000):08d}"
            if candidate not in used:
                account_id = candidate
                break
        if account_id is None:
            raise HTTPException(500, "Không tạo được ID tài khoản.")
        try:
            cur = conn.execute("INSERT INTO users(username,password_hash,salt,role,role_v2,account_id,display_name,balance_cents,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                               (username, pwd_hash, salt, "member", "", account_id, body.display_name.strip() or username, 50_000_000, m.utc_now().isoformat()))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "Tên đăng nhập đã tồn tại.") from exc
        conn.executemany("INSERT INTO holdings(user_id,symbol,quantity) VALUES(?,?,0)", [(cur.lastrowid, sym) for sym in m.ASSETS])
        _audit(conn, owner, "CREATE_ACCOUNT", username, f"ID {account_id}")
    return {"ok": True, "account": {"username": username, "account_id": account_id, "role": "member"}}


@m.app.post("/api/v2/owner/accounts/{account_id}/role")
async def v2_owner_change_role(account_id: str, body: RoleActionBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    owner = require_roles(request, {"owner"})
    with m.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        target = _get_by_account_id(conn, account_id)
        if not target or target["id"] == owner["id"]:
            raise HTTPException(404, "Không tìm thấy tài khoản hợp lệ.")
        old_role = role_of(target)
        if body.direction == "grant_superadmin":
            new_role = "superadmin"
        elif body.direction in {"promote", "demote"}:
            idx = ROLE_LADDER.index(old_role) if old_role in ROLE_LADDER else -1
            if idx < 0:
                raise HTTPException(400, "Không thể thay đổi vai trò này.")
            next_idx = idx + (1 if body.direction == "promote" else -1)
            if not 0 <= next_idx < len(ROLE_LADDER):
                raise HTTPException(409, "Đã ở cấp vai trò giới hạn.")
            new_role = ROLE_LADDER[next_idx]
        else:
            raise HTTPException(400, "Thao tác vai trò không hợp lệ.")
        if new_role in ROLE_LIMIT_DEFAULTS:
            cap = conn.execute("SELECT limit_count FROM role_limits WHERE role=?", (new_role,)).fetchone()[0]
            if _role_counts(conn)[new_role] >= int(cap):
                raise HTTPException(409, f"Đã dùng đủ {cap} suất {ROLE_LABELS[new_role]}.")
        base_role = new_role if new_role in {"admin", "owner"} else "member"
        role_v2 = "" if new_role in {"admin", "member", "owner"} else new_role
        conn.execute("UPDATE users SET role=?,role_v2=?,coordinate_promoted_at=? WHERE id=?",
                     (base_role, role_v2, _now() if new_role == "coordinate" else "", target["id"]))
        _audit(conn, owner, "ROLE_CHANGE", target["username"], f"{old_role} -> {new_role}")
        _notify(conn, target["id"], "Thay đổi vai trò", f"Vai trò của bạn đã đổi thành {ROLE_LABELS[new_role]}.")
        conn.execute("COMMIT")
    return {"ok": True, "role": new_role}


@m.app.post("/api/v2/owner/role-limits")
async def v2_owner_role_limits(body: RoleLimitsBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    owner = require_roles(request, {"owner"})
    with m.db() as conn:
        counts = _role_counts(conn)
        for role in ROLE_LIMIT_DEFAULTS:
            if role not in body.limits:
                continue
            cap = int(body.limits[role])
            if not 1 <= cap <= ROLE_LIMIT_MAX or cap < counts[role]:
                raise HTTPException(400, f"Hạn mức {ROLE_LABELS[role]} phải từ {counts[role]} đến {ROLE_LIMIT_MAX}.")
            conn.execute("UPDATE role_limits SET limit_count=? WHERE role=?", (cap, role))
        _audit(conn, owner, "ROLE_LIMITS_UPDATE", details=json.dumps(body.limits, ensure_ascii=False))
        caps = {r["role"]: int(r["limit_count"]) for r in conn.execute("SELECT * FROM role_limits")}
    return {"ok": True, "limits": caps}


@m.app.post("/api/v2/owner/accounts/{account_id}/cash")
async def v2_owner_cash(account_id: str, body: CashAdjustBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    owner = require_roles(request, {"owner"})
    delta = Decimal(body.delta)
    if not delta.is_finite() or abs(delta) > Decimal("9999999999"):
        raise HTTPException(400, "Mức điều chỉnh vượt giới hạn.")
    delta_cents = int((delta * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    with m.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        target = _get_by_account_id(conn, account_id)
        if not target:
            raise HTTPException(404, "Không tìm thấy tài khoản.")
        new_balance = int(target["balance_cents"]) + delta_cents
        if new_balance < 0:
            raise HTTPException(409, "Không thể trừ quá số dư hiện có.")
        conn.execute("UPDATE users SET balance_cents=? WHERE id=?", (new_balance, target["id"]))
        conn.execute("INSERT INTO trades(user_id,side,symbol,quantity,price,total_cents,created_at) VALUES(?,?,?,?,?,?,?)",
                     (target["id"], "admin_cash", "USD", abs(delta_cents)/100, 1, abs(delta_cents), m.utc_now().isoformat()))
        _audit(conn, owner, "ADMIN_CASH_ADJUST", target["username"], f"delta ${delta_cents/100:,.2f}")
        _notify(conn, target["id"], "Điều chỉnh số dư", f"Owner đã điều chỉnh số dư {delta_cents/100:+,.2f} USD.")
        conn.execute("COMMIT")
    return {"ok": True, "balance": new_balance/100}


@m.app.post("/api/v2/admin/cash")
async def v2_admin_cash(body: CashAdjustBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    actor = require_roles(request, {"owner", "superadmin", "admin"})
    delta = Decimal(body.delta)
    if not delta.is_finite() or abs(delta) > Decimal("9999999999"):
        raise HTTPException(400, "Mức điều chỉnh vượt giới hạn.")
    cents = int((delta * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    with m.db() as conn:
        current = conn.execute("SELECT balance_cents FROM users WHERE id=?", (actor["id"],)).fetchone()[0]
        new_balance = int(current) + cents
        if new_balance < 0:
            raise HTTPException(409, "Không thể trừ quá số dư hiện có.")
        conn.execute("UPDATE users SET balance_cents=? WHERE id=?", (new_balance, actor["id"]))
        _audit(conn, actor, "ADMIN_CASH_ADJUST_SELF", details=f"delta ${cents/100:,.2f}")
    return {"ok": True, "balance": new_balance/100}


def _penalty_limit(role: str) -> int | None:
    return {"coordinate": 1_000_000_000, "admin": 10_000_000_000, "superadmin": 100_000_000_000, "owner": None}.get(role, 0)


def _parse_penalty(spec: str, target_balance_cents: int, limit: int | None) -> int:
    raw = str(spec).strip().replace(",", "")
    if raw.endswith("%"):
        try:
            pct = Decimal(raw[:-1])
        except InvalidOperation:
            raise HTTPException(400, "Phần trăm không hợp lệ.")
        if not pct.is_finite() or pct <= 0 or pct > 1000:
            raise HTTPException(400, "Phần trăm phải lớn hơn 0 và không vượt 1000%.")
        if target_balance_cents <= 0:
            raise HTTPException(400, "Số dư không dương; hãy nhập số tiền cố định.")
        amount_usd = (Decimal(target_balance_cents)/100 * pct / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    else:
        if not re.fullmatch(r"\d{1,100}", raw):
            raise HTTPException(400, "Nhập số nguyên dương hoặc dạng 10%.")
        amount_usd = Decimal(raw)
    if amount_usd <= 0 or amount_usd > Decimal("999999999999"):
        raise HTTPException(400, "Số tiền phạt ngoài phạm vi cho phép.")
    if not raw.endswith("%") and limit is not None and amount_usd > limit:
        raise HTTPException(400, f"Hạn mức phạt vai trò là ${limit:,.0f}.")
    return int((amount_usd*100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _apply_penalty(conn: sqlite3.Connection, actor: sqlite3.Row, target: sqlite3.Row, amount_cents: int, spec: str, reason: str) -> None:
    if role_of(target) == "owner":
        raise HTTPException(403, "Không thể phạt Owner duy nhất.")
    tier = _premium_tier(conn, target)
    if tier == "ruby" and int(target["ruby_immunity_uses"] or 0) > 0:
        left = int(target["ruby_immunity_uses"]) - 1
        conn.execute("UPDATE users SET ruby_immunity_uses=? WHERE id=?", (left, target["id"]))
        _notify(conn, target["id"], "RUBY kích hoạt miễn phạt", f"Một lượt đã dùng; còn {left}/3 lượt trọn đời.")
        _audit(conn, actor, "PENALTY_IMMUNITY_USED", target["username"], f"Còn {left}/3")
        return
    old_balance = int(target["balance_cents"])
    new_balance = old_balance - amount_cents
    debt = max(0, -new_balance)
    conn.execute("UPDATE users SET balance_cents=?,exchange_debt_cents=? WHERE id=?", (new_balance, debt, target["id"]))
    conn.execute("INSERT INTO penalties(target_id,actor_id,amount_cents,entered_spec,reason,created_at) VALUES(?,?,?,?,?,?)",
                 (target["id"], actor["id"], amount_cents, spec[:110], reason[:500], _now()))
    conn.execute("INSERT INTO trades(user_id,side,symbol,quantity,price,total_cents,created_at) VALUES(?,?,?,?,?,?,?)",
                 (target["id"], "penalty", "USD", amount_cents/100, 1, amount_cents, m.utc_now().isoformat()))
    _notify(conn, target["id"], "Tài khoản bị phạt vi phạm", f"Người xử lý: {actor['display_name']} · {role_label(actor)}\nMức phạt: ${amount_cents/100:,.2f}\nSố dư mới: ${new_balance/100:,.2f}\nNợ sàn: ${debt/100:,.2f}\nLý do: {reason[:300]}")
    _notify(conn, actor["id"], "Đã áp dụng hình phạt", f"Đã phạt {target['display_name']} (${amount_cents/100:,.2f}).")
    _audit(conn, actor, "PENALTY_APPLIED", target["username"], f"${amount_cents/100:,.2f}; {reason[:250]}")


@m.app.post("/api/v2/admin/penalty")
async def v2_penalty(body: PenaltyBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    actor = require_roles(request, {"owner", "superadmin", "admin", "coordinate"})
    with m.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        target = _get_by_account_id(conn, body.target_id)
        if not target or target["id"] == actor["id"]:
            raise HTTPException(404, "Không tìm thấy mục tiêu hợp lệ.")
        actor_role, target_role = role_of(actor), role_of(target)
        if actor_role in {"admin", "coordinate"} and target_role != "member":
            raise HTTPException(403, "Admin/TỌA ĐỘ chỉ được phạt tài khoản thành viên thường.")
        if actor_role in {"admin", "coordinate"} and _premium_tier(conn, target) == "ruby":
            amount = _parse_penalty(body.amount_spec, int(target["balance_cents"]), _penalty_limit(actor_role))
            cur = conn.execute("INSERT INTO penalty_requests(target_id,actor_id,amount_spec,reason,status,created_at) VALUES(?,?,?,?,?,?)",
                               (target["id"], actor["id"], body.amount_spec, body.reason.strip(), "pending_spadmin", _now()))
            for sp in conn.execute("SELECT id FROM users WHERE role_v2='superadmin' OR role='superadmin'"):
                _notify(conn, sp["id"], "Yêu cầu phạt RUBY", f"Tài khoản {target['display_name']} · ID {target['account_id']} đang chờ SPADMIN xem xét. Mức đề xuất: ${amount/100:,.2f}.")
            _audit(conn, actor, "REQUEST_RUBY_PENALTY", target["username"], f"request {cur.lastrowid}")
            conn.execute("COMMIT")
            return {"ok": True, "pending_superadmin": True, "request_id": cur.lastrowid}
        amount = _parse_penalty(body.amount_spec, int(target["balance_cents"]), _penalty_limit(actor_role))
        _apply_penalty(conn, actor, target, amount, body.amount_spec, body.reason.strip() or "Vi phạm quy định sàn giả lập")
        conn.execute("COMMIT")
    return {"ok": True, "amount": amount/100}


@m.app.get("/api/v2/admin/penalty-requests")
async def v2_penalty_requests(request: Request) -> dict[str, Any]:
    actor = require_roles(request, {"owner", "superadmin"})
    with m.db() as conn:
        rows = conn.execute("SELECT p.*,t.username AS target_username,t.display_name AS target_name,t.account_id AS target_account_id,a.username AS actor_username,a.display_name AS actor_name FROM penalty_requests p JOIN users t ON t.id=p.target_id JOIN users a ON a.id=p.actor_id WHERE p.status='pending_spadmin' ORDER BY p.id DESC").fetchall()
    return {"items": [dict(r) for r in rows]}


@m.app.post("/api/v2/admin/penalty-requests/{request_id}/review")
async def v2_review_penalty_request(request_id: int, body: ReviewBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    actor = require_roles(request, {"owner", "superadmin"})
    with m.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        req = conn.execute("SELECT * FROM penalty_requests WHERE id=? AND status='pending_spadmin'", (request_id,)).fetchone()
        if not req:
            raise HTTPException(404, "Yêu cầu không còn chờ xử lý.")
        target = conn.execute("SELECT * FROM users WHERE id=?", (req["target_id"],)).fetchone()
        amount = _parse_penalty(req["amount_spec"], int(target["balance_cents"]), _penalty_limit(role_of(actor)))
        if body.approve:
            _apply_penalty(conn, actor, target, amount, req["amount_spec"], req["reason"])
        conn.execute("UPDATE penalty_requests SET status=?,reviewed_by=?,reviewed_at=? WHERE id=?",
                     ("approved" if body.approve else "rejected", actor["id"], _now(), request_id))
        _notify(conn, req["actor_id"], "Kết quả yêu cầu phạt RUBY", "Yêu cầu đã được duyệt." if body.approve else "Yêu cầu đã bị từ chối.")
        conn.execute("COMMIT")
    return {"ok": True}


@m.app.post("/api/v2/moderation/ban")
async def v2_ban(body: BanBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    actor = require_roles(request, {"owner", "superadmin", "admin", "coordinate"})
    with m.db() as conn:
        target = _get_by_account_id(conn, body.target_id)
        if not target or target["id"] == actor["id"]:
            raise HTTPException(404, "Không tìm thấy mục tiêu hợp lệ.")
        if _norm_name(body.target_name) != _norm_name(target["display_name"]):
            raise HTTPException(400, "Tên hiển thị không khớp ID.")
        tr = role_of(target)
        actor_role = role_of(actor)
        target_tier = _premium_tier(conn, target)
        if tr == "owner":
            raise HTTPException(403, "Không thể khóa Owner duy nhất.")
        if actor_role == "coordinate":
            if tr != "member" or PREMIUM_RANK.get(target_tier or "", -1) > PREMIUM_RANK["platinum"]:
                raise HTTPException(403, "TỌA ĐỘ chỉ được khóa thành viên thường có cấp Bạch Kim trở xuống.")
        if actor_role == "admin" and tr != "member":
            raise HTTPException(403, "Admin chỉ được xử lý tài khoản thành viên thường.")
        if actor_role == "superadmin" and tr not in {"member", "coordinate", "superadmin"}:
            raise HTTPException(403, "SPADMIN chỉ được xử lý thành viên, TỌA ĐỘ hoặc SPADMIN khác.")
        conn.execute("UPDATE users SET is_banned=?,ban_reason=? WHERE id=?", (int(body.banned), body.reason if body.banned else "", target["id"]))
        _notify(conn, target["id"], "Tài khoản bị khóa" if body.banned else "Tài khoản được mở khóa",
                f"Lý do: {body.reason}" if body.banned else "Quyền truy cập đã được khôi phục.")
        _audit(conn, actor, "BAN_ACCOUNT" if body.banned else "UNBAN_ACCOUNT", target["username"], body.reason)
    return {"ok": True, "banned": body.banned}


@m.app.post("/api/v2/moderation/reports")
async def v2_create_report(body: ReportBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    author = require_user(request)
    with m.db() as conn:
        target = _get_by_account_id(conn, body.target_id)
        if not target or target["id"] == author["id"]:
            raise HTTPException(404, "Không tìm thấy tài khoản được báo cáo.")
        if _norm_name(body.target_name) != _norm_name(target["display_name"]):
            raise HTTPException(400, "Tên hiển thị không khớp ID.")
        cur = conn.execute("INSERT INTO moderation_reports(author_id,target_id,report_type,reason,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                           (author["id"], target["id"], "ban_request" if body.request_ban else "complaint", body.reason.strip(), "pending", _now(), _now()))
        _notify(conn, target["id"], "Có báo cáo liên quan đến tài khoản", f"Một báo cáo đã được gửi để bộ phận quản trị xem xét. Mã hồ sơ: {cur.lastrowid}.")
        _audit(conn, author, "SUBMIT_MODERATION_REPORT", target["username"], f"report {cur.lastrowid}")
    return {"ok": True, "report_id": cur.lastrowid}


@m.app.get("/api/v2/moderation/reports")
async def v2_reports(request: Request) -> dict[str, Any]:
    user = require_user(request)
    role = role_of(user)
    with m.db() as conn:
        if role in {"owner", "superadmin", "admin", "coordinate"}:
            rows = conn.execute("SELECT r.*,a.username AS author_username,a.display_name AS author_name,t.username AS target_username,t.display_name AS target_name,t.account_id AS target_account_id FROM moderation_reports r JOIN users a ON a.id=r.author_id JOIN users t ON t.id=r.target_id ORDER BY r.id DESC LIMIT 200").fetchall()
        else:
            rows = conn.execute("SELECT r.*,a.username AS author_username,a.display_name AS author_name,t.username AS target_username,t.display_name AS target_name,t.account_id AS target_account_id FROM moderation_reports r JOIN users a ON a.id=r.author_id JOIN users t ON t.id=r.target_id WHERE r.author_id=? ORDER BY r.id DESC LIMIT 100", (user["id"],)).fetchall()
    return {"items": [dict(x) for x in rows]}


@m.app.post("/api/v2/moderation/reports/{report_id}/message")
async def v2_report_message(report_id: int, body: ReportMessageBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    user = require_user(request)
    with m.db() as conn:
        report = conn.execute("SELECT * FROM moderation_reports WHERE id=?", (report_id,)).fetchone()
        if not report:
            raise HTTPException(404, "Không tìm thấy hồ sơ.")
        role = role_of(user)
        if user["id"] not in (report["author_id"], report["target_id"]) and role not in {"owner", "superadmin", "admin", "coordinate"}:
            raise HTTPException(403, "Không có quyền trao đổi trong hồ sơ này.")
        conn.execute("INSERT INTO report_messages(report_id,sender_id,body,created_at) VALUES(?,?,?,?)",
                     (report_id, user["id"], body.text.strip(), _now()))
        conn.execute("UPDATE moderation_reports SET updated_at=? WHERE id=?", (_now(), report_id))
    return {"ok": True}


@m.app.get("/api/v2/moderation/reports/{report_id}/messages")
async def v2_report_messages(report_id: int, request: Request) -> dict[str, Any]:
    user = require_user(request)
    with m.db() as conn:
        report = conn.execute("SELECT * FROM moderation_reports WHERE id=?", (report_id,)).fetchone()
        if not report:
            raise HTTPException(404, "Không tìm thấy hồ sơ.")
        if user["id"] not in (report["author_id"], report["target_id"]) and role_of(user) not in {"owner", "superadmin", "admin", "coordinate"}:
            raise HTTPException(403, "Không có quyền xem trao đổi này.")
        rows = conn.execute("SELECT rm.*,u.username,u.display_name FROM report_messages rm JOIN users u ON u.id=rm.sender_id WHERE report_id=? ORDER BY rm.id", (report_id,)).fetchall()
    return {"items": [dict(x) for x in rows]}


@m.app.post("/api/v2/moderation/reports/{report_id}/decision")
async def v2_report_decision(report_id: int, body: ReviewBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    owner = require_roles(request, {"owner"})
    with m.db() as conn:
        report = conn.execute("SELECT * FROM moderation_reports WHERE id=?", (report_id,)).fetchone()
        if not report:
            raise HTTPException(404, "Không tìm thấy hồ sơ.")
        new_status = "approved" if body.approve else "rejected"
        conn.execute("UPDATE moderation_reports SET status=?,updated_at=? WHERE id=?", (new_status, _now(), report_id))
        if body.approve and report["report_type"] == "ban_request":
            conn.execute("UPDATE users SET is_banned=1,ban_reason=? WHERE id=?", (body.note or report["reason"], report["target_id"]))
        _audit(conn, owner, "MODERATION_DECISION", details=f"report {report_id}: {new_status}")
        _notify(conn, report["author_id"], "Kết quả hồ sơ kiểm duyệt", f"Hồ sơ #{report_id}: {new_status}. {body.note}")
    return {"ok": True, "status": new_status}


@m.app.get("/api/v2/chat/{room}")
async def v2_chat_get(room: str, request: Request) -> dict[str, Any]:
    user = require_user(request)
    role = role_of(user)
    tier = _premium_tier_for_request(user)
    allowed = room == "total" or (room == "qtv" and role in {"owner", "superadmin", "admin", "coordinate"}) or (room == "members" and (role in {"owner", "superadmin", "admin", "coordinate"} or tier is not None))
    if room not in {"total", "qtv", "members"} or not allowed:
        raise HTTPException(403, "Tài khoản không có quyền vào phòng chat này.")
    with m.db() as conn:
        rows = conn.execute("SELECT c.id,c.room,c.display_name,c.role_label,c.body,c.created_at,u.username FROM chat_messages c JOIN users u ON u.id=c.user_id WHERE room=? ORDER BY c.id DESC LIMIT 100", (room,)).fetchall()
    return {"items": [dict(x) for x in reversed(rows)]}


@m.app.post("/api/v2/chat/{room}")
async def v2_chat_send(room: str, body: ChatBody, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    user = require_user(request)
    role, tier = role_of(user), _premium_tier_for_request(user)
    allowed = room == "total" or (room == "qtv" and role in {"owner", "superadmin", "admin", "coordinate"}) or (room == "members" and (role in {"owner", "superadmin", "admin", "coordinate"} or tier is not None))
    if room not in {"total", "qtv", "members"} or not allowed:
        raise HTTPException(403, "Tài khoản không có quyền gửi tin nhắn trong phòng này.")
    clean = " ".join(body.text.split())
    if not clean:
        raise HTTPException(400, "Tin nhắn trống.")
    if tier == "emerald" and room != "qtv":
        tier_label = "THÀNH VIÊN"
    else:
        tier_label = PREMIUM[tier]["label"] if tier else ""
    label = ROLE_LABELS.get(role, role.upper()) + (f" · {tier_label}" if tier_label else "")
    with m.db() as conn:
        conn.execute("INSERT INTO chat_messages(room,user_id,display_name,role_label,body,created_at) VALUES(?,?,?,?,?,?)",
                     (room, user["id"], user["display_name"], label, clean[:500], _now()))
        conn.execute("DELETE FROM chat_messages WHERE room=? AND id NOT IN (SELECT id FROM chat_messages WHERE room=? ORDER BY id DESC LIMIT 500)", (room, room))
    return {"ok": True}


@m.app.get("/api/v2/owner/audit")
async def v2_audit(request: Request) -> dict[str, Any]:
    require_roles(request, {"owner"})
    with m.db() as conn:
        rows = conn.execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT 300").fetchall()
    return {"items": [dict(x) for x in rows]}


@m.app.get("/api/v2/owner/backup")
async def v2_backup(request: Request) -> JSONResponse:
    owner = require_roles(request, {"owner"})
    tables = ["users", "holdings", "trades", "inbox", "transfers", "loans", "penalties", "penalty_requests", "chat_messages", "audit_events", "admin_requests", "moderation_reports", "report_messages", "emerald_lots", "premium_requests", "role_limits", "owner_support_sessions", "global_state"]
    payload: dict[str, Any] = {"format": "sanchungkhoan-web-backup-v2", "created_at": _now(), "created_by": owner["username"], "tables": {}}
    m.persist_market_state()
    with m.db() as conn:
        for table in tables:
            payload["tables"][table] = [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
    return JSONResponse(payload, headers={"Content-Disposition": 'attachment; filename="sanh-chung-khoan-backup.json"'})


class RestoreBackupBody(BaseModel):
    payload: dict[str, Any]


@m.app.post("/api/v2/owner/restore")
async def v2_restore_backup(body: RestoreBackupBody, request: Request) -> dict[str, Any]:
    """Restore an Owner-exported backup, validating its sole Owner and every table/column.

    This supports moving demo accounts from Render's temporary SQLite database to an
    external persistent PostgreSQL database. The current Owner session is retained.
    """
    m.check_same_origin(request)
    current_owner = require_roles(request, {"owner"})
    payload = body.payload
    if not isinstance(payload, dict) or payload.get("format") != "sanchungkhoan-web-backup-v2":
        raise HTTPException(400, "Tệp sao lưu không đúng định dạng SÀN CHỨNG KHOÁN WEB v2.")
    tables = payload.get("tables")
    if not isinstance(tables, dict) or not isinstance(tables.get("users"), list):
        raise HTTPException(400, "Bản sao lưu không có danh sách tài khoản hợp lệ.")

    allowed_tables = {
        "users", "holdings", "trades", "inbox", "transfers", "loans", "penalties",
        "penalty_requests", "chat_messages", "audit_events", "admin_requests",
        "moderation_reports", "report_messages", "emerald_lots", "premium_requests",
        "role_limits", "owner_support_sessions", "global_state",
    }
    if set(tables) - allowed_tables:
        raise HTTPException(400, "Bản sao lưu chứa bảng không được hỗ trợ.")
    if len(tables["users"]) < 1 or len(tables["users"]) > 10000:
        raise HTTPException(400, "Số lượng tài khoản trong bản sao lưu không hợp lệ.")
    total_rows = 0
    for table_name, rows in tables.items():
        if table_name not in allowed_tables or not isinstance(rows, list) or len(rows) > 200000:
            raise HTTPException(400, f"Dữ liệu bảng {table_name} không hợp lệ hoặc quá lớn.")
        total_rows += len(rows)
        if total_rows > 300000:
            raise HTTPException(400, "Bản sao lưu quá lớn; đã từ chối để bảo vệ máy chủ.")
        if any(not isinstance(row, dict) for row in rows):
            raise HTTPException(400, f"Bảng {table_name} có bản ghi sai cấu trúc.")

    users = tables["users"]
    usernames = set()
    account_ids = set()
    user_ids = set()
    owner_rows = []
    valid_roles = {"owner", "admin", "member"}
    valid_roles_v2 = {"", "owner", "superadmin", "admin", "coordinate", "member"}
    for row in users:
        username = str(row.get("username", ""))
        account_id = str(row.get("account_id", ""))
        try:
            row_id = int(row.get("id"))
            balance = int(row.get("balance_cents"))
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(400, "Bản sao lưu có ID hoặc số dư tài khoản không hợp lệ.")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username) or username.casefold() in usernames:
            raise HTTPException(400, "Tên đăng nhập trong bản sao lưu không hợp lệ hoặc bị trùng.")
        if not re.fullmatch(r"\d{8}", account_id) or account_id in account_ids:
            raise HTTPException(400, "ID tài khoản trong bản sao lưu không hợp lệ hoặc bị trùng.")
        if row_id <= 0 or row_id in user_ids or balance < 0 or balance > 10**18:
            raise HTTPException(400, "ID hoặc số dư trong bản sao lưu vượt phạm vi cho phép.")
        if row.get("role") not in valid_roles or row.get("role_v2", "") not in valid_roles_v2:
            raise HTTPException(400, f"Vai trò của tài khoản {username} không hợp lệ.")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", str(row.get("password_hash", ""))) or not re.fullmatch(r"[0-9a-fA-F]{32}", str(row.get("salt", ""))):
            raise HTTPException(400, f"Thông tin xác thực đã băm của tài khoản {username} không hợp lệ.")
        usernames.add(username.casefold()); account_ids.add(account_id); user_ids.add(row_id)
        if row.get("role") == "owner" or row.get("role_v2") == "owner":
            owner_rows.append(row)
    if len(owner_rows) != 1 or str(owner_rows[0].get("username", "")).casefold() != current_owner["username"].casefold():
        raise HTTPException(400, "Bản sao lưu phải có đúng một Owner và trùng tên đăng nhập với Owner đang đăng nhập.")
    owner_backup_id = int(owner_rows[0]["id"])

    # Delete children before parents. All identifiers come from this fixed allow-list.
    delete_order = [
        "report_messages", "moderation_reports", "penalty_requests", "penalties", "loans",
        "transfers", "emerald_lots", "premium_requests", "admin_requests", "owner_support_sessions",
        "chat_messages", "audit_events", "inbox", "trades", "holdings", "sessions", "login_limits",
        "registration_limits", "users", "role_limits", "global_state",
    ]
    insert_order = [
        "users", "role_limits", "holdings", "trades", "inbox", "transfers", "loans", "penalties",
        "penalty_requests", "chat_messages", "audit_events", "admin_requests", "moderation_reports",
        "report_messages", "emerald_lots", "premium_requests", "owner_support_sessions", "global_state",
    ]
    with m.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            saved_sessions = [dict(r) for r in conn.execute(
                "SELECT token_hash,expires_at,created_at FROM sessions WHERE user_id=?", (current_owner["id"],)
            )]
            table_columns: dict[str, list[str]] = {}
            for table_name in tables:
                table_columns[table_name] = [str(r[1]) for r in conn.execute(f"PRAGMA table_info({table_name})")]
                if not table_columns[table_name]:
                    raise RuntimeError(f"Không tìm thấy cấu trúc bảng {table_name}.")
                for row in tables[table_name]:
                    if set(row) - set(table_columns[table_name]):
                        raise HTTPException(400, f"Bảng {table_name} chứa cột không được hỗ trợ.")

            for table_name in delete_order:
                if table_name in tables or table_name in {"sessions", "login_limits", "registration_limits"}:
                    conn.execute(f"DELETE FROM {table_name}")

            for table_name in insert_order:
                rows = tables.get(table_name, [])
                if not rows:
                    continue
                ordered_columns = table_columns.get(table_name)
                if ordered_columns is None:
                    ordered_columns = [str(r[1]) for r in conn.execute(f"PRAGMA table_info({table_name})")]
                first_columns = [col for col in ordered_columns if col in rows[0]]
                if not first_columns:
                    raise HTTPException(400, f"Bảng {table_name} không có cột dữ liệu hợp lệ.")
                for row in rows:
                    if set(row) != set(first_columns):
                        # Backups generated by the same app version include a consistent column set per table.
                        raise HTTPException(400, f"Các bản ghi trong bảng {table_name} không thống nhất cột.")
                placeholders = ",".join("?" for _ in first_columns)
                sql = f"INSERT INTO {table_name}({','.join(first_columns)}) VALUES({placeholders})"
                conn.executemany(sql, [tuple(row[col] for col in first_columns) for row in rows])

            # Rebind the current browser's Owner session to the restored Owner ID.
            for session in saved_sessions:
                conn.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
                             (session["token_hash"], owner_backup_id, session["expires_at"], session["created_at"]))

            if m.DATABASE_URL:
                serial_tables = sorted({name for name in insert_order if "id" in table_columns.get(name, [])})
                for table_name in serial_tables:
                    conn.execute(
                        f"SELECT setval(pg_get_serial_sequence('{table_name}','id'), GREATEST(COALESCE(MAX(id),1),1), MAX(id) IS NOT NULL) FROM {table_name}"
                    )
            restored_owner = conn.execute("SELECT * FROM users WHERE id=?", (owner_backup_id,)).fetchone()
            if not restored_owner:
                raise RuntimeError("Không khôi phục được tài khoản Owner.")
            _audit(conn, restored_owner, "OWNER_RESTORE_BACKUP", details=f"Khôi phục {len(users)} tài khoản từ bản sao lưu.")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # Apply saved global market prices to the running process, if present.
    if "global_state" in tables:
        with m.db() as conn:
            saved_prices = conn.execute("SELECT value FROM global_state WHERE key='market_prices'").fetchone()
            saved_pause = conn.execute("SELECT value FROM global_state WHERE key='market_paused'").fetchone()
        if saved_prices and saved_prices[0]:
            try:
                prices = json.loads(saved_prices[0])
                with m.market_lock:
                    for symbol in m.ASSETS:
                        value = float(prices.get(symbol, m.ASSETS[symbol]["price"]))
                        if math.isfinite(value) and value > 0:
                            m.market_prices[symbol] = value
                            m.market_history[symbol].clear(); m.market_history[symbol].append(value)
                    if saved_pause:
                        m.market_paused = saved_pause[0] == "true"
                    m.market_tick = 0
                    m.market_plan = m.build_market_plan(m.market_prices, m.local_now())
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
    return {"ok": True, "restored_accounts": len(users), "message": "Đã khôi phục dữ liệu. Phiên Owner hiện tại được giữ lại."}


@m.app.post("/api/v2/owner/support/start")
async def v2_support_start(account_id: str, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    owner = require_roles(request, {"owner"})
    with m.db() as conn:
        active = conn.execute("SELECT id FROM owner_support_sessions WHERE owner_id=? AND ended_at=''", (owner["id"],)).fetchone()
        if active:
            raise HTTPException(409, "Đang có phiên hỗ trợ khác; hãy kết thúc phiên đó trước.")
        target = _get_by_account_id(conn, account_id)
        if not target or target["id"] == owner["id"] or role_of(target) == "owner" or int(target["is_banned"] or 0):
            raise HTTPException(400, "Không thể mở phiên hỗ trợ cho tài khoản này.")
        conn.execute("INSERT INTO owner_support_sessions(owner_id,target_id,started_at) VALUES(?,?,?)", (owner["id"], target["id"], _now()))
        _audit(conn, owner, "OWNER_SUPPORT_START", target["username"], "Owner mở phiên hỗ trợ; không truy cập mật khẩu.")
        _notify(conn, target["id"], "Phiên hỗ trợ Owner", "Owner đang mở phiên hỗ trợ để kiểm tra tài khoản của bạn.")
    return {"ok": True, "target": {"username": target["username"], "display_name": target["display_name"], "account_id": target["account_id"]}}


@m.app.get("/api/v2/owner/support")
async def v2_support_current(request: Request) -> dict[str, Any]:
    owner = require_roles(request, {"owner"})
    with m.db() as conn:
        session = conn.execute("SELECT s.*,u.username,u.display_name,u.account_id,u.role,u.role_v2,u.balance_cents,u.premium_tier,u.is_banned,u.ban_reason FROM owner_support_sessions s JOIN users u ON u.id=s.target_id WHERE s.owner_id=? AND s.ended_at='' ORDER BY s.id DESC LIMIT 1", (owner["id"],)).fetchone()
    return {"active": bool(session), "target": dict(session) if session else None}


@m.app.post("/api/v2/owner/support/end")
async def v2_support_end(request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    owner = require_roles(request, {"owner"})
    with m.db() as conn:
        session = conn.execute("SELECT s.*,u.username FROM owner_support_sessions s JOIN users u ON u.id=s.target_id WHERE s.owner_id=? AND s.ended_at='' ORDER BY s.id DESC LIMIT 1", (owner["id"],)).fetchone()
        if not session:
            raise HTTPException(404, "Không có phiên hỗ trợ đang hoạt động.")
        conn.execute("UPDATE owner_support_sessions SET ended_at=? WHERE id=?", (_now(), session["id"]))
        _audit(conn, owner, "OWNER_SUPPORT_END", session["username"], "Owner kết thúc phiên hỗ trợ.")
        _notify(conn, session["target_id"], "Kết thúc phiên hỗ trợ", "Owner đã kết thúc phiên hỗ trợ tài khoản.")
    return {"ok": True}


@m.app.post("/api/v2/owner/accounts/{account_id}/membership/revoke")
async def v2_revoke_membership(account_id: str, request: Request) -> dict[str, Any]:
    m.check_same_origin(request)
    owner = require_roles(request, {"owner"})
    with m.db() as conn:
        target = _get_by_account_id(conn, account_id)
        if not target:
            raise HTTPException(404, "Không tìm thấy tài khoản.")
        conn.execute("UPDATE users SET premium_tier='',premium_plan='',premium_started_at='',premium_expires_at='',emerald_previous_json='' WHERE id=?", (target["id"],))
        conn.execute("DELETE FROM emerald_lots WHERE user_id=?", (target["id"],))
        _audit(conn, owner, "REVOKE_MEMBERSHIP", target["username"], "Thu hồi toàn bộ Premium do xử lý vi phạm.")
        _notify(conn, target["id"], "Đã thu hồi thành viên", "Owner đã thu hồi quyền Premium; tài khoản trở về thành viên thường.")
    return {"ok": True}


@m.app.exception_handler(HTTPException)
async def extended_http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
