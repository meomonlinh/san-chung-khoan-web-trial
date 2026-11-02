from pathlib import Path


def test_redundant_ui_notes_are_removed_and_important_notices_remain():
    html = (Path(__file__).parents[1] / "static" / "index.html").read_text(encoding="utf-8")
    js = (Path(__file__).parents[1] / "static" / "app.js").read_text(encoding="utf-8")

    assert "Giá và thưởng theo bản code chính" not in html
    assert "Gói năm tăng thưởng ngày thường 20%" not in html
    assert "Tất cả giá và số dư đều là số liệu giả lập" not in html
    assert "NET WORTH" not in html
    assert "MEMBERSHIP CENTER" not in html
    assert "ACCOUNT TO ACCOUNT" not in html
    assert "TRUST & SAFETY" not in html
    assert "OWNER CONSOLE" not in html
    assert 'id="forecastHint"' not in html
    assert "forecastHint" not in js

    # Keep warnings and explanations that change how users should act.
    assert "Chỉ dùng USD ảo" in html
    assert "người nhận 85%" in html
    assert "Phụ phí 15%" in html
    assert "Xác minh ID 8 chữ số" in html
    assert "dự báo ${item.forecast}s" in js
    assert "bảo vệ 80% khoản lỗ" in js
