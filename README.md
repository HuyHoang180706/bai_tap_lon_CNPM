# Hệ thống quản lý chuyến bay (Python Flask + MySQL)

Viết theo cấu trúc mẫu của môn học: một gói `flightapp` gồm `__init__.py` (app, db, login), `models.py`,
`dao.py`, `utils.py`, `index.py` (route cho khách hàng và nhân viên), `admin.py` (Flask-Admin cho quản trị viên),
thư mục `templates/layout` (base, header, footer), `static/js` (main.js, draw.js).

## Cách chạy

1. Tạo CSDL trong MySQL:

   ```sql
   CREATE DATABASE flightdb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   ```

2. Mở `flightapp/__init__.py`, sửa `root:root` trong `SQLALCHEMY_DATABASE_URI` thành user và mật khẩu MySQL của bạn.

3. Cài thư viện (nên dùng môi trường ảo), chạy từ thư mục gốc của dự án (thư mục chứa `flightapp`):

   ```bat
   pip install -r requirements.txt
   ```

4. Tạo bảng và nạp dữ liệu mẫu (sân bay, hạng vé, tuyến bay, tài khoản, chuyến bay và vé mẫu của vài tháng):

   ```bat
   python -m flightapp.models
   ```

   Tham số tùy chọn: `nodemo` (không tạo chuyến bay và vé mẫu), `reset` (xóa hết rồi tạo lại).
   Ví dụ làm lại từ đầu: `python -m flightapp.models reset`

5. Chạy website:

   ```bat
   python -m flightapp.index
   ```

   Mở http://127.0.0.1:5000 (trang quản trị ở http://127.0.0.1:5000/admin).
   Trong PyCharm có thể chạy trực tiếp file `flightapp/index.py` (nhớ đặt thư mục gốc dự án là Content Root).

## Tài khoản mẫu (mật khẩu đều là 123456)

| Vai trò        | Tên đăng nhập |
|----------------|---------------|
| Quản trị viên  | admin         |
| Nhân viên      | staff         |
| Khách hàng     | customer      |

Khách hàng tự đăng ký ở `/register`. Muốn có thêm nhân viên: đăng ký như khách hàng, rồi quản trị viên vào
Admin > Tài khoản, chọn tài khoản và dùng thao tác "Cấp quyền nhân viên".

## Chức năng theo từng yêu cầu của đề

| Yêu cầu | Ai dùng | Đường dẫn | Ghi chú |
|---|---|---|---|
| 1. Đặt vé online | Khách hàng | `/` (tìm chuyến), `/booking/<id>`, `/payment/<mã vé>`, `/my-tickets` | Chỉ hiện chuyến còn cách giờ bay từ 12 giờ và còn chỗ. Giữ chỗ 15 phút, vé chỉ có hiệu lực sau khi thanh toán thành công |
| 2. Bán vé tại quầy | Nhân viên | `/staff/sell` (tra cứu theo mã, tuyến, ngày bay), `/staff/sell/<id>`, `/staff/tickets` | Chỉ bán chuyến còn từ 4 giờ trước giờ bay và còn chỗ. Biểu mẫu VÉ CHUYẾN BAY có nút in |
| 3. Lập lịch chuyến bay | Nhân viên, quản trị | `/staff/flights/new` | Biểu mẫu LỊCH CHUYẾN BAY: tối đa 2 sân bay trung gian, dừng 20-30 phút, bay tối thiểu 30 phút |
| 4. Thống kê, báo cáo | Quản trị | Admin > Thống kê | Bảng BÁO CÁO DOANH THU THEO THÁNG, biểu đồ cột và tròn vẽ bằng Chart.js (`draw.js`) |
| 5. Thay đổi quy định | Quản trị | Admin > Quy định, Danh mục (sân bay, hạng vé, tuyến bay, bảng giá vé) | Mọi con số của đề lưu trong bảng `regulation`, không viết cứng trong code |
| Quản lý tuyến bay, chuyến bay | Quản trị | Admin > Danh mục > Tuyến bay, Admin > Quản lý bay | Thêm, sửa, xóa, tìm kiếm. Thêm chuyến bay bằng trang lập lịch. Hủy chuyến bằng thao tác "Hủy chuyến" |

## Quy ước nghiệp vụ (nên ghi vào báo cáo)

- "Đặt vé trước 12 giờ, bán vé trước 4 giờ" hiểu là phải còn cách giờ khởi hành ít nhất N giờ. Hai số này sửa được ở mục Quy định.
- Doanh thu tháng = tổng giá vé đã thanh toán của các chuyến bay khởi hành trong tháng (không tính chuyến bị hủy).
  Số lượt bay = số chuyến bay của tuyến trong tháng. Tỷ lệ = doanh thu tuyến / tổng doanh thu.
- Giá vé lưu theo (tuyến bay, hạng vé) và được chép vào từng vé khi bán, nên đổi bảng giá không làm đổi giá vé cũ.
- Một lần đặt là một vé một hành khách.
- Cổng thanh toán là giả lập (có chọn phương thức và kết quả thành công hoặc thất bại để thử).
  Muốn dùng VNPay/MoMo sandbox chỉ cần thay hàm `dao.pay_ticket`.
- Hủy chuyến bay đã có vé thanh toán: hệ thống chỉ cảnh báo, hoàn tiền chưa có trong đề nên làm thủ công.
- Giờ hệ thống lấy theo giờ của máy chạy server.

## Chống bán trùng ghế

Khi bán hoặc đặt vé, `dao.add_ticket` khóa dòng `flight_seat` của chuyến bay và hạng vé đó (`SELECT ... FOR UPDATE`),
đếm lại số vé đã thanh toán cộng vé đang giữ chỗ còn hạn, rồi mới tạo vé. Hai người cùng mua ghế cuối cùng thì chỉ một người thành công.
Mức cô lập `READ COMMITTED` được đặt trong `__init__.py` để việc đếm sau khi khóa luôn thấy dữ liệu mới nhất.

## Lưu ý theo đúng mẫu của môn học

- Mật khẩu băm bằng MD5 và các form chưa có CSRF token, giống dự án mẫu. Đủ cho bài tập, không dùng cho hệ thống thật.
- Muốn bảo mật tốt hơn: đổi `_md5` trong `dao.py` sang `werkzeug.security.generate_password_hash`
  và `check_password_hash`, và thêm Flask-WTF CSRFProtect (cần đặt Flask-Admin dùng form có CSRF).

## Kiểm thử

Kiểm thử tự động các quy tắc nghiệp vụ trong `utils.py` (không cần MySQL):

```bat
python -m unittest discover -s tests -v
```

Kịch bản kiểm thử thủ công nên chạy trước khi nộp:

1. Đăng nhập `staff`, mở Lập lịch chuyến bay: thời gian bay 20 phút phải báo lỗi; thời gian dừng 15 hoặc 40 phút phải báo lỗi; lập lịch hợp lệ thì thành công.
2. Đăng nhập `admin`, vào Admin > Quy định, đổi "thời gian bay tối thiểu" thành 60 rồi lập lịch bay 45 phút: phải báo lỗi. Đổi "số sân bay trung gian tối đa" thành 1: biểu mẫu chỉ còn 1 dòng.
3. Đăng nhập `staff`, vào Bán vé: chuyến sẽ bay trong chưa tới 4 giờ báo "Quá hạn bán vé"; hạng hết chỗ không chọn được. Bán một vé rồi in vé.
4. Đăng nhập `customer`, đặt vé: chuyến sẽ bay trong chưa tới 12 giờ không xuất hiện. Thanh toán thất bại một lần rồi thử lại thành công. Để hết 15 phút không thanh toán thì vé chuyển "Hết hạn giữ chỗ" và ghế được nhả.
5. Mở 2 trình duyệt cùng đặt ghế cuối của một hạng: chỉ một người thành công.
6. Admin > Thống kê, chọn các tháng khác nhau: tổng tỷ lệ bằng 100%, tổng doanh thu khớp tổng cột.
7. Đăng nhập `customer` rồi gõ thẳng `/staff/sell` hoặc `/admin`: phải bị từ chối quyền.
