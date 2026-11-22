from flask_login import UserMixin
from sqlalchemy import (Column, Integer, BigInteger, String, Boolean, DateTime, ForeignKey, Enum,
                        UniqueConstraint, CheckConstraint, Index)
from sqlalchemy.orm import relationship, backref
from flightapp import db, app
from enum import Enum as UserEnum
from datetime import datetime, timedelta


class BaseModel(db.Model):
    __abstract__ = True

    id = Column(Integer, primary_key=True, autoincrement=True)


class UserRoleEnum(UserEnum):
    ADMIN = 1       # quản trị viên
    STAFF = 2       # nhân viên bán vé / lập lịch
    CUSTOMER = 3    # khách hàng đặt vé online


class FlightStatusEnum(UserEnum):
    SCHEDULED = 1   # đã lập lịch
    CANCELLED = 2   # đã hủy
    COMPLETED = 3   # đã bay xong


class TicketStatusEnum(UserEnum):
    PENDING = 1     # đã giữ chỗ, chờ thanh toán (vé online)
    PAID = 2        # đã thanh toán / đã bán
    CANCELLED = 3   # đã hủy
    EXPIRED = 4     # quá hạn thanh toán, đã nhả chỗ


class ChannelEnum(UserEnum):
    ONLINE = 1
    COUNTER = 2


class PaymentStatusEnum(UserEnum):
    SUCCESS = 1
    FAILED = 2


# Người dùng
class User(BaseModel, UserMixin):
    name = Column(String(50), nullable=False)
    username = Column(String(50), nullable=False, unique=True)
    password = Column(String(100), nullable=False)
    user_role = Column(Enum(UserRoleEnum), default=UserRoleEnum.CUSTOMER)
    active = Column(Boolean, default=True)

    @property
    def is_active(self):                    # Flask-Login dùng thuộc tính này
        return bool(self.active)

    @property
    def is_admin(self):
        return self.user_role == UserRoleEnum.ADMIN

    @property
    def is_staff_member(self):              # nhân viên hoặc quản trị viên
        return self.user_role in (UserRoleEnum.STAFF, UserRoleEnum.ADMIN)

    def __str__(self):
        return self.name


# Danh mục: sân bay, hạng vé, quy định
class Airport(BaseModel):
    code = Column(String(3), nullable=False, unique=True)       # mã IATA, ví dụ SGN
    name = Column(String(120), nullable=False)                  # Tân Sơn Nhất
    city = Column(String(80), nullable=False)                   # TP. Hồ Chí Minh
    active = Column(Boolean, default=True)

    def __str__(self):
        return f'{self.code} - {self.city}'


class TicketClass(BaseModel):
    name = Column(String(50), nullable=False, unique=True)
    display_order = Column(Integer, default=0)
    active = Column(Boolean, default=True)

    def __str__(self):
        return self.name


class Regulation(BaseModel):
    max_airports = Column(Integer, nullable=False, default=10)                # số sân bay tối đa
    min_flight_minutes = Column(Integer, nullable=False, default=30)          # thời gian bay tối thiểu
    max_intermediate_airports = Column(Integer, nullable=False, default=2)    # số sân bay trung gian tối đa
    min_stop_minutes = Column(Integer, nullable=False, default=20)            # dừng tối thiểu
    max_stop_minutes = Column(Integer, nullable=False, default=30)            # dừng tối đa
    online_booking_min_hours = Column(Integer, nullable=False, default=12)    # đặt online: trước giờ bay >= N giờ
    counter_sale_min_hours = Column(Integer, nullable=False, default=4)       # bán quầy: trước giờ bay >= N giờ
    payment_hold_minutes = Column(Integer, nullable=False, default=15)        # thời gian giữ chỗ chờ thanh toán

    def __str__(self):
        return 'Quy định hiện hành'


# Tuyến bay và bảng đơn giá
class Route(BaseModel):
    __table_args__ = (UniqueConstraint('origin_id', 'destination_id', name='uq_route_airports'),
                      CheckConstraint('origin_id <> destination_id', name='ck_route_different'))

    origin_id = Column(Integer, ForeignKey(Airport.id), nullable=False)
    destination_id = Column(Integer, ForeignKey(Airport.id), nullable=False)
    active = Column(Boolean, default=True)
    origin = relationship(Airport, foreign_keys=[origin_id])
    destination = relationship(Airport, foreign_keys=[destination_id])

    @property
    def name(self):
        return f'{self.origin.code} → {self.destination.code}'

    def __str__(self):
        return self.name


class Fare(BaseModel):
    __table_args__ = (UniqueConstraint('route_id', 'class_id', name='uq_fare_route_class'),)

    route_id = Column(Integer, ForeignKey(Route.id), nullable=False)
    class_id = Column(Integer, ForeignKey(TicketClass.id), nullable=False)
    price = Column(BigInteger, nullable=False, default=0)                      # VND
    route = relationship(Route, backref=backref('fares', cascade='all, delete-orphan', lazy=True))
    ticket_class = relationship(TicketClass)


# Chuyến bay
class Flight(BaseModel):
    code = Column(String(20), nullable=False, unique=True)                     # Mã chuyến bay
    route_id = Column(Integer, ForeignKey(Route.id), nullable=False)
    departure_time = Column(DateTime, nullable=False, index=True)              # Ngày - giờ
    duration_minutes = Column(Integer, nullable=False)                         # Thời gian bay
    status = Column(Enum(FlightStatusEnum), default=FlightStatusEnum.SCHEDULED)
    created_by = Column(Integer, ForeignKey(User.id), nullable=True)
    created_date = Column(DateTime, default=datetime.now)
    route = relationship(Route)
    seats = relationship('FlightSeat', backref='flight', cascade='all, delete-orphan', lazy=True)
    stopovers = relationship('Stopover', backref='flight', cascade='all, delete-orphan', lazy=True,
                             order_by='Stopover.stop_order')
    tickets = relationship('Ticket', backref='flight', lazy=True)

    @property
    def arrival_time(self):
        return self.departure_time + timedelta(minutes=self.duration_minutes)

    def __str__(self):
        return self.code


class FlightSeat(BaseModel):
    __table_args__ = (UniqueConstraint('flight_id', 'class_id', name='uq_flight_class'),)

    flight_id = Column(Integer, ForeignKey(Flight.id), nullable=False)
    class_id = Column(Integer, ForeignKey(TicketClass.id), nullable=False)
    total_seats = Column(Integer, nullable=False, default=0)
    ticket_class = relationship(TicketClass)


class Stopover(BaseModel):
    __table_args__ = (UniqueConstraint('flight_id', 'stop_order', name='uq_stopover_order'),)

    flight_id = Column(Integer, ForeignKey(Flight.id), nullable=False)
    stop_order = Column(Integer, nullable=False)
    airport_id = Column(Integer, ForeignKey(Airport.id), nullable=False)
    stop_minutes = Column(Integer, nullable=False)
    note = Column(String(255), nullable=True)
    airport = relationship(Airport)


# Vé và thanh toán
class Ticket(BaseModel):
    __table_args__ = (Index('ix_ticket_seat_count', 'flight_id', 'class_id', 'status'),)

    code = Column(String(20), nullable=False, unique=True)                     # Mã vé
    flight_id = Column(Integer, ForeignKey(Flight.id), nullable=False)
    class_id = Column(Integer, ForeignKey(TicketClass.id), nullable=False)
    # Các thông tin trên biểu mẫu VÉ CHUYẾN BAY
    passenger_name = Column(String(100), nullable=False)                       # Hành khách
    id_number = Column(String(20), nullable=False)                             # CMND/CCCD
    phone = Column(String(15), nullable=False)                                 # Điện thoại
    price = Column(BigInteger, nullable=False)                                 # Giá tiền tại thời điểm bán
    channel = Column(Enum(ChannelEnum), nullable=False)
    customer_id = Column(Integer, ForeignKey(User.id), nullable=True)          # khách đặt online
    staff_id = Column(Integer, ForeignKey(User.id), nullable=True)             # nhân viên bán tại quầy
    status = Column(Enum(TicketStatusEnum), default=TicketStatusEnum.PENDING)
    hold_expires_at = Column(DateTime, nullable=True)                          # hạn giữ chỗ chờ thanh toán
    created_date = Column(DateTime, default=datetime.now)
    paid_date = Column(DateTime, nullable=True)
    ticket_class = relationship(TicketClass)
    customer = relationship(User, foreign_keys=[customer_id])
    staff = relationship(User, foreign_keys=[staff_id])


class Payment(BaseModel):
    ticket_id = Column(Integer, ForeignKey(Ticket.id), nullable=False)
    amount = Column(BigInteger, nullable=False)
    method = Column(String(30), nullable=False)                                # vnpay / momo / atm / card / cash
    status = Column(Enum(PaymentStatusEnum), default=PaymentStatusEnum.SUCCESS)
    transaction_code = Column(String(50), unique=True, nullable=True)
    created_date = Column(DateTime, default=datetime.now)
    paid_date = Column(DateTime, nullable=True)
    ticket = relationship(Ticket, backref=backref('payments', lazy=True))


AIRPORTS = [
    ('SGN', 'Tân Sơn Nhất', 'TP. Hồ Chí Minh'), ('HAN', 'Nội Bài', 'Hà Nội'),
    ('DAD', 'Đà Nẵng', 'Đà Nẵng'), ('CXR', 'Cam Ranh', 'Nha Trang'),
    ('PQC', 'Phú Quốc', 'Phú Quốc'), ('HPH', 'Cát Bi', 'Hải Phòng'),
    ('VII', 'Vinh', 'Vinh'), ('HUI', 'Phú Bài', 'Huế'),
    ('VCA', 'Cần Thơ', 'Cần Thơ'), ('DLI', 'Liên Khương', 'Đà Lạt'),
]
# (sân bay A, sân bay B, giá hạng 1, giá hạng 2, thời gian bay phút) - tạo cả 2 chiều
ROUTE_DATA = [
    ('SGN', 'HAN', 3_200_000, 1_800_000, 130), ('SGN', 'DAD', 2_400_000, 1_300_000, 85),
    ('SGN', 'CXR', 1_900_000, 1_000_000, 60), ('SGN', 'PQC', 1_800_000, 950_000, 65),
    ('HAN', 'DAD', 2_200_000, 1_200_000, 80),
]
DEMO_NAMES = ['Nguyễn Văn An', 'Trần Thị Bình', 'Lê Hoàng Cường', 'Phạm Thu Dung', 'Hoàng Minh Đức',
              'Vũ Thị Giang', 'Đặng Quốc Huy', 'Bùi Thanh Hà', 'Ngô Gia Khánh', 'Dương Mai Linh']


def seed_data(demo=True):
    import hashlib
    import random

    def md5(text):
        return hashlib.md5(text.encode('utf-8')).hexdigest()

    db.session.add(Regulation(id=1))
    c1, c2 = TicketClass(name='Hạng 1', display_order=1), TicketClass(name='Hạng 2', display_order=2)
    db.session.add_all([c1, c2])
    airports = {code: Airport(code=code, name=name, city=city) for code, name, city in AIRPORTS}
    db.session.add_all(airports.values())

    staff = User(name='Nhân Viên Quầy', username='staff', password=md5('123456'), user_role=UserRoleEnum.STAFF)
    customer = User(name='Khách Hàng Mẫu', username='customer', password=md5('123456'),
                    user_role=UserRoleEnum.CUSTOMER)
    db.session.add_all([User(name='Quản Trị Viên', username='admin', password=md5('123456'),
                             user_role=UserRoleEnum.ADMIN), staff, customer])
    db.session.flush()

    routes = []
    for a, b, price1, price2, minutes in ROUTE_DATA:
        for origin, dest in ((a, b), (b, a)):
            r = Route(origin=airports[origin], destination=airports[dest])
            r.fares = [Fare(ticket_class=c1, price=price1), Fare(ticket_class=c2, price=price2)]
            db.session.add(r)
            routes.append((r, minutes, price1, price2))
    db.session.flush()

    if demo:
        rnd = random.Random(2026)
        now = datetime.now().replace(minute=0, second=0, microsecond=0)
        ticket_no = 0
        # ngày lệch so với hôm nay: số âm = đã bay (có doanh thu), số dương = sắp bay (còn bán được)
        for idx, (route, minutes, price1, price2) in enumerate(routes, start=1):
            prices = {c1.id: price1, c2.id: price2}
            for n, offset in enumerate([-75, -50, -33, -20, -9, -3, 3, 5, 7, 10, 14], start=1):
                dep = (now + timedelta(days=offset)).replace(hour=rnd.choice((6, 9, 12, 15, 18)))
                past = dep < now
                flight = Flight(code=f'SK{idx:02d}{n:02d}', route=route, departure_time=dep,
                                duration_minutes=minutes, created_by=staff.id,
                                status=FlightStatusEnum.COMPLETED if past else FlightStatusEnum.SCHEDULED)
                totals = {c1.id: 10, c2.id: 40}
                flight.seats = [FlightSeat(class_id=cid, total_seats=t) for cid, t in totals.items()]
                if {route.origin.code, route.destination.code} == {'SGN', 'HAN'} and n % 3 == 0:
                    flight.stopovers = [Stopover(stop_order=1, airport=airports['DAD'], stop_minutes=25,
                                                 note='Tiếp nhiên liệu')]
                db.session.add(flight)

                share = rnd.uniform(0.45, 0.95) if past else rnd.uniform(0.0, 0.6)
                for cid, total in totals.items():
                    for _ in range(int(total * share)):
                        ticket_no += 1
                        online = rnd.random() < 0.5
                        paid = dep - timedelta(days=rnd.randint(1, 20), hours=rnd.randint(0, 12))
                        paid = min(paid, now - timedelta(hours=1))
                        db.session.add(Ticket(
                            code=f'TD{ticket_no:06d}', flight=flight, class_id=cid,
                            passenger_name=rnd.choice(DEMO_NAMES),
                            id_number=str(rnd.randint(10 ** 11, 10 ** 12 - 1)),
                            phone='09' + str(rnd.randint(10 ** 7, 10 ** 8 - 1)), price=prices[cid],
                            channel=ChannelEnum.ONLINE if online else ChannelEnum.COUNTER,
                            customer_id=customer.id if online else None,
                            staff_id=None if online else staff.id,
                            status=TicketStatusEnum.PAID, created_date=paid, paid_date=paid))
    db.session.commit()


if __name__ == '__main__':
    import sys

    with app.app_context():
        if 'reset' in sys.argv:
            db.drop_all()
        db.create_all()
        if Regulation.query.count() == 0:
            seed_data(demo='nodemo' not in sys.argv)
            print('Đã tạo bảng và nạp dữ liệu mẫu.')
        else:
            print('CSDL đã có dữ liệu, bỏ qua (dùng tham số reset để làm lại từ đầu).')
