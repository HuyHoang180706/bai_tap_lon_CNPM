import hashlib
import secrets
import string
from collections import namedtuple
from datetime import datetime, timedelta

from sqlalchemy import func, or_, and_
from sqlalchemy.orm import aliased

from flightapp import app, db, utils
from flightapp.models import (User, Airport, TicketClass, Regulation, Route, Fare, Flight, FlightSeat, Stopover,
                              Ticket, Payment, UserRoleEnum, FlightStatusEnum, TicketStatusEnum, ChannelEnum,
                              PaymentStatusEnum)


def load_airports(active_only=True):
    query = Airport.query
    if active_only:
        query = query.filter(Airport.active == True)
    return query.order_by(Airport.code).all()


def load_ticket_classes(active_only=True):
    query = TicketClass.query
    if active_only:
        query = query.filter(TicketClass.active == True)
    return query.order_by(TicketClass.display_order).all()


def get_regulation():
    reg = db.session.get(Regulation, 1)
    if not reg:
        reg = Regulation(id=1)
        db.session.add(reg)
        db.session.commit()
    return reg


def count_active_airports(exclude_id=None):
    query = Airport.query.filter(Airport.active == True)
    if exclude_id:
        query = query.filter(Airport.id != exclude_id)
    return query.count()


def airport_in_use(airport_id):
    return bool(Route.query.filter(or_(Route.origin_id == airport_id, Route.destination_id == airport_id)).first()
                or Stopover.query.filter(Stopover.airport_id == airport_id).first())


def class_in_use(class_id):
    return bool(Ticket.query.filter(Ticket.class_id == class_id).first()
                or FlightSeat.query.filter(FlightSeat.class_id == class_id).first())


def route_has_flights(route_id):
    return bool(Flight.query.filter(Flight.route_id == route_id).first())


def _md5(text):
    return hashlib.md5(text.strip().encode('utf-8')).hexdigest()


def authenticate(username, password):
    return User.query.filter(User.username == username.strip(),
                             User.password == _md5(password),
                             User.active == True).first()


def get_user_by_id(user_id):
    return db.session.get(User, int(user_id))


def get_user_by_username(username):
    return User.query.filter(User.username == username.strip()).first()


def create_user(name, username, password):
    u = User(name=name.strip(), username=username.strip().lower(), password=_md5(password),
             user_role=UserRoleEnum.CUSTOMER)
    db.session.add(u)
    db.session.commit()
    return u


def set_user_role(ids, role, except_id=None):
    query = User.query.filter(User.id.in_([int(i) for i in ids]))
    if except_id:
        query = query.filter(User.id != except_id)
    n = query.update({User.user_role: role}, synchronize_session=False)
    db.session.commit()
    return n


def set_user_active(ids, active, except_id=None):
    query = User.query.filter(User.id.in_([int(i) for i in ids]))
    if except_id:
        query = query.filter(User.id != except_id)
    n = query.update({User.active: active}, synchronize_session=False)
    db.session.commit()
    return n


def get_flight_by_id(flight_id):
    return db.session.get(Flight, flight_id)


def _flight_query(kw=None, origin_id=None, destination_id=None, date_from=None, date_to=None,
                  status=None, departure_from=None):
    query = Flight.query.join(Route, Flight.route_id == Route.id)

    if kw:
        query = query.filter(Flight.code.contains(kw))
    if origin_id:
        query = query.filter(Route.origin_id == origin_id)
    if destination_id:
        query = query.filter(Route.destination_id == destination_id)
    if date_from:
        query = query.filter(Flight.departure_time >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.filter(Flight.departure_time < datetime.combine(date_to + timedelta(days=1),
                                                                      datetime.min.time()))
    if status:
        query = query.filter(Flight.status == status)
    if departure_from:
        query = query.filter(Flight.departure_time >= departure_from)
    return query


def load_flights(page=1, **filters):
    query = _flight_query(**filters).order_by(Flight.departure_time)

    page_size = app.config['PAGE_SIZE']
    start = (page - 1) * page_size

    return query.slice(start, start + page_size).all()


def count_flights(**filters):
    return _flight_query(**filters).count()


def expire_holds(now=None):
    """Vé chờ thanh toán quá hạn thì chuyển 'expired' để nhả chỗ."""
    now = now or datetime.now()
    Ticket.query.filter(Ticket.status == TicketStatusEnum.PENDING, Ticket.hold_expires_at <= now) \
        .update({Ticket.status: TicketStatusEnum.EXPIRED}, synchronize_session=False)
    db.session.commit()


def _occupying(now):
    """Vé đang chiếm ghế: đã thanh toán, hoặc đang giữ chỗ còn hạn."""
    return or_(Ticket.status == TicketStatusEnum.PAID,
               and_(Ticket.status == TicketStatusEnum.PENDING, Ticket.hold_expires_at > now))


def seats_taken(flight_id, class_id, now=None):
    now = now or datetime.now()
    return db.session.query(func.count(Ticket.id)).filter(
        Ticket.flight_id == flight_id, Ticket.class_id == class_id, _occupying(now)).scalar() or 0


ClassAvail = namedtuple('ClassAvail', 'ticket_class total sold remaining price')


def availability(flights, now=None):
    """{flight_id: [ClassAvail, ...]}: số ghế còn lại và đơn giá của từng hạng vé."""
    now = now or datetime.now()
    flights = list(flights)
    if not flights:
        return {}

    taken = {(fid, cid): n for fid, cid, n in db.session.query(
        Ticket.flight_id, Ticket.class_id, func.count(Ticket.id))
        .filter(Ticket.flight_id.in_([f.id for f in flights]), _occupying(now))
        .group_by(Ticket.flight_id, Ticket.class_id)}
    prices = {(fa.route_id, fa.class_id): fa.price
              for fa in Fare.query.filter(Fare.route_id.in_({f.route_id for f in flights}))}

    result = {}
    for flight in flights:
        items = []
        for seat in sorted(flight.seats, key=lambda s: s.ticket_class.display_order):
            n = taken.get((flight.id, seat.class_id), 0)
            items.append(ClassAvail(seat.ticket_class, seat.total_seats, n,
                                    max(seat.total_seats - n, 0), prices.get((flight.route_id, seat.class_id))))
        result[flight.id] = items
    return result


def sellable(items):
    """Chỉ các hạng đang bán và có ghế trên chuyến bay."""
    return [a for a in items if a.ticket_class.active and a.total > 0]


def flight_seat_stats(flight, now=None):
    now = now or datetime.now()
    stats = []
    for seat in sorted(flight.seats, key=lambda s: s.ticket_class.display_order):
        paid = sum(1 for t in flight.tickets if t.class_id == seat.class_id and t.status == TicketStatusEnum.PAID)
        held = sum(1 for t in flight.tickets if t.class_id == seat.class_id
                   and t.status == TicketStatusEnum.PENDING and t.hold_expires_at and t.hold_expires_at > now)
        stats.append({'cls': seat.ticket_class, 'total': seat.total_seats, 'paid': paid, 'held': held,
                      'remaining': max(seat.total_seats - paid - held, 0)})
    return stats


def new_ticket_code():
    alphabet = string.ascii_uppercase + string.digits
    while True:
        code = 'TK' + ''.join(secrets.choice(alphabet) for _ in range(8))
        if not Ticket.query.filter(Ticket.code == code).first():
            return code


def add_ticket(flight_id,
               class_id,
               passenger_name,
               id_number,
               phone,
               channel,
               customer_id=None,
               staff_id=None):
    reg = get_regulation()
    now = datetime.now()
    try:
        seat = (FlightSeat.query.filter(FlightSeat.flight_id == flight_id, FlightSeat.class_id == class_id)
                .with_for_update().first())
        if seat is None or seat.total_seats <= 0 or not seat.ticket_class.active:
            raise utils.SaleError('Chuyến bay không có hạng vé này.')

        flight = get_flight_by_id(flight_id)
        if flight is None or flight.status != FlightStatusEnum.SCHEDULED:
            raise utils.SaleError('Chuyến bay không còn mở bán.')

        online = channel == ChannelEnum.ONLINE
        min_hours = reg.online_booking_min_hours if online else reg.counter_sale_min_hours
        if not utils.is_sale_open(flight.departure_time, now, min_hours):
            where = 'đặt vé online' if online else 'bán vé tại quầy'
            raise utils.SaleError(f'Đã quá thời hạn {where}: chỉ được thực hiện trước giờ khởi hành '
                                  f'ít nhất {min_hours} giờ.')

        if seats_taken(flight_id, class_id, now) >= seat.total_seats:
            raise utils.SaleError('Hạng vé này đã hết chỗ.')

        fare = Fare.query.filter(Fare.route_id == flight.route_id, Fare.class_id == class_id).first()
        if fare is None:
            raise utils.SaleError('Tuyến bay chưa có đơn giá cho hạng vé này.')

        ticket = Ticket(code=new_ticket_code(), flight_id=flight_id, class_id=class_id,
                        passenger_name=passenger_name, id_number=id_number, phone=phone,
                        price=fare.price, channel=channel, customer_id=customer_id, staff_id=staff_id,
                        status=TicketStatusEnum.PENDING if online else TicketStatusEnum.PAID,
                        hold_expires_at=now + timedelta(minutes=reg.payment_hold_minutes) if online else None,
                        paid_date=None if online else now)
        db.session.add(ticket)
        if not online:
            db.session.add(Payment(ticket=ticket, amount=fare.price, method='cash',
                                   status=PaymentStatusEnum.SUCCESS, paid_date=now))
        db.session.commit()
        return ticket
    except Exception:
        db.session.rollback()
        raise


def pay_ticket(ticket_id,
               method,
               success):

    if method not in utils.PAYMENT_METHODS:
        raise utils.SaleError('Phương thức thanh toán không hợp lệ.')
    now = datetime.now()
    try:
        ticket = Ticket.query.filter(Ticket.id == ticket_id).with_for_update().first()
        if ticket is None:
            raise utils.SaleError('Không tìm thấy vé.')
        if ticket.status == TicketStatusEnum.PAID:
            raise utils.SaleError('Vé này đã được thanh toán.')
        if ticket.status != TicketStatusEnum.PENDING or ticket.hold_expires_at <= now:
            if ticket.status == TicketStatusEnum.PENDING:
                ticket.status = TicketStatusEnum.EXPIRED
                db.session.commit()
            raise utils.SaleError('Phiên giữ chỗ đã hết hạn, vui lòng đặt lại vé.')

        payment = Payment(ticket=ticket, amount=ticket.price, method=method,
                          status=PaymentStatusEnum.SUCCESS if success else PaymentStatusEnum.FAILED,
                          transaction_code='PAY' + now.strftime('%y%m%d%H%M%S') + secrets.token_hex(3).upper(),
                          paid_date=now if success else None)
        db.session.add(payment)
        if success:
            ticket.status = TicketStatusEnum.PAID
            ticket.paid_date = now
            ticket.hold_expires_at = None
        db.session.commit()
        return payment
    except Exception:
        db.session.rollback()
        raise


def cancel_pending_ticket(ticket):
    if ticket.status != TicketStatusEnum.PENDING:
        raise utils.SaleError('Chỉ hủy được vé đang chờ thanh toán.')
    ticket.status = TicketStatusEnum.CANCELLED
    db.session.commit()


def get_ticket_by_code(code):
    return Ticket.query.filter(Ticket.code == code).first()


def load_tickets_of_user(user_id):
    return Ticket.query.filter(Ticket.customer_id == user_id).order_by(Ticket.created_date.desc()).all()


def _ticket_query(kw=None):
    query = Ticket.query
    if kw:
        like = f'%{kw}%'
        query = query.filter(or_(Ticket.code.like(like), Ticket.id_number.like(like),
                                 Ticket.phone.like(like), Ticket.passenger_name.like(like)))
    return query


def search_tickets(kw=None, page=1):
    page_size = app.config['PAGE_SIZE']
    start = (page - 1) * page_size
    return _ticket_query(kw).order_by(Ticket.created_date.desc()).slice(start, start + page_size).all()


def count_tickets(kw=None):
    return _ticket_query(kw).count()



def add_flight(data, creator_id):

    reg = get_regulation()
    classes = load_ticket_classes()
    errors = utils.validate_flight(
        code=data['code'], origin_id=data['origin_id'], destination_id=data['destination_id'],
        departure=data['departure'], duration=data['duration'], seats=data['seats'], stops=data['stops'],
        reg=reg, now=datetime.now(), class_names={c.id: c.name for c in classes})

    route = None
    if not errors:
        active_ids = {a.id for a in load_airports()}
        used = [data['origin_id'], data['destination_id']] + [s['airport_id'] for s in data['stops']]
        if any(a not in active_ids for a in used):
            errors.append('Có sân bay không tồn tại hoặc đã ngừng hoạt động.')
        route = Route.query.filter(Route.origin_id == data['origin_id'],
                                   Route.destination_id == data['destination_id']).first()
        if route is None or not route.active:
            errors.append('Tuyến bay này chưa được khai báo hoặc đã ngừng khai thác. '
                          'Quản trị viên cần tạo tuyến bay và đơn giá trước.')
        if Flight.query.filter(Flight.code == data['code']).first():
            errors.append(f"Mã chuyến bay {data['code']} đã tồn tại.")

    if not errors:
        priced = {f.class_id for f in route.fares}
        for c in classes:
            if (data['seats'].get(c.id) or 0) > 0 and c.id not in priced:
                errors.append(f'Tuyến {route.name} chưa có đơn giá cho {c.name}. '
                              'Hãy cập nhật bảng giá vé trước.')
    if errors:
        return None, errors

    flight = Flight(code=data['code'], route_id=route.id, departure_time=data['departure'],
                    duration_minutes=data['duration'], created_by=creator_id)
    flight.seats = [FlightSeat(class_id=cid, total_seats=n) for cid, n in data['seats'].items()]
    flight.stopovers = [Stopover(stop_order=s['order'], airport_id=s['airport_id'],
                                 stop_minutes=s['minutes'], note=s['note'] or None) for s in data['stops']]
    db.session.add(flight)
    db.session.commit()
    return flight, []


def cancel_flights(ids):
    cancelled = paid = 0
    for flight in Flight.query.filter(Flight.id.in_([int(i) for i in ids])).all():
        if flight.status != FlightStatusEnum.SCHEDULED:
            continue
        flight.status = FlightStatusEnum.CANCELLED
        for t in flight.tickets:
            if t.status == TicketStatusEnum.PENDING:
                t.status = TicketStatusEnum.EXPIRED
            elif t.status == TicketStatusEnum.PAID:
                paid += 1
        cancelled += 1
    db.session.commit()
    return cancelled, paid


def count_flights_by_route():
    o, d = aliased(Airport), aliased(Airport)
    rows = (db.session.query(Route.id, o.code, d.code, func.count(Flight.id))
            .select_from(Route)
            .join(o, Route.origin_id == o.id).join(d, Route.destination_id == d.id)
            .join(Flight, Flight.route_id == Route.id, isouter=True)
            .group_by(Route.id, o.code, d.code).order_by(o.code, d.code).all())
    return [(rid, f'{oc} → {dc}', n) for rid, oc, dc, n in rows]


def stats_revenue_by_route(year, month):

    start, end = utils.month_bounds(year, month)
    in_month = and_(Flight.departure_time >= start, Flight.departure_time < end)

    flights_by_route = dict(db.session.query(Flight.route_id, func.count(Flight.id))
                            .filter(in_month, Flight.status != FlightStatusEnum.CANCELLED)
                            .group_by(Flight.route_id).all())
    revenue_by_route = {rid: int(v) for rid, v in
                        db.session.query(Flight.route_id, func.sum(Ticket.price))
                        .select_from(Flight).join(Ticket, Ticket.flight_id == Flight.id)
                        .filter(in_month, Ticket.status == TicketStatusEnum.PAID)
                        .group_by(Flight.route_id).all()}

    route_ids = set(flights_by_route) | set(revenue_by_route)
    names = {}
    if route_ids:
        names = {r.id: r.name for r in Route.query.filter(Route.id.in_(route_ids)).all()}
    return utils.build_revenue_rows(names, flights_by_route, revenue_by_route)


def stats_summary():
    now = datetime.now()
    today = datetime.combine(now.date(), datetime.min.time())
    first_of_month = today.replace(day=1)
    upcoming = Flight.query.filter(Flight.status == FlightStatusEnum.SCHEDULED, Flight.departure_time >= now,
                                   Flight.departure_time < now + timedelta(days=7)).count()
    sold_today = Ticket.query.filter(Ticket.status == TicketStatusEnum.PAID, Ticket.paid_date >= today).count()
    revenue = db.session.query(func.coalesce(func.sum(Ticket.price), 0)).filter(
        Ticket.status == TicketStatusEnum.PAID, Ticket.paid_date >= first_of_month).scalar()
    return {'upcoming_flights': upcoming, 'sold_today': sold_today, 'revenue_month': int(revenue or 0)}
