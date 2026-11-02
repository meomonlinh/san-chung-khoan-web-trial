from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import os
import random
import secrets
import sqlite3
import threading
import time
from array import array
from collections import deque
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
REQUIRE_DATABASE_URL = os.getenv("REQUIRE_DATABASE_URL", "false").lower() == "true"
DB_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "market_web.sqlite3")))
if not DATABASE_URL:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
TZ = ZoneInfo("Asia/Bangkok")
COOKIE_NAME = "market_session"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
SESSION_HOURS = 12
PBKDF2_ITERATIONS = 310_000
MARKET_PLAN_HORIZON_SECONDS = 24 * 60 * 60
MARKET_PLAN_CHUNK_SECONDS = 24 * 60 * 60
MARKET_PLAN_MARGIN_SECONDS = 300
MARKET_PLAN_SECONDS = MARKET_PLAN_HORIZON_SECONDS + MARKET_PLAN_CHUNK_SECONDS + MARKET_PLAN_MARGIN_SECONDS
MARKET_TICK_SECONDS = 1.0
HISTORY_POINTS = 360
UPWARD_BIAS_PER_HOUR = math.log(1.008)  # xu hướng thống kê +0,8%/giờ từ bản desktop
RANDOM_LOG_STEP_MAX = 0.0025
FIXED_HOLIDAYS = {"01-01", "04-30", "05-01", "09-02"}
EXTRA_HOLIDAYS = {"2026-01-02", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-04-27", "2026-08-31", "2026-09-01"}

ASSETS: dict[str, dict[str, Any]] = {
    "GOLD": {"name": "Vàng Thần Kỳ", "category": "Kim loại quý", "price": 2350.00, "color": "#f0bd38", "crypto": False},
    "SILVER": {"name": "Bạc Hoàng Gia", "category": "Kim loại quý", "price": 28.50, "color": "#bfc8d2", "crypto": False},
    "COPPER": {"name": "Đồng Công Nghiệp", "category": "Kim loại quý", "price": 4.20, "color": "#c47a48", "crypto": False},
    "PLATINUM": {"name": "Bạch Kim Đỏ", "category": "Kim loại quý", "price": 980.00, "color": "#d9e2eb", "crypto": False},
    "OIL": {"name": "Dầu Thô WTI", "category": "Năng lượng", "price": 78.50, "color": "#ef5350", "crypto": False},
    "BRENT": {"name": "Dầu Brent Biển", "category": "Năng lượng", "price": 82.00, "color": "#ff895c", "crypto": False},
    "GAS": {"name": "Khí Đốt Tự Nhiên", "category": "Năng lượng", "price": 2.65, "color": "#39b7d8", "crypto": False},
    "BTC": {"name": "Bitcoin Ảo", "category": "Crypto Real", "price": 65000.00, "color": "#ff9f0a", "crypto": True},
    "ETH": {"name": "Ethereum Ảo", "category": "Crypto Real", "price": 3500.00, "color": "#9a79db", "crypto": True},
    "SOL": {"name": "Solana Siêu Tốc", "category": "Crypto Real", "price": 145.00, "color": "#33b890", "crypto": True},
}

def bootstrap_owner_from_environment() -> bool:
    """Create the first Owner from deployment secrets, but never overwrite an existing Owner.

    Intended for disposable trial deployments where Render Free has no persistent disk.
    Keep the two environment variables set so an Owner can be recreated if the free
    instance loses its ephemeral SQLite database after a restart or spin-down.
    """
    username = os.getenv("INITIAL_OWNER_USERNAME", "").strip()
    password = os.getenv("INITIAL_OWNER_PASSWORD", "")
    if not username and not password:
        return False
    if not username or not password:
        raise RuntimeError("Cần cấu hình đồng thời INITIAL_OWNER_USERNAME và INITIAL_OWNER_PASSWORD.")
    if not (3 <= len(username) <= 32) or not username.replace("_", "").replace("-", "").isalnum():
        raise RuntimeError("INITIAL_OWNER_USERNAME phải dài 3–32 ký tự, gồm chữ, số, _ hoặc -.")
    if len(password) < 12 or len(password) > 128 or not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
        raise RuntimeError("INITIAL_OWNER_PASSWORD phải có 12–128 ký tự, gồm ít nhất một chữ và một số.")
    with db() as conn:
        owner = conn.execute("SELECT id FROM users WHERE role='owner' OR role_v2='owner' LIMIT 1").fetchone()
        if owner:
            return False
        duplicate = conn.execute("SELECT id FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        if duplicate:
            raise RuntimeError("Tên INITIAL_OWNER_USERNAME đã tồn tại nhưng chưa có vai trò Owner; hãy chọn tên khác hoặc kiểm tra database.")
    create_account(username, password, "owner", username)
    with db() as conn:
        conn.execute("UPDATE users SET role_v2='owner' WHERE username=? COLLATE NOCASE", (username,))
    return True


@asynccontextmanager
async def app_lifespan(application):
    global market_plan, sim_task
    init_db()
    bootstrap_owner_from_environment()
    with market_lock:
        market_plan = build_market_plan(market_prices, local_now())
    sim_task = asyncio.create_task(market_broadcaster())
    try:
        yield
    finally:
        if sim_task:
            sim_task.cancel()
            try:
                await sim_task
            except asyncio.CancelledError:
                pass
        persist_market_state()

app = FastAPI(title="SÀN CHỨNG KHOÁN", version="0.2.2-registration-trial", lifespan=app_lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Shared process-wide market. Every authenticated browser reads this same object.
market_lock = threading.RLock()
market_prices: dict[str, float] = {sym: float(row["price"]) for sym, row in ASSETS.items()}
market_history: dict[str, deque[float]] = {sym: deque([float(row["price"])] * 60, maxlen=HISTORY_POINTS) for sym, row in ASSETS.items()}
market_paused = False
market_tick = 0
market_plan: dict[str, array] = {}
market_plan_rng: random.Random | None = None
market_anchor = datetime.now(TZ)
market_clients: set[WebSocket] = set()
sim_task: asyncio.Task | None = None


@contextmanager
def db():
    """Use durable PostgreSQL when DATABASE_URL is configured; retain SQLite for local Windows runs.

    Render Free's local filesystem is ephemeral. REQUIRE_DATABASE_URL prevents accidentally
    launching a cloud trial against /tmp SQLite and silently losing player data after restart.
    """
    if DATABASE_URL:
        from db_compat import connect_postgres
        conn = connect_postgres(DATABASE_URL)
        try:
            yield conn
        finally:
            conn.close()
        return
    if REQUIRE_DATABASE_URL:
        raise RuntimeError("DATABASE_URL chưa được cấu hình. Hãy kết nối PostgreSQL bền vững trước khi chạy bản cloud.")
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('owner','admin','member')),
            account_id TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            balance_cents INTEGER NOT NULL DEFAULT 50000000,
            created_at TEXT NOT NULL,
            disabled INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS holdings (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            symbol TEXT NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id, symbol)
        );
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            side TEXT NOT NULL,
            symbol TEXT NOT NULL,
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            total_cents INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS inbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS login_limits (
            attempt_key TEXT PRIMARY KEY,
            failures INTEGER NOT NULL,
            window_started REAL NOT NULL,
            locked_until REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS registration_limits (
            attempt_key TEXT PRIMARY KEY,
            created_count INTEGER NOT NULL,
            window_started REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS global_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT OR IGNORE INTO global_state(key, value) VALUES('market_paused', 'false');
        INSERT OR IGNORE INTO global_state(key, value) VALUES('market_prices', '{}');
        """)
        stored = conn.execute("SELECT value FROM global_state WHERE key='market_prices'").fetchone()
        if stored and stored["value"]:
            try:
                prices = json.loads(stored["value"])
                for sym in ASSETS:
                    value = float(prices.get(sym, ASSETS[sym]["price"]))
                    if math.isfinite(value) and value > 0:
                        market_prices[sym] = value
                        market_history[sym].clear()
                        market_history[sym].append(value)
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
        paused = conn.execute("SELECT value FROM global_state WHERE key='market_paused'").fetchone()
        global market_paused
        market_paused = bool(paused and paused["value"] == "true")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def local_now() -> datetime:
    return datetime.now(TZ)


def is_market_open(symbol: str, when: datetime | None = None) -> bool:
    when = when or local_now()
    if ASSETS[symbol]["crypto"]:
        return True
    date_key = when.strftime("%Y-%m-%d")
    if when.weekday() >= 5 or when.strftime("%m-%d") in FIXED_HOLIDAYS or date_key in EXTRA_HOLIDAYS:
        return False
    return True


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    if not isinstance(password, str) or len(password) < 10 or len(password) > 128:
        raise ValueError("Mật khẩu phải dài từ 10 đến 128 ký tự.")
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return digest.hex(), salt.hex()


def verify_password(password: str, salt_hex: str, expected_hash: str) -> bool:
    try:
        actual, _ = hash_password(password, salt_hex)
        return hmac.compare_digest(actual, expected_hash)
    except (ValueError, TypeError):
        return False


def create_account(username: str, password: str, role: str = "member", display_name: str = "") -> dict[str, Any]:
    username = username.strip()
    if not (3 <= len(username) <= 32) or not username.replace("_", "").replace("-", "").isalnum():
        raise ValueError("Tên đăng nhập phải có 3–32 ký tự, chỉ gồm chữ, số, _ hoặc -.")
    if role not in {"owner", "admin", "member"}:
        raise ValueError("Vai trò không hợp lệ.")
    password_hash, salt = hash_password(password)
    with db() as conn:
        used = {row[0] for row in conn.execute("SELECT account_id FROM users")}
        account_id = None
        for _ in range(1000):
            candidate = f"{secrets.randbelow(100_000_000):08d}"
            if candidate not in used:
                account_id = candidate
                break
        if account_id is None:
            raise RuntimeError("Không tạo được ID 8 chữ số duy nhất.")
        cur = conn.execute(
            "INSERT INTO users(username,password_hash,salt,role,account_id,display_name,balance_cents,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (username, password_hash, salt, role, account_id, display_name.strip()[:50] or username, 50_000_000, utc_now().isoformat()),
        )
        user_id = int(cur.lastrowid)
        conn.executemany("INSERT INTO holdings(user_id,symbol,quantity) VALUES(?,?,0)", [(user_id, sym) for sym in ASSETS])
    return {"id": user_id, "username": username, "account_id": account_id, "role": role, "display_name": display_name.strip() or username}


def get_session_user(request: Request) -> sqlite3.Row:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Vui lòng đăng nhập.")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with db() as conn:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.disabled=0",
            (token_hash, utc_now().isoformat()),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Phiên đăng nhập hết hạn hoặc không hợp lệ.")
    return row


def require_owner(request: Request) -> sqlite3.Row:
    user = get_session_user(request)
    if user["role"] != "owner":
        raise HTTPException(403, "Chỉ Owner được sử dụng chức năng này.")
    return user


def check_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    from urllib.parse import urlparse
    parsed = urlparse(origin)
    if parsed.netloc != request.headers.get("host"):
        raise HTTPException(403, "Yêu cầu khác nguồn bị từ chối.")


def _is_traditional_open(when: datetime) -> bool:
    date_key = when.strftime("%Y-%m-%d")
    return when.weekday() < 5 and when.strftime("%m-%d") not in FIXED_HOLIDAYS and date_key not in EXTRA_HOLIDAYS


def build_market_plan(start_prices: dict[str, float], anchor: datetime | None = None) -> dict[str, array]:
    """Precompute more than 24h so exact Owner forecasts always have a full day ahead."""
    global market_anchor, market_plan_rng
    anchor = anchor or local_now()
    market_anchor = anchor
    rng = random.Random(secrets.randbits(128))
    market_plan_rng = rng
    paths = {sym: array("d", [float(start_prices[sym])]) for sym in ASSETS}
    current = dict(start_prices)
    drift = UPWARD_BIAS_PER_HOUR / 3600.0
    for second in range(1, MARKET_PLAN_SECONDS + 1):
        when = anchor + timedelta(seconds=second)
        traditional_open = _is_traditional_open(when)
        for sym, config in ASSETS.items():
            if config["crypto"] or traditional_open:
                noise = rng.uniform(-RANDOM_LOG_STEP_MAX, RANDOM_LOG_STEP_MAX)
                current[sym] = max(0.01, round(current[sym] * math.exp(noise + drift), 2))
            paths[sym].append(current[sym])
    return paths


def extend_market_plan() -> None:
    """Append another day to the shared precomputed path without changing current prices."""
    global market_plan_rng
    if not market_plan or market_plan_rng is None:
        return
    last_prices = {sym: float(path[-1]) for sym, path in market_plan.items()}
    start_second = len(next(iter(market_plan.values()))) - 1
    drift = UPWARD_BIAS_PER_HOUR / 3600.0
    current = dict(last_prices)
    for offset in range(1, MARKET_PLAN_CHUNK_SECONDS + 1):
        when = market_anchor + timedelta(seconds=start_second + offset)
        traditional_open = _is_traditional_open(when)
        for sym, config in ASSETS.items():
            if config["crypto"] or traditional_open:
                noise = market_plan_rng.uniform(-RANDOM_LOG_STEP_MAX, RANDOM_LOG_STEP_MAX)
                current[sym] = max(0.01, round(current[sym] * math.exp(noise + drift), 2))
            market_plan[sym].append(current[sym])


def price_snapshot() -> dict[str, Any]:
    with market_lock:
        now = local_now()
        rows = {}
        for sym, config in ASSETS.items():
            hist = list(market_history[sym])
            price = market_prices[sym]
            old = hist[-2] if len(hist) > 1 else price
            change = ((price - old) / old * 100.0) if old else 0.0
            rows[sym] = {
                "symbol": sym, "name": config["name"], "category": config["category"],
                "price": price, "change_pct": change, "history": hist,
                "color": config["color"], "is_open": is_market_open(sym, now),
            }
        return {"assets": rows, "paused": market_paused, "server_time": now.isoformat(),
                "market_status": "TẠM DỪNG TOÀN SÀN" if market_paused else "THỊ TRƯỜNG ĐANG CHẠY"}


def persist_market_state() -> None:
    with market_lock:
        prices_json = json.dumps(market_prices, separators=(",", ":"))
        paused_value = "true" if market_paused else "false"
    with db() as conn:
        conn.execute("UPDATE global_state SET value=? WHERE key='market_prices'", (prices_json,))
        conn.execute("UPDATE global_state SET value=? WHERE key='market_paused'", (paused_value,))


def engine_step() -> None:
    global market_tick, market_plan
    with market_lock:
        if market_paused:
            return
        next_tick = market_tick + 1
        if not market_plan:
            market_plan = build_market_plan(market_prices, local_now())
            market_tick = 0
            next_tick = 1
        remaining = len(next(iter(market_plan.values()))) - 1 - market_tick
        if remaining <= MARKET_PLAN_HORIZON_SECONDS + MARKET_PLAN_MARGIN_SECONDS:
            extend_market_plan()
        for sym in ASSETS:
            market_prices[sym] = float(market_plan[sym][next_tick])
            market_history[sym].append(market_prices[sym])
        market_tick = next_tick
    if market_tick % 10 == 0:
        persist_market_state()


def admin_forecasts() -> list[dict[str, Any]]:
    horizons = [("5 giây", 5), ("5 phút", 300), ("30 phút", 1800), ("1 tiếng", 3600), ("10 tiếng", 36000), ("1 ngày", 86400)]
    rows = []
    with market_lock:
        if market_paused:
            return [{"label": label, "seconds": secs, "paused": True} for label, secs in horizons]
        for sym in ASSETS:
            arr = market_plan.get(sym)
            if arr is None:
                continue
            for label, seconds in horizons:
                index = market_tick + seconds
                if index >= len(arr):
                    # Fail closed: do not label a clamped endpoint as an exact future price.
                    rows.append({"symbol": sym, "label": label, "seconds": seconds,
                                 "price": None, "change_pct": None, "paused": False,
                                 "error": "Đang nối dài lịch giá; thử tải lại."})
                    continue
                future_price = float(arr[index])
                current = market_prices[sym]
                rows.append({"symbol": sym, "label": label, "seconds": seconds,
                             "price": future_price,
                             "change_pct": ((future_price / current) - 1.0) * 100.0 if current else 0.0,
                             "paused": False})
    return rows


async def market_broadcaster() -> None:
    while True:
        await asyncio.sleep(MARKET_TICK_SECONDS)
        engine_step()
        snapshot = price_snapshot()
        dead: list[WebSocket] = []
        for ws in list(market_clients):
            try:
                await ws.send_json({"type": "market", "data": snapshot})
            except Exception:
                dead.append(ws)
        for ws in dead:
            market_clients.discard(ws)


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class RegisterBody(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(default="", max_length=50)


class AccountCreateBody(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(default="", max_length=50)
    role: str = "member"


class TradeBody(BaseModel):
    side: str
    symbol: str
    quantity: float = Field(gt=0, le=1_000_000_000)


class PauseBody(BaseModel):
    paused: bool


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "san-chung-khoan",
        "version": "0.2.3-persistent-db-trial",
        "trial_mode": "true" if os.getenv("TRIAL_MODE", "false").lower() == "true" else "false",
        "database_backend": "postgresql" if DATABASE_URL else "sqlite",
        "persistent_storage": bool(DATABASE_URL),
    }


@app.post("/api/register")
async def register(body: RegisterBody, request: Request) -> JSONResponse:
    """Public self-registration. New accounts are always ordinary members."""
    check_same_origin(request)
    username = body.username.strip()
    display_name = body.display_name.strip()[:50] or username
    client_ip = request.client.host if request.client else "unknown"
    attempt_key = hashlib.sha256(f"register|{client_ip}".encode()).hexdigest()
    now_ts = time.time()
    password_hash, salt = hash_password(body.password)
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires = utc_now() + timedelta(hours=SESSION_HOURS)

    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute("SELECT id FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
            if existing:
                raise HTTPException(409, "Tên đăng nhập đã tồn tại. Hãy chọn tên khác.")

            limit = conn.execute(
                "SELECT created_count,window_started FROM registration_limits WHERE attempt_key=?",
                (attempt_key,),
            ).fetchone()
            if limit and now_ts - float(limit["window_started"]) < 3600:
                created_count = int(limit["created_count"])
                window_started = float(limit["window_started"])
                if created_count >= 10:
                    raise HTTPException(429, "Địa chỉ mạng này đã tạo quá nhiều tài khoản trong 1 giờ. Hãy thử lại sau.")
                created_count += 1
            else:
                created_count = 1
                window_started = now_ts

            account_id = None
            for _ in range(1000):
                candidate = f"{secrets.randbelow(100_000_000):08d}"
                exists = conn.execute("SELECT 1 FROM users WHERE account_id=?", (candidate,)).fetchone()
                if not exists:
                    account_id = candidate
                    break
            if account_id is None:
                raise HTTPException(503, "Chưa tạo được ID tài khoản. Hãy thử lại.")

            cur = conn.execute(
                "INSERT INTO users(username,password_hash,salt,role,account_id,display_name,balance_cents,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (username, password_hash, salt, "member", account_id, display_name, 50_000_000, utc_now().isoformat()),
            )
            user_id = int(cur.lastrowid)
            conn.executemany(
                "INSERT INTO holdings(user_id,symbol,quantity) VALUES(?,?,0)",
                [(user_id, sym) for sym in ASSETS],
            )
            conn.execute(
                "INSERT INTO registration_limits(attempt_key,created_count,window_started) VALUES(?,?,?) "
                "ON CONFLICT(attempt_key) DO UPDATE SET created_count=excluded.created_count,window_started=excluded.window_started",
                (attempt_key, created_count, window_started),
            )
            conn.execute(
                "INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
                (token_hash, user_id, expires.isoformat(), utc_now().isoformat()),
            )
            user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    response = JSONResponse({"ok": True, "user": public_user(user), "message": "Tạo tài khoản thành công."})
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=COOKIE_SECURE,
                        samesite="strict", max_age=SESSION_HOURS * 3600, path="/")
    return response


@app.post("/api/login")
async def login(body: LoginBody, request: Request) -> JSONResponse:
    check_same_origin(request)
    client_ip = request.client.host if request.client else "unknown"
    attempt_key = hashlib.sha256(f"{client_ip}|{body.username.strip().casefold()}".encode()).hexdigest()
    now_ts = time.time()
    with db() as conn:
        limit = conn.execute("SELECT failures,window_started,locked_until FROM login_limits WHERE attempt_key=?", (attempt_key,)).fetchone()
        if limit and float(limit["locked_until"]) > now_ts:
            retry = max(1, int(float(limit["locked_until"]) - now_ts + 0.999))
            raise HTTPException(429, f"Đăng nhập tạm khóa. Thử lại sau {retry} giây.")
        user = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (body.username.strip(),)).fetchone()
        valid = bool(user and not user["disabled"] and verify_password(body.password, user["salt"], user["password_hash"]))
        if not valid:
            window_started = float(limit["window_started"]) if limit and now_ts - float(limit["window_started"]) < 600 else now_ts
            failures = int(limit["failures"]) + 1 if limit and now_ts - float(limit["window_started"]) < 600 else 1
            locked_until = now_ts + 900 if failures >= 5 else 0
            conn.execute("INSERT INTO login_limits(attempt_key,failures,window_started,locked_until) VALUES(?,?,?,?) ON CONFLICT(attempt_key) DO UPDATE SET failures=excluded.failures,window_started=excluded.window_started,locked_until=excluded.locked_until",
                         (attempt_key, failures, window_started, locked_until))
            if locked_until:
                raise HTTPException(429, "Đăng nhập tạm khóa 15 phút do nhập sai quá nhiều lần.")
            raise HTTPException(401, "Tên đăng nhập hoặc mật khẩu không đúng.")
        conn.execute("DELETE FROM login_limits WHERE attempt_key=?", (attempt_key,))
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires = utc_now() + timedelta(hours=SESSION_HOURS)
        conn.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
                     (token_hash, user["id"], expires.isoformat(), utc_now().isoformat()))
    response = JSONResponse({"ok": True, "user": public_user(user)})
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=COOKIE_SECURE,
                        samesite="strict", max_age=SESSION_HOURS * 3600, path="/")
    return response


def public_user(user: sqlite3.Row) -> dict[str, Any]:
    return {"username": user["username"], "display_name": user["display_name"],
            "account_id": user["account_id"], "role": user["role"]}


@app.post("/api/logout")
async def logout(request: Request) -> JSONResponse:
    check_same_origin(request)
    token = request.cookies.get(COOKIE_NAME)
    if token:
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
    return response


@app.get("/api/me")
async def me(request: Request) -> dict[str, Any]:
    user = get_session_user(request)
    return public_user(user)


@app.get("/api/market")
async def market(request: Request) -> dict[str, Any]:
    get_session_user(request)
    return price_snapshot()


@app.get("/api/portfolio")
async def portfolio(request: Request) -> dict[str, Any]:
    user = get_session_user(request)
    with db() as conn:
        rows = conn.execute("SELECT symbol,quantity FROM holdings WHERE user_id=?", (user["id"],)).fetchall()
        trades = conn.execute("SELECT side,symbol,quantity,price,total_cents,created_at FROM trades WHERE user_id=? ORDER BY id DESC LIMIT 20", (user["id"],)).fetchall()
        balance = conn.execute("SELECT balance_cents FROM users WHERE id=?", (user["id"],)).fetchone()[0]
    holdings = {r["symbol"]: r["quantity"] for r in rows}
    with market_lock:
        value_cents = sum(int(round(holdings.get(sym, 0.0) * market_prices[sym] * 100)) for sym in ASSETS)
        net_worth = int(balance) + value_cents
    return {"balance_cents": balance, "balance": balance / 100,
            "holdings": holdings, "holdings_value": value_cents / 100,
            "net_worth": net_worth / 100, "trades": [dict(r) for r in trades]}


@app.post("/api/trade")
async def trade(body: TradeBody, request: Request) -> dict[str, Any]:
    check_same_origin(request)
    user = get_session_user(request)
    if body.side not in {"buy", "sell"} or body.symbol not in ASSETS or not math.isfinite(body.quantity):
        raise HTTPException(400, "Lệnh giao dịch không hợp lệ.")
    qty = round(float(body.quantity), 6)
    if qty <= 0:
        raise HTTPException(400, "Khối lượng phải lớn hơn 0.")
    with market_lock:
        price = market_prices[body.symbol]
        opened = is_market_open(body.symbol, local_now())
        paused = market_paused
    if paused:
        raise HTTPException(409, "Toàn sàn đang tạm dừng.")
    if not opened:
        raise HTTPException(409, "Tài sản truyền thống đang nghỉ giao dịch.")
    total_cents = int((Decimal(str(price)) * Decimal(str(qty)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if total_cents <= 0:
        raise HTTPException(400, "Giá trị giao dịch quá nhỏ.")
    now = utc_now().isoformat()
    try:
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account = conn.execute("SELECT balance_cents FROM users WHERE id=?", (user["id"],)).fetchone()
            holding = conn.execute("SELECT quantity FROM holdings WHERE user_id=? AND symbol=?", (user["id"], body.symbol)).fetchone()
            current_qty = float(holding[0]) if holding else 0.0
            if body.side == "buy":
                if account[0] < total_cents:
                    raise HTTPException(409, "Số dư USD ảo không đủ.")
                conn.execute("UPDATE users SET balance_cents=balance_cents-? WHERE id=?", (total_cents, user["id"]))
                new_qty = round(current_qty + qty, 6)
            else:
                if current_qty + 1e-9 < qty:
                    raise HTTPException(409, "Không đủ tài sản để bán.")
                conn.execute("UPDATE users SET balance_cents=balance_cents+? WHERE id=?", (total_cents, user["id"]))
                new_qty = round(max(0.0, current_qty - qty), 6)
            conn.execute("INSERT INTO holdings(user_id,symbol,quantity) VALUES(?,?,?) ON CONFLICT(user_id,symbol) DO UPDATE SET quantity=excluded.quantity",
                         (user["id"], body.symbol, new_qty))
            conn.execute("INSERT INTO trades(user_id,side,symbol,quantity,price,total_cents,created_at) VALUES(?,?,?,?,?,?,?)",
                         (user["id"], body.side, body.symbol, qty, price, total_cents, now))
            conn.execute("COMMIT")
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        raise HTTPException(500, "Không lưu được giao dịch; không thay đổi số dư.") from exc
    return {"ok": True, "side": body.side, "symbol": body.symbol, "quantity": qty,
            "price": price, "total": total_cents / 100,
            "message": "Mua thành công." if body.side == "buy" else "Bán thành công."}


@app.get("/api/inbox")
async def inbox(request: Request) -> dict[str, Any]:
    user = get_session_user(request)
    with db() as conn:
        rows = conn.execute("SELECT id,title,body,created_at,is_read FROM inbox WHERE user_id=? ORDER BY id DESC LIMIT 50", (user["id"],)).fetchall()
        conn.execute("UPDATE inbox SET is_read=1 WHERE user_id=?", (user["id"],))
    return {"items": [dict(r) for r in rows]}


@app.get("/api/admin/forecast")
async def forecast(request: Request) -> dict[str, Any]:
    require_owner(request)
    return {"items": admin_forecasts()}


@app.post("/api/admin/market/pause")
async def pause_market(body: PauseBody, request: Request) -> dict[str, Any]:
    global market_paused, market_tick, market_anchor
    check_same_origin(request)
    require_owner(request)
    with market_lock:
        market_paused = body.paused
        if not body.paused:
            # Resuming continues from current prices and prepares a matching new path.
            market_plan.clear()
            market_tick = 0
            market_anchor = local_now()
    persist_market_state()
    return {"ok": True, "paused": market_paused}


@app.get("/api/owner/accounts")
async def owner_accounts(request: Request) -> dict[str, Any]:
    require_owner(request)
    with db() as conn:
        rows = conn.execute("SELECT id,username,display_name,account_id,role,balance_cents,created_at,disabled FROM users ORDER BY id").fetchall()
    return {"items": [dict(r) for r in rows]}


@app.post("/api/owner/accounts")
async def owner_create_account(body: AccountCreateBody, request: Request) -> dict[str, Any]:
    check_same_origin(request)
    require_owner(request)
    if body.role not in {"member", "admin"}:
        raise HTTPException(400, "Owner chỉ có thể tạo tài khoản Thành viên hoặc Admin; không thể tạo thêm Owner.")
    try:
        result = create_account(body.username, body.password, body.role, body.display_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Tên đăng nhập đã tồn tại.") from exc
    return {"ok": True, "account": result}


@app.websocket("/ws")
async def websocket_market(websocket: WebSocket) -> None:
    token = websocket.cookies.get(COOKIE_NAME)
    valid = False
    if token:
        with db() as conn:
            row = conn.execute("SELECT 1 FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.disabled=0",
                               (hashlib.sha256(token.encode()).hexdigest(), utc_now().isoformat())).fetchone()
            valid = row is not None
    if not valid:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    market_clients.add(websocket)
    try:
        await websocket.send_json({"type": "market", "data": price_snapshot()})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        market_clients.discard(websocket)


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def main() -> None:
    import uvicorn
    uvicorn.run("main:app", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")), reload=False)


import extended  # noqa: E402,F401 - registers the full web feature set and schema migration.

if __name__ == "__main__":
    main()
