SÀN CHỨNG KHOÁN - WEB EDITION 0.2.1-TRIAL
=================================

ĐÂY LÀ GÌ?
- Bản web nâng cấp từ chương trình desktop Pygame do người dùng cung cấp.
- Tên giao diện thống nhất: SÀN CHỨNG KHOÁN.
- Thị trường dùng chung cho các tài khoản đang kết nối cùng một máy chủ.
- Tất cả số dư là USD ẢO; không có thanh toán, nạp/rút tiền thật hay kết nối ngân hàng.
- Đây là bản chuyển đổi nghiệp vụ lên giao diện web, không phải bản sao pixel-perfect của mọi cửa sổ Pygame.

CÁC NHÓM CHỨC NĂNG ĐÃ CÓ TRONG WEB 0.2
1. Tài khoản và bảo mật
   - Owner cấp tài khoản; không có đăng ký công khai.
   - ID tài khoản 8 chữ số.
   - Mật khẩu băm PBKDF2-HMAC-SHA256 + salt; không lưu chữ thường.
   - Cookie HttpOnly, SameSite=Strict; giới hạn đăng nhập sai.
   - Vai trò: OWNER, SPADMIN, ADMIN, TỌA ĐỘ, THÀNH VIÊN.
   - Owner xét duyệt yêu cầu Admin, thăng/hạ vai trò và đặt hạn mức ghế.

2. Thị trường và giao dịch
   - 10 mã: GOLD, SILVER, COPPER, PLATINUM, OIL, BRENT, GAS, BTC, ETH, SOL.
   - Giá thị trường dùng chung, cập nhật qua WebSocket.
   - Biểu đồ web và các đường MA5/10/20/50/200.
   - Mua/bán bằng USD ảo; danh mục và lịch sử giao dịch riêng.
   - Owner tạm dừng/chạy toàn thị trường.
   - Có lưu giá/trạng thái thị trường và lập lịch dự báo dùng chung.
   - Owner có dự báo 6 mốc: 5 giây, 5 phút, 30 phút, 1 giờ, 10 giờ, 1 ngày.
   - Dự báo Premium theo cấp; RUBY có lựa chọn 1/3/5 giây.

3. Premium và đặc quyền
   - ĐỒNG, BẠC, VÀNG, BẠCH KIM, KIM CƯƠNG; gói 3 ngày/1 tháng/1 năm.
   - Thưởng ngày; gói năm có cơ chế thưởng theo quy tắc gói.
   - Tặng Premium có phụ phí 15%.
   - SPADMIN xin Owner duyệt RUBY; Owner cấp RUBY, lượt miễn phạt được lưu theo vòng đời.
   - Owner cấp LỤC BẢO; gói tặng người khác 1 ngày và có cơ chế khôi phục gói trước.
   - Lệnh mua đủ điều kiện trong thời gian LỤC BẢO được theo dõi để hoàn 80% phần lỗ đã thực hiện.
   - TỌA ĐỘ có ĐỒNG miễn phí trong thời gian giữ vai trò.

4. Ví và nghĩa vụ
   - Chuyển tiền xác minh ID 8 số và tên hiển thị; người nhận nhận 85%, 15% bị khấu trừ.
   - Cho nợ chuyển đủ số tiền và tạo hồ sơ khoản nợ.
   - Công cụ điều chỉnh ví Owner và ví của tài khoản quản trị được phân quyền.
   - Phạt theo số tiền hoặc phần trăm; có hạn mức theo vai trò, nợ sàn và luồng yêu cầu xử lý phạt RUBY.

5. Cộng đồng và kiểm duyệt
   - CHAT TỔNG, CHAT THÀNH VIÊN, CHAT QTV; quyền truy cập theo vai trò/cấp thành viên.
   - Gửi báo cáo, trao đổi trong hồ sơ và quyết định xử lý.
   - Khóa/mở khóa tài khoản theo phạm vi vai trò.
   - Hòm thư thông báo.

6. Hồ sơ và quản trị
   - Hồ sơ tên hiển thị, tuổi/khu vực/giới thiệu tùy chọn.
   - Cài đặt tắt âm lượng/thay đổi mức âm lượng được lưu.
   - Owner xem danh sách tài khoản, cấp/thu hồi quyền Premium, hỗ trợ tài khoản có ghi nhật ký.
   - Owner xem nhật ký quản trị và tải bản sao lưu JSON.
   - Không có tính năng xem mật khẩu hay đăng nhập bằng mật khẩu của tài khoản khác.

CÁC PHẦN CHƯA TƯƠNG ĐƯƠNG HOÀN TOÀN VỚI PYGAME
- Giao diện đã được thiết kế lại cho trình duyệt; bố cục/cửa sổ kéo thả, modal và thao tác chuột của Pygame không được sao chép nguyên pixel.
- Cú sốc giá cực đoan theo từng sự kiện và lớp cảnh báo/overlay đặc biệt của desktop chưa được chuyển đầy đủ.
- Phiên hỗ trợ Owner hiện là phiên kiểm tra thông tin có ghi nhật ký, chưa chuyển sang cơ chế thao tác mọi màn hình dưới danh tính tài khoản đích như desktop.
- Web đã có tải bản sao lưu; chức năng khôi phục bản sao lưu từ giao diện web chưa có.
- Âm lượng/tắt âm thanh được lưu, nhưng âm thanh thông báo trình duyệt chưa tương đương chuông và overlay Pygame.
- Một số hộp thoại/slider quản trị, xác nhận hồ sơ và quy trình phụ trong desktop đã được giản lược thành biểu mẫu web.
- Tệp accounts_sandbox.json của Pygame không tự động nhập vào SQLite. Cần quy trình chuyển đổi riêng nếu muốn mang tài khoản desktop sang web.
- Không có đồng bộ dữ liệu tự động giữa bản desktop và web.
- Bản Render Free chỉ là môi trường thử nghiệm: dịch vụ ngủ sau 15 phút không có inbound traffic và có thể khởi động lại.
- Render Free không có Persistent Disk; SQLite đặt ở /tmp có thể mất dữ liệu khi ngủ/khởi động lại/triển khai. Không dùng dữ liệu Owner thật.
- Có bootstrap Owner qua biến môi trường bí mật INITIAL_OWNER_USERNAME / INITIAL_OWNER_PASSWORD để tạo lại Owner khi database thử nghiệm trống.
- Đã kiểm thử Python/API; chưa chạy kiểm thử trình duyệt end-to-end trên Chrome/Edge thật.

CÀI TRÊN WINDOWS
1. Giải nén ZIP vào một thư mục mới, ví dụ Desktop\san_chung_khoan_web_full.
2. Cài Python 3.11 trở lên và bật tùy chọn thêm Python vào PATH khi cài.
3. Chạy install_windows.bat để tạo môi trường riêng .venv và cài thư viện.
4. Nếu muốn giữ tài khoản Owner đã tạo trong bản web thử nghiệm 0.1:
   - Dừng server cũ bằng Ctrl+C ở cửa sổ CMD đang chạy bản cũ.
   - Sao chép TOÀN BỘ thư mục data từ bản cũ sang thư mục san_chung_khoan_web_full.
   - Chỉ sao chép, không di chuyển/xóa dữ liệu bản cũ.
   - Khi đã sao chép data cũ, KHÔNG chạy create_owner_windows.bat; tài khoản Owner đã nằm trong database.
5. Nếu muốn database mới hoàn toàn, bỏ qua bước sao chép data và chạy create_owner_windows.bat để tạo Owner mới.
6. Chạy run_windows.bat.
7. Mở Chrome/Edge tại http://127.0.0.1:8001.
8. Đăng nhập. Giữ cửa sổ CMD chạy trong khi sử dụng web.

LƯU Ý NÂNG CẤP TỪ BẢN 0.1
- Bản 0.1 dùng cổng 8000, bản 0.2 dùng cổng 8001 để tránh nhầm server.
- Bản 0.2 có migration SQLite khi khởi động. Hãy sao chép thư mục data trước khi nâng cấp.
- Không ghi đè thư mục bản 0.1. Giữ bản cũ đến khi xác nhận bản mới hoạt động ổn.
- Không chia sẻ tệp database hoặc mật khẩu Owner.

CẤU TRÚC CHÍNH
- main.py: FastAPI, đăng nhập, database, engine thị trường.
- extended.py: API và nghiệp vụ bổ sung từ bản desktop.
- static/: giao diện HTML/CSS/JavaScript.
- bootstrap_owner.py: tạo Owner đầu tiên.
- tests/: kiểm thử tự động.

TRIỂN KHAI INTERNET
- render.yaml hiện cấu hình Render Free cho BẢN THỬ NGHIỆM, không phải môi trường sản xuất hay 24/7.
- Dịch vụ Free tự ngủ sau 15 phút không có inbound traffic; request định kỳ khi người chơi đang đăng nhập sẽ giữ tương tác hoạt động, nhưng không bảo đảm luôn bật.
- Free không có Persistent Disk. SQLite ở /tmp có thể mất dữ liệu sau spin-down, restart hoặc redeploy. Chỉ dùng tài khoản demo.
- Chưa chuyển SQLite sang PostgreSQL; không được giả định dữ liệu trên Render Free sẽ bền vững. Render Free Postgres cũng có thời hạn và giới hạn riêng.
- SQLite và trạng thái thị trường hiện thiết kế cho một tiến trình máy chủ; không chạy nhiều bản sao cùng dùng một SQLite file qua mạng.
- Khi dùng Internet, chỉ nhập dữ liệu mô phỏng; không dùng mật khẩu trùng với dịch vụ khác hoặc dữ liệu cá nhân quan trọng.
- Chỉ triển khai dữ liệu mô phỏng, không dùng tiền thật hoặc thông tin tài chính cá nhân.
