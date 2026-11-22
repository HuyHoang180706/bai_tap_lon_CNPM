import math
from datetime import datetime, timedelta
from functools import wraps

from flask import render_template, request, redirect, abort, flash
from flask_login import login_user, logout_user, login_required, current_user
from flightapp import app, dao, login, utils
from flightapp.models import ChannelEnum, FlightStatusEnum, TicketStatusEnum


app.add_template_filter(utils.format_vnd, 'vnd')
app.add_template_filter(utils.format_dt, 'dt')
app.add_template_filter(utils.format_duration, 'dur')


def staff_required(f):
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_staff_member:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def get_page():
    return max(utils.parse_int(request.args.get('page'), 1), 1)


def other_args():
    args = request.args.to_dict()
    args.pop('page', None)
    return args


def safe_next(target):
    if target and target.startswith('/') and not target.startswith('//') and '\\' not in target:
        return target
    return None


@app.route('/')
def index():
    dao.expire_holds()
    reg = dao.get_regulation()
    now = datetime.now()

    filters = utils.read_flight_filters(request.args)
    filters.update(status=FlightStatusEnum.SCHEDULED,
                   departure_from=now + timedelta(hours=reg.online_booking_min_hours))

    page = get_page()
    flights = dao.load_flights(page=page, **filters)
    page_size = app.config['PAGE_SIZE']
    return render_template('index.html', flights=flights, page=page, reg=reg, args=other_args(),
                           pages=math.ceil(dao.count_flights(**filters) / page_size),
                           avail=dao.availability(flights, now), sellable=dao.sellable,
                           airports=dao.load_airports())


@app.route('/booking/<int:flight_id>', methods=['get', 'post'])
@login_required
def booking(flight_id):
    dao.expire_holds()
    flight = dao.get_flight_by_id(flight_id)
    if not flight:
        abort(404)

    reg = dao.get_regulation()
    now = datetime.now()
    form, errors = {'passenger_name': current_user.name}, []

    if request.method == 'POST':
        form = request.form
        errors, passenger = utils.clean_passenger(form.get('passenger_name'), form.get('id_number'),
                                                  form.get('phone'))
        class_id = utils.parse_int(form.get('class_id'))
        if class_id is None:
            errors.append('Vui lòng chọn hạng vé.')

        if not errors:
            try:
                t = dao.add_ticket(flight_id=flight.id, class_id=class_id, channel=ChannelEnum.ONLINE,
                                   customer_id=current_user.id, **passenger)
                return redirect(f'/payment/{t.code}')
            except utils.SaleError as ex:
                errors = [str(ex)]

    min_hours = reg.online_booking_min_hours
    return render_template('booking.html', flight=flight, form=form, errors=errors, reg=reg,
                           avail=dao.sellable(dao.availability([flight], now)[flight.id]),
                           is_open=flight.status == FlightStatusEnum.SCHEDULED
                           and utils.is_sale_open(flight.departure_time, now, min_hours),
                           deadline=utils.sale_deadline(flight.departure_time, min_hours))


def own_ticket_or_404(code):
    ticket = dao.get_ticket_by_code(code)
    if not ticket or ticket.customer_id != current_user.id:
        abort(404)
    return ticket


@app.route('/payment/<code>', methods=['get', 'post'])
@login_required
def payment(code):
    ticket = own_ticket_or_404(code)
    if ticket.status == TicketStatusEnum.PAID:
        return redirect(f'/tickets/{ticket.code}')
    if ticket.status != TicketStatusEnum.PENDING or ticket.hold_expires_at <= datetime.now():
        dao.expire_holds()
        flash('Phiên giữ chỗ đã hết hạn hoặc vé đã bị hủy. Vui lòng đặt lại.', 'warning')
        return redirect('/')

    errors = []
    if request.method == 'POST':
        try:
            p = dao.pay_ticket(ticket.id, request.form.get('method'),
                               success=request.form.get('outcome') == 'success')
            if p.status.name == 'SUCCESS':
                flash(f'Thanh toán thành công (mã giao dịch {p.transaction_code}).', 'success')
                return redirect(f'/tickets/{ticket.code}')
            errors = ['Thanh toán không thành công. Chỗ vẫn được giữ, bạn có thể thử lại trước khi hết hạn.']
        except utils.SaleError as ex:
            flash(str(ex), 'danger')
            return redirect('/my-tickets')

    remaining = max(int((ticket.hold_expires_at - datetime.now()).total_seconds()), 0)
    return render_template('payment.html', ticket=ticket, remaining=remaining, errors=errors,
                           methods=utils.PAYMENT_METHODS)


@app.route('/my-tickets')
@login_required
def my_tickets():
    dao.expire_holds()
    return render_template('my_tickets.html', tickets=dao.load_tickets_of_user(current_user.id))


@app.route('/my-tickets/<code>/cancel', methods=['post'])
@login_required
def cancel_ticket(code):
    try:
        dao.cancel_pending_ticket(own_ticket_or_404(code))
        flash('Đã hủy giữ chỗ.', 'success')
    except utils.SaleError as ex:
        flash(str(ex), 'danger')
    return redirect('/my-tickets')


@app.route('/tickets/<code>')
@login_required
def ticket_view(code):
    """Biểu mẫu VÉ CHUYẾN BAY (in được). Khách chỉ xem vé của mình, nhân viên xem mọi vé."""
    ticket = dao.get_ticket_by_code(code)
    if not ticket or (not current_user.is_staff_member and ticket.customer_id != current_user.id):
        abort(404)
    return render_template('ticket.html', ticket=ticket)


@app.route('/login')
def login_view():
    return render_template('login.html')


@app.route('/login', methods=['post'])
def login_process():
    username = request.form.get('username') or ''
    password = request.form.get('password') or ''

    u = dao.authenticate(username=username, password=password)
    if not u:
        return render_template('login.html', err_msg='Sai tên đăng nhập hoặc mật khẩu, hoặc tài khoản đã bị khóa!')

    login_user(user=u)

    next = safe_next(request.args.get('next') or request.form.get('next'))
    if next:
        return redirect(next)
    if u.is_admin:
        return redirect('/admin')
    if u.is_staff_member:
        return redirect('/staff/sell')
    return redirect('/')


@app.route('/logout')
def logout_process():
    logout_user()
    return redirect('/login')


@app.route('/register')
def register_view():
    return render_template('register.html')


@app.route('/register', methods=['post'])
def register_process():
    data = request.form.copy()
    errors = utils.validate_account(data.get('name'), data.get('username'), data.get('password'),
                                    data.get('confirm'))
    if not errors and dao.get_user_by_username(data.get('username')):
        errors.append('Tên đăng nhập này đã có người dùng!')

    if errors:
        return render_template('register.html', errors=errors, form=data)

    dao.create_user(name=data.get('name'), username=data.get('username'), password=data.get('password'))
    return redirect('/login')


@app.route('/staff/sell')
@staff_required
def sell_search():
    """Tra cứu chuyến bay theo mã, tuyến, khoảng ngày bay rồi chọn chuyến để bán vé."""
    dao.expire_holds()
    reg = dao.get_regulation()
    now = datetime.now()

    filters = utils.read_flight_filters(request.args)
    filters.update(status=FlightStatusEnum.SCHEDULED, departure_from=now)       # chỉ chuyến chưa bay

    page = get_page()
    flights = dao.load_flights(page=page, **filters)
    page_size = app.config['PAGE_SIZE']
    return render_template('staff/sell_search.html', flights=flights, page=page, reg=reg, args=other_args(),
                           pages=math.ceil(dao.count_flights(**filters) / page_size),
                           avail=dao.availability(flights, now), sellable=dao.sellable,
                           open_flags={f.id: utils.is_sale_open(f.departure_time, now, reg.counter_sale_min_hours)
                                       for f in flights},
                           deadline=utils.sale_deadline, airports=dao.load_airports(active_only=False),
                           back=request.full_path.rstrip('?'))


@app.route('/staff/sell/<int:flight_id>', methods=['get', 'post'])
@staff_required
def sell(flight_id):
    dao.expire_holds()
    flight = dao.get_flight_by_id(flight_id)
    if not flight:
        abort(404)

    reg = dao.get_regulation()
    now = datetime.now()
    back = safe_next(request.values.get('back')) or '/staff/sell'
    form, errors = {}, []

    if request.method == 'POST':
        form = request.form
        errors, passenger = utils.clean_passenger(form.get('passenger_name'), form.get('id_number'),
                                                  form.get('phone'))
        class_id = utils.parse_int(form.get('class_id'))
        if class_id is None:
            errors.append('Vui lòng chọn hạng vé.')

        if not errors:
            try:
                t = dao.add_ticket(flight_id=flight.id, class_id=class_id, channel=ChannelEnum.COUNTER,
                                   staff_id=current_user.id, **passenger)
                flash('Bán vé thành công. Có thể in vé giao cho khách.', 'success')
                return redirect(f'/tickets/{t.code}')
            except utils.SaleError as ex:
                errors = [str(ex)]

    min_hours = reg.counter_sale_min_hours
    return render_template('staff/sell.html', flight=flight, form=form, errors=errors, reg=reg, back=back,
                           avail=dao.sellable(dao.availability([flight], now)[flight.id]),
                           is_open=flight.status == FlightStatusEnum.SCHEDULED
                           and utils.is_sale_open(flight.departure_time, now, min_hours),
                           deadline=utils.sale_deadline(flight.departure_time, min_hours))


@app.route('/staff/tickets')
@staff_required
def staff_tickets():
    kw = (request.args.get('kw') or '').strip()
    page = get_page()
    return render_template('staff/tickets.html', tickets=dao.search_tickets(kw=kw, page=page), kw=kw, page=page,
                           pages=math.ceil(dao.count_tickets(kw=kw) / app.config['PAGE_SIZE']),
                           args=other_args())


@app.route('/staff/flights/new', methods=['get', 'post'])
@staff_required
def flight_new():
    reg = dao.get_regulation()
    classes = dao.load_ticket_classes()
    form, errors = {}, []

    if request.method == 'POST':
        form = request.form
        data = utils.read_flight_form(form, reg.max_intermediate_airports, [c.id for c in classes])
        flight, errors = dao.add_flight(data, creator_id=current_user.id)
        if flight:
            flash(f'Đã lập lịch chuyến bay {flight.code}.', 'success')
            return redirect(f'/staff/flights/{flight.id}')

    return render_template('staff/flight_form.html', form=form, errors=errors, reg=reg, classes=classes,
                           airports=dao.load_airports(), stop_rows=range(1, reg.max_intermediate_airports + 1))


@app.route('/staff/flights/<int:flight_id>')
@staff_required
def flight_detail(flight_id):
    flight = dao.get_flight_by_id(flight_id)
    if not flight:
        abort(404)
    tickets = sorted(flight.tickets, key=lambda t: t.created_date, reverse=True)
    return render_template('staff/flight_detail.html', flight=flight, tickets=tickets,
                           stats=dao.flight_seat_stats(flight))


@app.errorhandler(403)
def forbidden(_error):
    return render_template('error.html', code=403, title='Không có quyền truy cập',
                           text='Tài khoản của bạn không được phép mở trang này.'), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template('error.html', code=404, title='Không tìm thấy trang',
                           text='Đường dẫn không tồn tại hoặc đã bị xóa.'), 404


@login.user_loader
def get_user(user_id):
    return dao.get_user_by_id(user_id)


if __name__ == '__main__':
    with app.app_context():
        from flightapp import admin
        app.run(debug=True)
