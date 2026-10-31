"""Create the first Owner account for a fresh web installation."""
from getpass import getpass
import sqlite3

from main import DB_PATH, create_account, init_db


def main() -> None:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        owners = conn.execute("SELECT COUNT(*) FROM users WHERE role='owner'").fetchone()[0]
    if owners:
        print("Đã có tài khoản Owner. Không tạo thêm Owner mới.")
        return
    print("=== THIẾT LẬP OWNER CHO SÀN WEB ===")
    username = input("Tên đăng nhập Owner: ").strip()
    password = getpass("Mật khẩu (10–128 ký tự): ")
    confirm = getpass("Nhập lại mật khẩu: ")
    if password != confirm:
        raise SystemExit("Hai mật khẩu không khớp.")
    display_name = input("Tên hiển thị (có thể bỏ trống): ").strip() or username
    try:
        account = create_account(username, password, "owner", display_name)
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise SystemExit(f"Không tạo được Owner: {exc}") from exc
    print("Đã tạo Owner thành công.")
    print(f"Tên đăng nhập: {account['username']}")
    print(f"ID tài khoản: {account['account_id']}")
    print("Mật khẩu không được in ra màn hình và không lưu dạng chữ thường.")


if __name__ == "__main__":
    main()
