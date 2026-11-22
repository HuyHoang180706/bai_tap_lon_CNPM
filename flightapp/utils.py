
import re
from datetime import datetime, timedelta

FLIGHT_CODE_RE = re.compile(r"^[A-Z0-9-]{3,20}$")
AIRPORT_CODE_RE = re.compile(r"^[A-Z]{3}$")
PHONE_RE = re.compile(r"^0\d{9,10}$")
ID_RE = re.compile(r"^(\d{9}|\d{12})$")            # CMND 9 số hoặc CCCD 12 số
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{4,30}$")
MAX_FLIGHT_MINUTES = 24 * 60

PAYMENT_METHODS = {
    "vnpay": "VNPay QR",
    "momo": "Ví MoMo",
    "atm": "Thẻ ATM nội địa",
    "card": "Thẻ quốc tế Visa/Mastercard",
}


class SaleError(Exception):
    """Lỗi nghiệp vụ có thông báo hiển thị trực tiếp cho người dùng."""


# ----------------------------------------------------------------------
# Chuyển đổi dữ liệu nhập
# ----------------------------------------------------------------------
def parse_int(value, default=None):
    """'12' -> 12; rỗng hoặc sai định dạng -> default."""
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except ValueError:
        return default


def parse_datetime_local(value):
    """Chuỗi từ <input type="datetime-local"> -> datetime (hoặc None)."""
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime((value or "").strip(), fmt)
        except ValueError:
            continue
    return None


def parse_date(value):
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def read_flight_filters(args):
    """Đọc bộ lọc tra cứu chuyến bay từ request.args."""
    return {
        "kw": (args.get("kw") or "").strip(),
        "origin_id": parse_int(args.get("origin_id")),
        "destination_id": parse_int(args.get("destination_id")),
        "date_from": parse_date(args.get("date_from")),
        "date_to": parse_date(args.get("date_to")),
    }


# ----------------------------------------------------------------------
# Hiển thị (đăng ký thành bộ lọc Jinja ở index.py)
# ----------------------------------------------------------------------
def format_vnd(value):
    """1500000 -> '1.500.000 ₫'"""
    if value is None:
        return ""
    return f"{int(value):,}".replace(",", ".") + " ₫"


def format_dt(value, fmt="%d/%m/%Y %H:%M"):
    return value.strftime(fmt) if value else ""


def format_duration(minutes):
    """130 -> '2 giờ 10 phút'"""
    if minutes is None:
        return ""
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f"{hours} giờ {mins} phút"
    return f"{hours} giờ" if hours else f"{mins} phút"


# ----------------------------------------------------------------------
# Thời hạn bán vé / đặt vé
# ----------------------------------------------------------------------
def sale_deadline(departure, min_hours):
    """Thời điểm muộn nhất được bán/đặt vé = giờ khởi hành trừ N giờ."""
    return departure - timedelta(hours=min_hours)


def is_sale_open(departure, now, min_hours):
    """Đúng quy định khi còn cách giờ khởi hành ít nhất `min_hours` giờ."""
    return now <= sale_deadline(departure, min_hours)


# ----------------------------------------------------------------------
# Tài khoản & hành khách
# ----------------------------------------------------------------------
def validate_account(name, username, password, confirm):
    """Kiểm tra định dạng thông tin đăng ký (chưa kiểm tra trùng trong CSDL)."""
    errors = []
    if len((name or "").strip()) < 2:
        errors.append("Vui lòng nhập họ và tên.")
    if not USERNAME_RE.match((username or "").strip()):
        errors.append("Tên đăng nhập gồm 4-30 ký tự: chữ không dấu, số hoặc dấu gạch dưới.")
    if len(password or "") < 6:
        errors.append("Mật khẩu phải có ít nhất 6 ký tự.")
    elif password != confirm:
        errors.append("Mật khẩu nhập lại không khớp.")
    return errors


def clean_passenger(name, id_number, phone):
    """Chuẩn hóa thông tin trên VÉ CHUYẾN BAY. Trả về (errors, cleaned)."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    id_number = re.sub(r"\s", "", id_number or "")
    phone = re.sub(r"[\s.\-]", "", phone or "")

    errors = []
    if len(name) < 2:
        errors.append("Vui lòng nhập họ tên hành khách.")
    elif len(name) > 100:
        errors.append("Họ tên hành khách tối đa 100 ký tự.")
    if not ID_RE.match(id_number):
        errors.append("CMND/CCCD phải gồm 9 hoặc 12 chữ số.")
    if not PHONE_RE.match(phone):
        errors.append("Số điện thoại phải bắt đầu bằng 0 và có 10-11 chữ số.")
    return errors, {"passenger_name": name, "id_number": id_number, "phone": phone}


# ----------------------------------------------------------------------
# Lập lịch chuyến bay (Yêu cầu 3)
# ----------------------------------------------------------------------
def read_flight_form(form, max_stops, class_ids):
    """Đọc biểu mẫu LỊCH CHUYẾN BAY thành dict dữ liệu đã chuyển kiểu."""
    seats = {cid: parse_int(form.get(f"seats_{cid}") or "0") for cid in class_ids}
    stops = []
    for i in range(1, max_stops + 1):
        airport_id = parse_int(form.get(f"stop_airport_{i}"))
        minutes_raw = (form.get(f"stop_minutes_{i}") or "").strip()
        note = (form.get(f"stop_note_{i}") or "").strip()
        if airport_id is None and not minutes_raw and not note:
            continue                                   # dòng bỏ trống
        stops.append({"order": len(stops) + 1, "airport_id": airport_id,
                      "minutes": parse_int(minutes_raw), "note": note})
    return {
        "code": (form.get("code") or "").strip().upper(),
        "origin_id": parse_int(form.get("origin_id")),
        "destination_id": parse_int(form.get("destination_id")),
        "departure": parse_datetime_local(form.get("departure_time")),
        "duration": parse_int(form.get("duration_minutes")),
        "seats": seats,
        "stops": stops,
    }


def validate_flight(*, code, origin_id, destination_id, departure, duration, seats, stops,
                    reg, now, class_names, require_future=True):
    """Kiểm tra biểu mẫu LỊCH CHUYẾN BAY theo bảng quy định `reg`. Trả về danh sách lỗi.

    seats: {class_id: số ghế (int hoặc None nếu nhập sai)}
    stops: [{"order": 1, "airport_id": int|None, "minutes": int|None, "note": str}, ...]
    class_names: {class_id: "Hạng 1"} để ghi thông báo lỗi
    """
    errors = []

    if not FLIGHT_CODE_RE.match(code or ""):
        errors.append("Mã chuyến bay gồm 3-20 ký tự: chữ in hoa, số hoặc dấu gạch ngang (ví dụ VN101).")

    if origin_id is None or destination_id is None:
        errors.append("Vui lòng chọn sân bay đi và sân bay đến.")
    elif origin_id == destination_id:
        errors.append("Sân bay đi và sân bay đến phải khác nhau.")

    if departure is None:
        errors.append("Vui lòng nhập ngày giờ khởi hành hợp lệ.")
    elif require_future and departure <= now:
        errors.append("Ngày giờ khởi hành phải ở tương lai.")

    if duration is None:
        errors.append("Vui lòng nhập thời gian bay (số phút).")
    elif duration < reg.min_flight_minutes:
        errors.append(f"Thời gian bay tối thiểu là {reg.min_flight_minutes} phút.")
    elif duration > MAX_FLIGHT_MINUTES:
        errors.append(f"Thời gian bay không quá {MAX_FLIGHT_MINUTES} phút.")

    for class_id, count in seats.items():
        label = class_names.get(class_id, f"hạng {class_id}")
        if count is None or count < 0:
            errors.append(f"Số ghế {label} phải là số nguyên không âm.")
    if seats and all((c or 0) == 0 for c in seats.values()) \
            and not any(c is None or c < 0 for c in seats.values()):
        errors.append("Chuyến bay phải có ít nhất một ghế ở một hạng vé.")

    if len(stops) > reg.max_intermediate_airports:
        errors.append(f"Tối đa {reg.max_intermediate_airports} sân bay trung gian.")
    seen = set()
    for stop in stops:
        label = f"Sân bay trung gian {stop['order']}"
        airport_id = stop["airport_id"]
        if airport_id is None:
            errors.append(f"{label}: vui lòng chọn sân bay.")
        else:
            if airport_id in (origin_id, destination_id):
                errors.append(f"{label}: không được trùng sân bay đi hoặc sân bay đến.")
            if airport_id in seen:
                errors.append(f"{label}: sân bay này đã được chọn ở dòng khác.")
            seen.add(airport_id)
        minutes = stop["minutes"]
        if minutes is None or not (reg.min_stop_minutes <= minutes <= reg.max_stop_minutes):
            errors.append(f"{label}: thời gian dừng phải từ {reg.min_stop_minutes} đến "
                          f"{reg.max_stop_minutes} phút.")
        if len(stop.get("note", "")) > 255:
            errors.append(f"{label}: ghi chú tối đa 255 ký tự.")
    return errors


# ----------------------------------------------------------------------
# Thay đổi quy định (Yêu cầu 5)
# ----------------------------------------------------------------------
# (tên cột, nhãn hiển thị, giá trị nhỏ nhất)
REG_FIELDS = [
    ("max_airports", "Số sân bay tối đa", 2),
    ("min_flight_minutes", "Thời gian bay tối thiểu (phút)", 1),
    ("max_intermediate_airports", "Số sân bay trung gian tối đa", 0),
    ("min_stop_minutes", "Thời gian dừng tối thiểu (phút)", 1),
    ("max_stop_minutes", "Thời gian dừng tối đa (phút)", 1),
    ("online_booking_min_hours", "Đặt vé online trước giờ bay ít nhất (giờ)", 0),
    ("counter_sale_min_hours", "Bán vé tại quầy trước giờ bay ít nhất (giờ)", 0),
    ("payment_hold_minutes", "Thời gian giữ chỗ chờ thanh toán (phút)", 1),
]


def validate_regulation(values, active_airports):
    """Trả về (errors, cleaned). `values` là dict {tên cột: giá trị}."""
    errors, cleaned = [], {}
    for key, label, minimum in REG_FIELDS:
        value = parse_int(values.get(key))
        if value is None:
            errors.append(f"{label}: phải là số nguyên.")
        elif value < minimum:
            errors.append(f"{label}: phải từ {minimum} trở lên.")
        elif value > 100000:
            errors.append(f"{label}: giá trị quá lớn.")
        else:
            cleaned[key] = value

    if not errors:
        if cleaned["max_stop_minutes"] < cleaned["min_stop_minutes"]:
            errors.append("Thời gian dừng tối đa phải lớn hơn hoặc bằng thời gian dừng tối thiểu.")
        if cleaned["max_airports"] < active_airports:
            errors.append(f"Hiện có {active_airports} sân bay đang hoạt động, "
                          "số sân bay tối đa không được nhỏ hơn con số này.")
        if cleaned["max_intermediate_airports"] > cleaned["max_airports"] - 2:
            errors.append("Số sân bay trung gian tối đa không thể vượt quá (số sân bay - 2).")
    return errors, cleaned


# ----------------------------------------------------------------------
# Báo cáo doanh thu (Yêu cầu 4)
# ----------------------------------------------------------------------
def parse_month(value, today):
    """'2026-11' -> (2026, 11); sai định dạng -> tháng hiện tại."""
    match = re.fullmatch(r"(\d{4})-(\d{2})", (value or "").strip())
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 2000 <= year <= 2100 and 1 <= month <= 12:
            return year, month
    return today.year, today.month


def month_bounds(year, month):
    """[đầu tháng, đầu tháng sau) để lọc theo giờ khởi hành."""
    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start, end


def build_revenue_rows(route_names, flights_by_route, revenue_by_route):
    """Gộp số liệu từng tuyến thành các dòng của BÁO CÁO DOANH THU THEO THÁNG.

    Trả về (rows, total_revenue, total_flights). Mỗi dòng có: stt, route_id, name,
    revenue, flights, share (tỷ lệ % doanh thu của tuyến trên tổng).
    """
    route_ids = set(flights_by_route) | set(revenue_by_route)
    total = int(sum(revenue_by_route.get(r, 0) for r in route_ids))
    rows = []
    for route_id in route_ids:
        revenue = int(revenue_by_route.get(route_id, 0))
        rows.append({
            "route_id": route_id,
            "name": route_names.get(route_id, f"Tuyến #{route_id}"),
            "revenue": revenue,
            "flights": int(flights_by_route.get(route_id, 0)),
            "share": round(revenue * 100 / total, 1) if total else 0.0,
        })
    rows.sort(key=lambda r: (-r["revenue"], r["name"]))
    for stt, row in enumerate(rows, start=1):
        row["stt"] = stt
    return rows, total, sum(r["flights"] for r in rows)
