import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
import pytest
import main


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "test.sqlite3")
    main.market_prices = {sym: float(row["price"]) for sym, row in main.ASSETS.items()}
    main.market_history = {sym: main.deque([float(row["price"])] * 20, maxlen=main.HISTORY_POINTS) for sym, row in main.ASSETS.items()}
    main.market_paused = False
    main.market_tick = 0
    main.market_plan = {}
    main.init_db()
    main.create_account("owner_test", "OwnerPassword!234", "owner", "Owner Test")
    with TestClient(main.app) as test_client:
        yield test_client
